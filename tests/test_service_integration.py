"""Integration-style tests for service and timer generation flows."""

from pathlib import Path
from unittest.mock import patch

import armactl.i18n as i18n
import armactl.service_manager as service_manager


def test_generate_services_writes_expected_units_and_restarts_timer(tmp_path: Path) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    start_script_path = instance_root / "start-armareforger.sh"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    installed_units: dict[Path, str] = {}

    def fake_install(source: Path, destination: Path) -> service_manager.ServiceResult:
        installed_units[destination] = source.read_text(encoding="utf-8")
        return service_manager.ServiceResult(
            True,
            i18n.tr("Installed {name} to {path}", name=destination.name, path=destination.parent),
        )

    with (
        patch("armactl.service_manager.paths.instance_root", return_value=instance_root),
        patch("armactl.service_manager.paths.start_script", return_value=start_script_path),
        patch("armactl.service_manager.paths.SYSTEMD_DIR", systemd_dir),
        patch("armactl.service_manager.install_systemd_unit_file", side_effect=fake_install),
        patch(
            "armactl.service_manager.daemon_reload",
            return_value=service_manager.ServiceResult(True, "ok"),
        ),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=service_manager.ServiceResult(True, "timer restarted"),
        ) as restart_timer_mock,
        patch.dict("os.environ", {"USER": "tester"}, clear=False),
    ):
        results = service_manager.generate_services(
            instance=instance,
            on_calendar=["*-*-* 08:00:00", "*-*-* 20:00:00"],
        )

    service_path = systemd_dir / "armareforger@alpha.service"
    restart_service_path = systemd_dir / "armareforger-restart@alpha.service"
    timer_path = systemd_dir / "armareforger-restart@alpha.timer"
    server_dir = instance_root / "server"
    service_text = installed_units[service_path]
    start_script_text = start_script_path.read_text(encoding="utf-8")

    assert start_script_path.exists()
    assert f'SERVER_DIR="{server_dir}"' in start_script_text
    assert f'CONFIG_FILE="{instance_root / "config" / "config.json"}"' in start_script_text
    assert 'exec "${SERVER_DIR}/ArmaReforgerServer"' in start_script_text
    assert "  -logStats 10000 \\" in start_script_text
    assert start_script_text.index("-logStats 10000") < start_script_text.index("-maxFPS 60")
    assert f"WorkingDirectory={server_dir}" in service_text
    assert f"ExecStart={start_script_path}" in service_text
    assert "CPUAccounting=yes" in service_text
    assert "MemoryAccounting=yes" in service_text
    assert "ExecStart=/usr/bin/systemctl restart armareforger@alpha.service" in installed_units[
        restart_service_path
    ]
    assert "OnCalendar=*-*-* 08:00:00" in installed_units[timer_path]
    assert "OnCalendar=*-*-* 20:00:00" in installed_units[timer_path]
    assert any(
        result.message == i18n.tr("Generated {path}", path=start_script_path)
        for result in results
    )
    assert any(result.message == i18n._("Systemd daemon reloaded") for result in results)
    assert any(
        result.message
        == i18n.tr(
            "Timer {timer_name} restarted to apply schedule",
            timer_name="armareforger-restart@alpha.timer",
        )
        for result in results
    )
    restart_timer_mock.assert_called_once_with("restart", "armareforger-restart@alpha.timer")


def test_update_restart_timer_schedule_without_helper_installs_rendered_timer(
    tmp_path: Path,
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    captured: dict[str, object] = {}

    def fake_install(source: Path, destination: Path) -> service_manager.ServiceResult:
        captured["destination"] = destination
        captured["content"] = source.read_text(encoding="utf-8")
        return service_manager.ServiceResult(
            True,
            i18n.tr("Installed {name} to {path}", name=destination.name, path=destination.parent),
        )

    with (
        patch("armactl.service_manager.paths.SYSTEMD_DIR", systemd_dir),
        patch("armactl.service_manager.has_privileged_systemctl_channel", return_value=False),
        patch("armactl.service_manager.install_systemd_unit_file", side_effect=fake_install),
        patch(
            "armactl.service_manager.daemon_reload",
            return_value=service_manager.ServiceResult(True, "ok"),
        ),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=service_manager.ServiceResult(True, "timer restarted"),
        ) as restart_timer_mock,
    ):
        results = service_manager.update_restart_timer_schedule("alpha", "06:00, 18:00")

    assert captured["destination"] == systemd_dir / "armareforger-restart@alpha.timer"
    assert "OnCalendar=*-*-* 06:00:00" in captured["content"]
    assert "OnCalendar=*-*-* 18:00:00" in captured["content"]
    assert [result.success for result in results] == [True, True, True]
    restart_timer_mock.assert_called_once_with("restart", "armareforger-restart@alpha.timer")


def test_sync_generated_start_script_refreshes_stale_runtime_script(tmp_path: Path) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    server_dir = instance_root / "server"
    config_dir = instance_root / "config"
    config_file = config_dir / "config.json"
    start_script_path = instance_root / "start-armareforger.sh"

    server_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    config_file.write_text("{}", encoding="utf-8")
    start_script_path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ROOT="/old"
SERVER_DIR="${INSTANCE_ROOT}/server"
CONFIG_DIR="${INSTANCE_ROOT}/config"
CONFIG_FILE="${CONFIG_DIR}/config.json"
PROFILE_DIR="${SERVER_DIR}/profile"

exec "${SERVER_DIR}/ArmaReforgerServer" \
  -config "${CONFIG_FILE}" \
  -profile "${PROFILE_DIR}" \
  -logStats 10000 \
  -maxFPS 60
""",
        encoding="utf-8",
    )
    start_script_path.chmod(0o644)

    with (
        patch("armactl.service_manager.paths.server_dir", return_value=server_dir),
        patch("armactl.service_manager.paths.config_dir", return_value=config_dir),
        patch("armactl.service_manager.paths.config_file", return_value=config_file),
        patch("armactl.service_manager.paths.start_script", return_value=start_script_path),
    ):
        result = service_manager.sync_generated_start_script(instance)

    start_script_text = start_script_path.read_text(encoding="utf-8")
    assert result.success is True
    assert result.exit_code == 0
    assert "PROFILE_DIR=" not in start_script_text
    assert f'CONFIG_DIR="{config_dir}"' in start_script_text
    assert '-profile "${CONFIG_DIR}"' in start_script_text
    assert "-logStats 10000" in start_script_text
    assert start_script_path.stat().st_mode & 0o777 == 0o755

