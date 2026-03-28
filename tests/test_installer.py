"""Integration-style tests for the installer orchestration flow."""

from unittest.mock import patch

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
        patch("armactl.installer.download_server"),
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
    enable_service_mock.assert_called_once_with("armareforger.service")
    restart_service_mock.assert_called_once_with("armareforger.service")
    discover_mock.assert_called_once_with(instance="default")


def test_run_install_uses_instance_specific_service_name() -> None:
    with (
        patch("armactl.installer.check_os"),
        patch("armactl.installer.check_sudo"),
        patch("armactl.installer.install_steamcmd"),
        patch("armactl.installer.create_install_dir"),
        patch("armactl.installer.download_server"),
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
