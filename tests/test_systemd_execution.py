"""Tests for the narrow systemctl execution boundary."""

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

from armactl.platform.systemd_execution import (
    ServiceResult,
    build_systemctl_command,
    execute_systemctl_command,
    resolve_systemctl_binary,
)


def test_service_result_keeps_the_compatibility_shape() -> None:
    result = ServiceResult(False, "failed", 7)

    assert result.to_dict() == {
        "success": False,
        "message": "failed",
        "exit_code": 7,
    }


def test_build_systemctl_command_covers_direct_helper_and_sudo_paths() -> None:
    assert build_systemctl_command(
        "clean-timer-state",
        "example.timer",
        use_sudo=False,
        systemctl_binary="/bin/systemctl",
        privileged_helper=None,
        stdin_isatty=True,
    ) == [
        "/bin/systemctl",
        "clean",
        "--what=state",
        "example.timer",
    ]
    assert build_systemctl_command(
        "restart",
        "example.service",
        use_sudo=True,
        systemctl_binary="",
        privileged_helper=Path("/usr/local/libexec/armactl-systemctl-helper"),
        stdin_isatty=False,
    ) == [
        "sudo",
        "-n",
        "/usr/local/libexec/armactl-systemctl-helper",
        "restart",
        "example.service",
    ]
    assert build_systemctl_command(
        "stop",
        "example.service",
        use_sudo=True,
        systemctl_binary="/bin/systemctl",
        privileged_helper=None,
        stdin_isatty=False,
    ) == [
        "sudo",
        "-n",
        "/bin/systemctl",
        "stop",
        "example.service",
    ]


def test_resolve_systemctl_binary_has_a_stable_absolute_fallback() -> None:
    assert resolve_systemctl_binary(which=lambda _name: "/custom/systemctl") == (
        "/custom/systemctl"
    )
    assert resolve_systemctl_binary(which=lambda _name: None) == "/usr/bin/systemctl"


def test_execute_systemctl_command_uses_injected_runner() -> None:
    calls: list[tuple[list[str], int]] = []

    def run(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        calls.append((command, timeout))
        return CompletedProcess(command, 0, stdout="", stderr="")

    command = ["/bin/systemctl", "start", "example.service"]
    result = execute_systemctl_command(
        "start",
        "example.service",
        command=command,
        use_sudo=False,
        timeout_seconds=12,
        run=run,
    )

    assert result.success is True
    assert result.exit_code == 0
    assert calls == [(command, 12)]


def test_execute_systemctl_command_maps_sudo_auth_failure() -> None:
    command = ["sudo", "-n", "/bin/systemctl", "stop", "example.service"]

    def run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            1,
            stdout="",
            stderr="sudo: a password is required",
        )

    result = execute_systemctl_command(
        "stop",
        "example.service",
        command=command,
        use_sudo=True,
        timeout_seconds=30,
        run=run,
        privileged_channel_message=lambda: "configure helper",
    )

    assert result == ServiceResult(False, "configure helper", 1)


def test_execute_systemctl_command_bounds_timeout() -> None:
    def run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        raise TimeoutExpired("systemctl", 8)

    result = execute_systemctl_command(
        "restart",
        "example.service",
        command=["/bin/systemctl", "restart", "example.service"],
        use_sudo=False,
        timeout_seconds=8,
        run=run,
    )

    assert result.success is False
    assert result.exit_code == 1
    assert "timed out after 8s" in result.message
