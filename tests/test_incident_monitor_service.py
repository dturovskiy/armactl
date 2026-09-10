"""Tests for generated incident-monitor systemd units."""

from pathlib import Path
from unittest.mock import patch

from armactl import incident_monitor_service
from armactl.service_manager import ServiceResult


def test_monitor_unit_is_read_only_to_system_except_data_root() -> None:
    project_root = Path(__file__).resolve().parents[1]
    text = incident_monitor_service.render_incident_monitor_service_unit(
        instance="default",
        data_root=Path("/srv/armactl-data"),
        project_root=project_root,
        user="arma",
        home_dir=Path("/home/arma"),
    )

    assert "User=arma" in text
    assert "SupplementaryGroups=systemd-journal" in text
    assert "incidents monitor run --once --scheduled" in text
    assert "ProtectSystem=strict" in text
    assert "ReadWritePaths=/srv/armactl-data" in text
    assert "armareforger.service" in text
    assert "ExecStart=" in text
    assert "systemctl restart" not in text


def test_monitor_timer_runs_frequently_after_completed_cycle() -> None:
    project_root = Path(__file__).resolve().parents[1]
    text = incident_monitor_service.render_incident_monitor_timer_unit(
        instance="default",
        service_name="armactl-incident-monitor.service",
        project_root=project_root,
    )

    assert "OnActiveSec=20s" in text
    assert "OnUnitInactiveSec=15s" in text
    assert "Unit=armactl-incident-monitor.service" in text


def test_monitor_installs_non_restarting_core_dropin() -> None:
    project_root = Path(__file__).resolve().parents[1]
    text = incident_monitor_service.render_incident_core_dropin(project_root=project_root)

    assert text == "[Service]\nLimitCORE=infinity\n"
    assert "Restart=" not in text


def test_monitor_install_places_core_dropin_without_game_action(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    systemd_dir = tmp_path / "systemd"
    installed: dict[Path, str] = {}

    def fake_install(source: Path, destination: Path, *, mode: str = "0644") -> ServiceResult:
        del mode
        installed[destination] = source.read_text(encoding="utf-8")
        return ServiceResult(True, f"installed {destination.name}")

    with (
        patch.object(incident_monitor_service.paths, "SYSTEMD_DIR", systemd_dir),
        patch.object(
            incident_monitor_service,
            "get_systemd_unit_status",
            return_value={"exists": True, "enabled": True},
        ),
        patch.object(
            incident_monitor_service,
            "_runtime_check",
            return_value=ServiceResult(True, "runtime ready"),
        ),
        patch.object(incident_monitor_service, "resolve_linux_user", return_value="arma"),
        patch.object(incident_monitor_service, "_home_directory", return_value=Path("/home/arma")),
        patch.object(
            incident_monitor_service,
            "install_systemd_unit_file",
            side_effect=fake_install,
        ),
        patch.object(
            incident_monitor_service,
            "daemon_reload",
            return_value=ServiceResult(True, "reloaded"),
        ),
        patch.object(incident_monitor_service, "start_service") as start_service,
        patch.object(incident_monitor_service, "stop_service") as stop_service,
    ):
        result = incident_monitor_service.install_incident_monitor_service(
            data_root=tmp_path / "armactl-data",
            project_root=project_root,
        )

    dropin = systemd_dir / "armareforger.service.d" / "20-armactl-incident-core.conf"
    assert result.success is True
    assert result.core_dropin_path == dropin
    assert installed[dropin] == "[Service]\nLimitCORE=infinity\n"
    assert systemd_dir / "armactl-incident-monitor.service" in installed
    assert systemd_dir / "armactl-incident-monitor.timer" in installed
    start_service.assert_not_called()
    stop_service.assert_not_called()
