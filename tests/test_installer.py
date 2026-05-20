"""Integration-style tests for the installer orchestration flow."""

import types
from pathlib import Path
from unittest.mock import patch

import pytest

import armactl.i18n as i18n
import armactl.installer as installer
import armactl.service_manager as service_manager


def test_run_install_orchestrates_default_instance_flow() -> None:
    generate_results = [
        service_manager.ServiceResult(True, "generated service"),
        service_manager.ServiceResult(True, "generated timer"),
    ]
    privileged_results = [
        service_manager.ServiceResult(True, "installed helper"),
    ]

    with (
        patch("armactl.installer.check_os"),
        patch("armactl.installer.check_sudo"),
        patch("armactl.installer.install_steamcmd"),
        patch("armactl.installer.create_install_dir"),
        patch(
            "armactl.installer.check_package_integrity",
            return_value=types.SimpleNamespace(complete=False),
        ),
        patch("armactl.installer.mark_install_started") as mark_install_started_mock,
        patch("armactl.installer.download_server", return_value=iter(())),
        patch("armactl.installer.record_package_manifest") as record_manifest_mock,
        patch("armactl.installer.clear_install_marker") as clear_marker_mock,
        patch("armactl.installer.smoke_check"),
        patch("armactl.installer.generate_default_config"),
        patch("armactl.installer.generate_services", return_value=generate_results),
        patch(
            "armactl.installer.install_privileged_systemctl_channel",
            return_value=privileged_results,
        ),
        patch("armactl.installer.enable_service") as enable_service_mock,
        patch("armactl.installer.restart_service") as restart_service_mock,
        patch("armactl.installer.discover") as discover_mock,
    ):
        messages = list(installer.run_install("default"))

    assert messages == [
        i18n._("Verifying OS requirements..."),
        i18n._("Verifying sudo permissions..."),
        i18n._("Verifying steamcmd..."),
        i18n._("Creating installation directories..."),
        i18n._("Downloading Arma Reforger via steamcmd... (This may take a while)"),
        i18n._("Recording package integrity manifest..."),
        i18n._("Running smoke check..."),
        i18n._("Generating default configuration..."),
        i18n._("Generating systemd services and timers..."),
        i18n.tr("  - {message}", message="generated service"),
        i18n.tr("  - {message}", message="generated timer"),
        i18n._("Installing secure privileged control channel..."),
        i18n.tr("  - {message}", message="installed helper"),
        i18n._("Setting permissions and starting the server..."),
        i18n._("Saving state.json..."),
        i18n._("Installation complete!"),
    ]
    mark_install_started_mock.assert_called_once()
    record_manifest_mock.assert_called_once_with("default")
    clear_marker_mock.assert_called_once()
    enable_service_mock.assert_called_once_with("armareforger.service")
    restart_service_mock.assert_called_once_with("armareforger.service")
    discover_mock.assert_called_once_with(instance="default")


def test_run_install_uses_instance_specific_service_name() -> None:
    with (
        patch("armactl.installer.check_os"),
        patch("armactl.installer.check_sudo"),
        patch("armactl.installer.install_steamcmd"),
        patch("armactl.installer.create_install_dir"),
        patch(
            "armactl.installer.check_package_integrity",
            return_value=types.SimpleNamespace(complete=False),
        ),
        patch("armactl.installer.mark_install_started"),
        patch("armactl.installer.download_server", return_value=iter(())),
        patch("armactl.installer.record_package_manifest"),
        patch("armactl.installer.clear_install_marker"),
        patch("armactl.installer.smoke_check"),
        patch("armactl.installer.generate_default_config"),
        patch(
            "armactl.installer.generate_services",
            return_value=[service_manager.ServiceResult(True, "generated service")],
        ),
        patch(
            "armactl.installer.install_privileged_systemctl_channel",
            return_value=[service_manager.ServiceResult(True, "installed helper")],
        ),
        patch("armactl.installer.enable_service") as enable_service_mock,
        patch("armactl.installer.restart_service") as restart_service_mock,
        patch("armactl.installer.discover"),
    ):
        list(installer.run_install("alpha"))

    enable_service_mock.assert_called_once_with("armareforger@alpha.service")
    restart_service_mock.assert_called_once_with("armareforger@alpha.service")


def test_download_server_includes_steamcmd_details_in_error() -> None:
    class FakeProc:
        def __init__(self) -> None:
            self.stdout = iter(
                ["ERROR! Failed to install app '1874900' (No subscription)\n"]
            )

        def wait(self) -> int:
            return 7

    with (
        patch("armactl.installer.paths.server_dir", return_value=Path("/tmp/server")),
        patch("armactl.installer._resolve_steamcmd_binary", return_value="/usr/games/steamcmd"),
        patch("armactl.installer.subprocess.Popen", return_value=FakeProc()),
    ):
        try:
            list(installer.download_server("default"))
        except installer.InstallError as error:
            message = str(error)
        else:
            raise AssertionError("download_server() should raise InstallError")

    assert i18n._("Failed to download server via steamcmd") in message
    assert "ERROR! Failed to install app '1874900' (No subscription)" in message


def test_download_server_streams_steamcmd_output_lines() -> None:
    class FakeProc:
        def __init__(self) -> None:
            self.stdout = iter(
                [
                    "Connecting anonymously to Steam Public...OK\n",
                    "\n",
                    "Success! App '1874900' fully installed.\n",
                ]
            )

        def wait(self) -> int:
            return 0

    with (
        patch("armactl.installer.paths.server_dir", return_value=Path("/tmp/server")),
        patch("armactl.installer._resolve_steamcmd_binary", return_value="/usr/games/steamcmd"),
        patch("armactl.installer.subprocess.Popen", return_value=FakeProc()),
    ):
        lines = list(installer.download_server("default"))

    assert lines == [
        "SteamCMD server download attempt 1/3...",
        "Connecting anonymously to Steam Public...OK",
        "Success! App '1874900' fully installed.",
    ]


def test_installer_refuses_project_root_as_install_dir() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    with pytest.raises(installer.InstallError, match="project root"):
        installer.build_steamcmd_update_command(repo_root)


def test_installer_refuses_install_dir_inside_git_working_tree(tmp_path: Path) -> None:
    git_root = tmp_path / "checkout"
    install_dir = git_root / "server"
    (git_root / ".git").mkdir(parents=True)

    with pytest.raises(installer.InstallError, match="Git working tree"):
        installer.build_steamcmd_update_command(install_dir)


def test_download_server_retries_transient_steamcmd_failure() -> None:
    class FakeProc:
        def __init__(self, lines: list[str], return_code: int) -> None:
            self.stdout = iter(lines)
            self._return_code = return_code

        def wait(self) -> int:
            return self._return_code

    procs = [
        FakeProc(
            ["ERROR! Failed to install app '1874900' (Missing configuration)\n"],
            7,
        ),
        FakeProc(["Success! App '1874900' fully installed.\n"], 0),
    ]

    with (
        patch("armactl.installer.paths.server_dir", return_value=Path("/tmp/server")),
        patch(
            "armactl.installer._resolve_steamcmd_binary",
            return_value="/usr/games/steamcmd",
        ),
        patch("armactl.installer.subprocess.Popen", side_effect=procs) as popen_mock,
        patch("armactl.installer.time.sleep") as sleep_mock,
    ):
        lines = list(installer.download_server("default"))

    assert popen_mock.call_count == 2
    sleep_mock.assert_called_once_with(10.0)
    assert "SteamCMD server download attempt 1/3..." in lines
    assert "SteamCMD download failed; retrying in 10s..." in lines
    assert "SteamCMD server download attempt 2/3..." in lines
    assert "Success! App '1874900' fully installed." in lines


def test_stream_server_update_raises_after_retry_attempts() -> None:
    class FakeProc:
        def __init__(self) -> None:
            self.stdout = iter(
                ["ERROR! Failed to install app '1874900' (Missing configuration)\n"]
            )

        def wait(self) -> int:
            return 7

    with (
        patch(
            "armactl.installer._resolve_steamcmd_binary",
            return_value="/usr/games/steamcmd",
        ),
        patch(
            "armactl.installer.subprocess.Popen",
            side_effect=[FakeProc(), FakeProc()],
        ) as popen_mock,
        patch("armactl.installer.time.sleep") as sleep_mock,
    ):
        with pytest.raises(installer.InstallError) as exc_info:
            list(
                installer.stream_server_update(
                    Path("/tmp/server"),
                    max_attempts=2,
                    retry_delays=(0.0,),
                )
            )

    assert popen_mock.call_count == 2
    sleep_mock.assert_not_called()
    assert "Missing configuration" in str(exc_info.value)


def test_download_server_does_not_retry_permanent_steamcmd_error() -> None:
    class FakeProc:
        def __init__(self) -> None:
            self.stdout = iter(
                ["ERROR! Failed to install app '1874900' (No subscription)\n"]
            )

        def wait(self) -> int:
            return 7

    with (
        patch("armactl.installer.paths.server_dir", return_value=Path("/tmp/server")),
        patch(
            "armactl.installer._resolve_steamcmd_binary",
            return_value="/usr/games/steamcmd",
        ),
        patch(
            "armactl.installer.subprocess.Popen",
            return_value=FakeProc(),
        ) as popen_mock,
    ):
        with pytest.raises(installer.InstallError) as exc_info:
            list(installer.download_server("default"))

    popen_mock.assert_called_once()
    assert "No subscription" in str(exc_info.value)
