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

    assert start_script_path.exists()
    assert str(instance_root) in start_script_path.read_text(encoding="utf-8")
    assert "CPUAccounting=yes" in installed_units[service_path]
    assert "MemoryAccounting=yes" in installed_units[service_path]
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
