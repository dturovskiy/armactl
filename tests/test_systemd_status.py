"""Tests for the narrow read-only systemd status boundary."""

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

from armactl.platform.systemd_status import (
    get_service_status,
    get_systemd_unit_status,
    is_active,
    is_enabled,
    parse_systemctl_show,
)


def test_parse_systemctl_show_keeps_value_equals_and_trims_whitespace() -> None:
    assert parse_systemctl_show("Key = value=part \nIgnored\nEmpty=\n") == {
        "Key ": "value=part",
        "Empty": "",
    }


def test_state_probes_use_injected_runner_and_fail_closed() -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        calls.append(command)
        state = "active\n" if command[1] == "is-active" else "enabled\n"
        return CompletedProcess(command, 0, stdout=state, stderr="")

    assert is_active("example.service", run=run) is True
    assert is_enabled("example.service", run=run) is True
    assert calls == [
        ["systemctl", "is-active", "example.service"],
        ["systemctl", "is-enabled", "example.service"],
    ]

    def timeout(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        raise TimeoutExpired("systemctl", 5)

    assert is_active("example.service", run=timeout) is False
    assert is_enabled("example.service", run=timeout) is False


def test_get_systemd_unit_status_parses_generated_unit_state(tmp_path: Path) -> None:
    unit_path = tmp_path / "example.timer"
    unit_path.write_text("[Timer]\nOnUnitInactiveSec=15s\n", encoding="utf-8")

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            0,
            stdout=(
                "LoadState=loaded\n"
                "ActiveState=failed\n"
                "SubState=failed\n"
                "UnitFileState=enabled-runtime\n"
                "Result=exit-code\n"
                "ExecMainCode=2\n"
                "ExecMainStatus=11\n"
                "NextElapseUSecRealtime=n/a\n"
                "NextElapseUSecMonotonic=12s\n"
            ),
            stderr="",
        )

    status = get_systemd_unit_status(
        unit_path.name,
        unit_path=unit_path,
        run=run,
    )

    assert status["exists"] is True
    assert status["active"] is False
    assert status["enabled"] is True
    assert status["failed"] is True
    assert status["exec_main_code"] == "killed"
    assert status["exec_main_status"] == 11
    assert status["next_trigger"] == ""
    assert status["next_trigger_kind"] == "monotonic"


def test_get_service_status_uses_probes_and_bounded_numeric_fields() -> None:
    probes: list[tuple[str, str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            0,
            stdout=(
                "ActiveState=active\n"
                "SubState=running\n"
                "Description=Example\n"
                "User=server\n"
                "MainPID=0\n"
                "ExecMainPID=42\n"
                "ControlPID=43\n"
                "MemoryCurrent=9223372036854775808\n"
                "CPUUsageNSec=-1\n"
                "ExecMainStartTimestampMonotonic=0\n"
                "ActiveEnterTimestampMonotonic=7\n"
                "NRestarts=-2\n"
                "Result=n/a\n"
                "ExecMainCode=n/a\n"
                "ExecMainStatus=invalid\n"
            ),
            stderr="",
        )

    def active_probe(name: str) -> bool:
        probes.append(("active", name))
        return True

    def enabled_probe(name: str) -> bool:
        probes.append(("enabled", name))
        return False

    status = get_service_status(
        "example.service",
        run=run,
        active_probe=active_probe,
        enabled_probe=enabled_probe,
    )

    assert probes == [
        ("active", "example.service"),
        ("enabled", "example.service"),
    ]
    assert status["active"] is True
    assert status["enabled"] is False
    assert status["main_pid"] == 42
    assert status["main_pid_raw"] == 0
    assert status["memory_current_bytes"] is None
    assert status["cpu_usage_nsec"] is None
    assert status["exec_main_start_usec"] is None
    assert status["active_enter_usec"] == 7
    assert status["n_restarts"] == 0
    assert status["result"] == ""
    assert status["exec_main_code"] == ""
    assert status["exec_main_status"] is None
