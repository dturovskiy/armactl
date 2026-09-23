"""Tests for the narrow privileged systemd boundary."""

from __future__ import annotations

import sys
from pathlib import Path
from subprocess import CompletedProcess

from armactl.platform.systemd_execution import ServiceResult
from armactl.platform.systemd_privileged import (
    get_privileged_channel_user,
    has_privileged_channel,
    install_privileged_channel,
    install_root_owned_file,
    resolve_helper_python_binary,
    resolve_install_binary,
    update_timer_with_helper,
)


def test_binary_resolution_and_channel_detection_have_stable_fallbacks(
    tmp_path: Path,
) -> None:
    assert resolve_install_binary(which=lambda _name: None) == "/usr/bin/install"
    assert (
        resolve_helper_python_binary(
            which=lambda _name: None,
            current_executable="/custom/python",
        )
        == "/custom/python"
    )

    helper = tmp_path / "libexec" / "helper"
    sudoers = tmp_path / "sudoers.d" / "helper"
    helper.parent.mkdir()
    sudoers.parent.mkdir()
    assert has_privileged_channel(helper, sudoers) is False
    helper.write_text("helper", encoding="utf-8")
    sudoers.write_text("rule", encoding="utf-8")
    assert has_privileged_channel(helper, sudoers) is True


def test_privileged_channel_user_is_read_from_non_comment_rule(tmp_path: Path) -> None:
    sudoers = tmp_path / "helper.sudoers"
    sudoers.write_text(
        "# generated\noperator ALL=(root) NOPASSWD: /usr/local/libexec/helper *\n",
        encoding="utf-8",
    )

    assert get_privileged_channel_user(sudoers) == "operator"
    assert get_privileged_channel_user(tmp_path / "missing") is None


def test_root_owned_install_uses_explicit_mode_and_maps_sudo_auth_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = Path("/etc/systemd/system/example.service")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(
            command,
            1,
            stdout="",
            stderr="sudo: a password is required",
        )

    result = install_root_owned_file(
        source,
        destination,
        mode="0640",
        install_binary="/bin/install",
        run=run,
        privileged_channel_message=lambda: "refresh helper",
    )

    assert result == ServiceResult(False, "refresh helper", 1)
    assert calls == [
        [
            "sudo",
            "/bin/install",
            "-D",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0640",
            str(source),
            str(destination),
        ]
    ]


def test_privileged_channel_validates_then_installs_both_files(tmp_path: Path) -> None:
    helper_path = tmp_path / "root" / "helper"
    sudoers_path = tmp_path / "root" / "helper.sudoers"
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="", stderr="")

    results = install_privileged_channel(
        helper_text="print('ok')\n",
        sudoers_text="operator ALL=(root) NOPASSWD: /helper *\n",
        helper_name="helper",
        helper_path=helper_path,
        sudoers_path=sudoers_path,
        python_binary=sys.executable,
        visudo_binary=str(tmp_path / "missing-visudo"),
        install_binary="/bin/install",
        run=run,
    )

    assert [result.success for result in results] == [True, True]
    assert calls[0][0:3] == [sys.executable, "-m", "py_compile"]
    install_calls = [command for command in calls if command[0] == "sudo"]
    assert [command[8] for command in install_calls] == ["0755", "0440"]
    assert [command[-1] for command in install_calls] == [
        str(helper_path),
        str(sudoers_path),
    ]


def test_privileged_channel_stops_before_install_on_validation_failure(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 2, stdout="", stderr="invalid helper")

    results = install_privileged_channel(
        helper_text="broken",
        sudoers_text="rule",
        helper_name="helper",
        helper_path=tmp_path / "root-helper",
        sudoers_path=tmp_path / "root-sudoers",
        python_binary=sys.executable,
        visudo_binary=str(tmp_path / "missing-visudo"),
        install_binary="/bin/install",
        run=run,
    )

    assert len(results) == 1
    assert results[0].success is False
    assert "invalid helper" in results[0].message
    assert len(calls) == 1


def test_timer_update_uses_only_the_narrow_helper(tmp_path: Path) -> None:
    helper = tmp_path / "helper"
    command_seen: list[str] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        command_seen.extend(command)
        return CompletedProcess(command, 0, stdout="", stderr="")

    result = update_timer_with_helper(
        helper_path=helper,
        timer_name="armareforger-restart@alpha.timer",
        schedule_entries=["*-*-* 06:00:00", "*-*-* 18:00:00"],
        timer_directory=Path("/etc/systemd/system"),
        run=run,
    )

    assert result.success is True
    assert command_seen == [
        "sudo",
        "-n",
        str(helper),
        "update-timer",
        "armareforger-restart@alpha.timer",
        "*-*-* 06:00:00",
        "*-*-* 18:00:00",
    ]


def test_timer_update_maps_sudo_auth_error_without_exposing_stderr(
    tmp_path: Path,
) -> None:
    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            1,
            stdout="",
            stderr="sudo: a password is required",
        )

    result = update_timer_with_helper(
        helper_path=tmp_path / "helper",
        timer_name="armareforger-restart.timer",
        schedule_entries=["*-*-* 06:00:00"],
        timer_directory=Path("/etc/systemd/system"),
        run=run,
        privileged_channel_message=lambda: "refresh helper",
    )

    assert result == ServiceResult(False, "refresh helper", 1)
