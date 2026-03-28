"""Tests for service and timer helpers."""

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from armactl import paths
from armactl.i18n import _
from armactl.service_manager import (
    _build_systemctl_command,
    _render_privileged_helper_script,
    _run_systemctl,
    format_schedule_for_input,
    get_timer_status,
    has_privileged_systemctl_channel,
    normalize_on_calendar,
    normalize_on_calendar_entries,
    service_unit_name,
    timer_unit_name,
    update_restart_timer_schedule,
)


def test_normalize_on_calendar_accepts_time_only() -> None:
    """Short HH:MM input should expand to a full OnCalendar expression."""
    assert normalize_on_calendar("8:00") == "*-*-* 08:00:00"
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


def test_has_privileged_systemctl_channel_requires_helper_and_sudoers(tmp_path: Path) -> None:
    helper_path = tmp_path / "armactl-systemctl-helper"
    sudoers_path = tmp_path / "armactl-systemctl-helper.sudoers"

    with (
        patch("armactl.service_manager.paths.privileged_helper_file", return_value=helper_path),
        patch("armactl.service_manager.paths.privileged_sudoers_file", return_value=sudoers_path),
    ):
        assert has_privileged_systemctl_channel() is False
        helper_path.write_text("helper", encoding="utf-8")
        assert has_privileged_systemctl_channel() is False
        sudoers_path.write_text("sudoers", encoding="utf-8")
        assert has_privileged_systemctl_channel() is True


def test_render_privileged_helper_script_uses_python_and_lf_newlines() -> None:
    rendered = _render_privileged_helper_script()

    assert rendered.startswith("#!/usr/bin/env python3\n")
    assert "\r" not in rendered
    assert "def main(argv: list[str]) -> int:" in rendered


def test_build_systemctl_command_prefers_secure_helper_channel() -> None:
    helper_path = Path("/usr/local/libexec/armactl-systemctl-helper")

    with (
        patch("armactl.service_manager.has_privileged_systemctl_channel", return_value=True),
        patch("armactl.service_manager.paths.privileged_helper_file", return_value=helper_path),
        patch(
            "armactl.service_manager._resolve_helper_python_binary",
            return_value="/usr/bin/python3",
        ),
    ):
        command = _build_systemctl_command("restart", "armareforger.service")

    assert command == [
        "sudo",
        "-n",
        "/usr/bin/python3",
        str(helper_path),
        "restart",
        "armareforger.service",
    ]


def test_build_systemctl_command_uses_noninteractive_sudo_without_tty() -> None:
    with (
        patch("armactl.service_manager.has_privileged_systemctl_channel", return_value=False),
        patch("armactl.service_manager.sys.stdin.isatty", return_value=False),
        patch(
            "armactl.service_manager._resolve_systemctl_binary",
            return_value="/usr/bin/systemctl",
        ),
    ):
        command = _build_systemctl_command("stop", "armareforger.service")

    assert command == [
        "sudo",
        "-n",
        "/usr/bin/systemctl",
        "stop",
        "armareforger.service",
    ]


def test_update_restart_timer_schedule_uses_secure_helper_channel() -> None:
    helper_path = Path("/usr/local/libexec/armactl-systemctl-helper")
    update_completed = CompletedProcess(
        args=["sudo", "-n", "/usr/bin/python3", str(helper_path), "update-timer"],
        returncode=0,
        stdout="",
        stderr="",
    )
    reload_completed = CompletedProcess(
        args=["sudo", "-n", "/usr/bin/python3", str(helper_path), "daemon-reload"],
        returncode=0,
        stdout="",
        stderr="",
    )
    restart_completed = CompletedProcess(
        args=["sudo", "-n", "/usr/bin/python3", str(helper_path), "restart", paths.TIMER_NAME],
        returncode=0,
        stdout="",
        stderr="",
    )

    with (
        patch("armactl.service_manager.has_privileged_systemctl_channel", return_value=True),
        patch("armactl.service_manager.paths.privileged_helper_file", return_value=helper_path),
        patch(
            "armactl.service_manager._resolve_helper_python_binary",
            return_value="/usr/bin/python3",
        ),
        patch(
            "armactl.service_manager.subprocess.run",
            side_effect=[update_completed, reload_completed, restart_completed],
        ) as run_mock,
    ):
        results = update_restart_timer_schedule("default", ["*-*-* 08:00:00"])

    assert [result.success for result in results] == [True, True, True]
    run_mock.assert_any_call(
        [
            "sudo",
            "-n",
            "/usr/bin/python3",
            str(helper_path),
            "update-timer",
            paths.TIMER_NAME,
            "*-*-* 08:00:00",
        ],
        capture_output=True,
        text=True,
    )


def test_run_systemctl_rewrites_noninteractive_sudo_error() -> None:
    completed = CompletedProcess(
        args=["sudo", "-n", "/usr/bin/systemctl", "stop", "armareforger.service"],
        returncode=1,
        stdout="",
        stderr=(
            "sudo: a terminal is required to read the password; "
            "either use the -S option to read from standard input or configure an askpass helper\n"
            "sudo: a password is required"
        ),
    )

    with patch("armactl.service_manager.subprocess.run", return_value=completed):
        result = _run_systemctl("stop", "armareforger.service")

    assert result.success is False
    assert _(
        "Secure privileged control is not configured yet. "
        "Install/update the bot service or re-run install/repair "
        "from the TUI to install the secure sudo helper."
    ) == result.message
