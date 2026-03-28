"""Tests for service and timer helpers."""

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from armactl import paths
from armactl.service_manager import (
    format_schedule_for_input,
    get_timer_status,
    normalize_on_calendar,
    normalize_on_calendar_entries,
    service_unit_name,
    timer_unit_name,
)


def test_normalize_on_calendar_accepts_time_only() -> None:
    """Short HH:MM input should expand to a full OnCalendar expression."""
    assert normalize_on_calendar("05:30") == "*-*-* 05:30:00"
    assert normalize_on_calendar("05:30:10") == "*-*-* 05:30:10"


def test_normalize_on_calendar_entries_accepts_multiple_times() -> None:
    """Comma-separated times should become multiple OnCalendar entries."""
    assert normalize_on_calendar_entries("05:00, 13:30, 22:00") == [
        "*-*-* 05:00:00",
        "*-*-* 13:30:00",
        "*-*-* 22:00:00",
    ]


def test_normalize_on_calendar_entries_accepts_space_separated_times() -> None:
    """Space-separated times should also become multiple OnCalendar entries."""
    assert normalize_on_calendar_entries("06:00 18:00") == [
        "*-*-* 06:00:00",
        "*-*-* 18:00:00",
    ]


def test_format_schedule_for_input_compacts_daily_times() -> None:
    """Stored daily schedules should render back to user-friendly times."""
    assert format_schedule_for_input(
        ["*-*-* 05:00:00", "*-*-* 13:30:00", "*-*-* 22:00:00"]
    ) == "05:00, 13:30, 22:00"


def test_unit_name_helpers_respect_instances() -> None:
    """Service and timer helpers should derive names consistently."""
    assert service_unit_name() == paths.SERVICE_NAME
    assert timer_unit_name() == paths.TIMER_NAME
    assert service_unit_name("alpha") == "armareforger@alpha.service"
    assert timer_unit_name("alpha") == "armareforger-restart@alpha.timer"


def test_get_timer_status_falls_back_to_timer_file_schedule(tmp_path: Path) -> None:
    """Timer status should read OnCalendar from the unit file when needed."""
    timer_name = "armareforger-restart@test.timer"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / timer_name).write_text(
        "[Timer]\nOnCalendar=*-*-* 05:30:00\nOnCalendar=*-*-* 13:45:00\n",
        encoding="utf-8",
    )
    completed = CompletedProcess(
        args=["systemctl", "show", timer_name],
        returncode=0,
        stdout=(
            "ActiveState=active\n"
            "SubState=waiting\n"
            "Description=Scheduled restart\n"
            "UnitFileState=enabled\n"
            "NextElapseUSecRealtime=Mon 2026-03-30 05:30:00 UTC\n"
            "LastTriggerUSec=n/a\n"
            "TimersCalendar=\n"
        ),
        stderr="",
    )

    with (
        patch("armactl.service_manager.paths.SYSTEMD_DIR", systemd_dir),
        patch("armactl.service_manager.subprocess.run", return_value=completed),
    ):
        status = get_timer_status(timer_name)

    assert status["exists"] is True
    assert status["active"] is True
    assert status["enabled"] is True
    assert status["schedule_entries"] == ["*-*-* 05:30:00", "*-*-* 13:45:00"]
    assert status["schedule"] == "05:30, 13:45"
    assert status["next_run"] == "Mon 2026-03-30 05:30:00 UTC"
