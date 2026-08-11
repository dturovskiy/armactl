"""Integration-style tests for service and timer generation flows."""

from pathlib import Path
from unittest.mock import patch

import armactl.i18n as i18n
import armactl.service_manager as service_manager
from armactl.restart_timing import RESTART_TIMING
from armactl.runtime_settings import RuntimeSettingsError, normalize_max_fps_profile


def test_generate_services_writes_expected_units_and_restarts_timer(tmp_path: Path) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    start_script_path = instance_root / "start-armareforger.sh"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    installed_units: dict[Path, str] = {}
    installed_modes: dict[Path, str] = {}

    def fake_install(
        source: Path,
        destination: Path,
        *,
        mode: str = "0644",
    ) -> service_manager.ServiceResult:
        installed_units[destination] = source.read_text(encoding="utf-8")
        installed_modes[destination] = mode
        return service_manager.ServiceResult(
            True,
            i18n.tr("Installed {name} to {path}", name=destination.name, path=destination.parent),
        )

    with (
        patch("armactl.service_manager.paths.instance_root", return_value=instance_root),
        patch("armactl.service_manager.paths.start_script", return_value=start_script_path),
        patch("armactl.service_manager.paths.SYSTEMD_DIR", systemd_dir),
        patch("armactl.service_manager.paths._containing_git_marker", return_value=None),
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
    helper_path = service_manager.paths.safe_restart_helper_file()
    server_dir = instance_root / "server"
    service_text = installed_units[service_path]
    restart_service_text = installed_units[restart_service_path]
    helper_text = installed_units[helper_path]
    start_script_text = start_script_path.read_text(encoding="utf-8")

    assert start_script_path.exists()
    assert f'SERVER_DIR="{server_dir}"' in start_script_text
    assert f'CONFIG_FILE="{instance_root / "config" / "config.json"}"' in start_script_text
    assert "run_sat_admin_guard" in start_script_text
    assert "-m armactl.sat_admin_guard" in start_script_text
    assert start_script_text.index("run_sat_admin_guard") < start_script_text.index(
        'exec "${SERVER_DIR}/ArmaReforgerServer"'
    )
    assert 'exec "${SERVER_DIR}/ArmaReforgerServer"' in start_script_text
    assert "  -logStats 10000 \\" in start_script_text
    assert start_script_text.index("-logStats 10000") < start_script_text.index("-maxFPS 60")
    assert f"WorkingDirectory={server_dir}" in service_text
    assert f"ExecStart={start_script_path}" in service_text
    assert "TimeoutStopSec=90s" in service_text
    assert "KillMode=mixed" in service_text
    assert "SendSIGKILL=yes" in service_text
    assert "CPUAccounting=yes" in service_text
    assert "MemoryAccounting=yes" in service_text
    assert f"ExecStart={helper_path} armareforger@alpha.service" in restart_service_text
    assert "/usr/bin/systemctl restart armareforger@alpha.service" not in restart_service_text
    assert (
        f"TimeoutStartSec={RESTART_TIMING.restart_unit_timeout_start_sec}" in restart_service_text
    )
    assert installed_modes[helper_path] == "0755"
    assert installed_modes[restart_service_path] == "0644"
    assert "SIGKILL" in helper_text
    assert "STOP_GRACE_SECONDS" in helper_text
    assert "STABLE_SECONDS" in helper_text
    assert "armareforger(?:@[A-Za-z0-9_.-]+)?\\.service" in helper_text
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


def test_generate_services_stops_after_failed_unit_install(tmp_path: Path) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)

    def fake_install(
        source: Path,
        destination: Path,
        *,
        mode: str = "0644",
    ) -> service_manager.ServiceResult:
        return service_manager.ServiceResult(False, f"failed {destination.name}", 7)

    with (
        patch("armactl.service_manager.paths.instance_root", return_value=instance_root),
        patch("armactl.service_manager.paths.SYSTEMD_DIR", systemd_dir),
        patch("armactl.service_manager.paths._containing_git_marker", return_value=None),
        patch("armactl.service_manager.install_systemd_unit_file", side_effect=fake_install),
        patch("armactl.service_manager.daemon_reload") as daemon_reload_mock,
        patch("armactl.service_manager._run_systemctl") as restart_timer_mock,
        patch.dict("os.environ", {"USER": "tester"}, clear=False),
    ):
        results = service_manager.generate_services(instance=instance)

    assert [result.success for result in results] == [True, False]
    assert results[-1].exit_code == 7
    daemon_reload_mock.assert_not_called()
    restart_timer_mock.assert_not_called()


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
        patch("armactl.service_manager.is_active", return_value=False),
        patch("armactl.service_manager.install_systemd_unit_file", side_effect=fake_install),
        patch(
            "armactl.service_manager.daemon_reload",
            return_value=service_manager.ServiceResult(True, "ok"),
        ),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=service_manager.ServiceResult(True, "timer restarted"),
        ) as systemctl_mock,
    ):
        results = service_manager.update_restart_timer_schedule("alpha", "06:00, 18:00")

    assert captured["destination"] == systemd_dir / "armareforger-restart@alpha.timer"
    assert "OnCalendar=*-*-* 06:00:00" in captured["content"]
    assert "OnCalendar=*-*-* 18:00:00" in captured["content"]
    assert [result.success for result in results] == [True, True, True]
    systemctl_mock.assert_called_once_with(
        "clean-timer-state",
        "armareforger-restart@alpha.timer",
    )


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
        patch("armactl.service_manager.paths._containing_git_marker", return_value=None),
    ):
        result = service_manager.sync_generated_start_script(instance)

    start_script_text = start_script_path.read_text(encoding="utf-8")
    assert result.success is True
    assert result.exit_code == 0
    assert "PROFILE_DIR=" not in start_script_text
    assert f'CONFIG_DIR="{config_dir}"' in start_script_text
    assert "-m armactl.sat_admin_guard" in start_script_text
    assert '-profile "${CONFIG_DIR}"' in start_script_text
    assert "-logStats 10000" in start_script_text
    assert start_script_path.stat().st_mode & 0o777 == 0o755


def test_render_start_script_defaults_to_60_max_fps(tmp_path: Path) -> None:
    script = service_manager.render_start_script(
        instance_root=tmp_path / "default",
        server_dir=tmp_path / "default" / "server",
        config_dir=tmp_path / "default" / "config",
        config_file=tmp_path / "default" / "config" / "config.json",
    )

    assert "-maxFPS 60" in script


def test_max_fps_profile_accepts_only_safe_values() -> None:
    assert normalize_max_fps_profile(60) == 60
    assert normalize_max_fps_profile("120") == 120
    for value in (0, 30, 90, 144, "", "060", "120;rm", True):
        try:
            normalize_max_fps_profile(value)
        except RuntimeSettingsError:
            continue
        raise AssertionError(f"{value!r} should be rejected")



def test_update_max_fps_profile_persists_and_regenerates_120(tmp_path: Path) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    server_dir = instance_root / "server"
    config_dir = instance_root / "config"
    config_file = config_dir / "config.json"
    start_script_path = instance_root / "start-armareforger.sh"
    runtime_settings_path = instance_root / "runtime-settings.json"

    server_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    config_file.write_text("{}", encoding="utf-8")
    start_script_path.write_text("#!/usr/bin/env bash\n-maxFPS 60\n", encoding="utf-8")

    with (
        patch("armactl.service_manager.paths.server_dir", return_value=server_dir),
        patch("armactl.service_manager.paths.config_dir", return_value=config_dir),
        patch("armactl.service_manager.paths.config_file", return_value=config_file),
        patch("armactl.service_manager.paths.start_script", return_value=start_script_path),
        patch(
            "armactl.service_manager.paths.runtime_settings_file",
            return_value=runtime_settings_path,
        ),
        patch("armactl.service_manager.paths.backups_dir", return_value=instance_root / "backups"),
        patch("armactl.service_manager.paths._containing_git_marker", return_value=None),
    ):
        result = service_manager.update_max_fps_profile(instance, 120)

    assert result.success is True
    assert "Max FPS profile set to 120" in result.message
    assert "\"max_fps\": 120" in runtime_settings_path.read_text(encoding="utf-8")
    assert "-maxFPS 120" in start_script_path.read_text(encoding="utf-8")
    backups = list((instance_root / "backups").glob("start-armareforger.sh.*.bak"))
    assert len(backups) == 1
    assert "-maxFPS 60" in backups[0].read_text(encoding="utf-8")


def test_sync_generated_preserves_configured_max_fps(tmp_path: Path) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    server_dir = instance_root / "server"
    config_dir = instance_root / "config"
    config_file = config_dir / "config.json"
    start_script_path = instance_root / "start-armareforger.sh"
    runtime_settings_path = instance_root / "runtime-settings.json"

    server_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    config_file.write_text("{}", encoding="utf-8")
    runtime_settings_path.write_text(
        "{\"version\": 1, \"scope\": \"armactl-generated-runtime\", \"max_fps\": 120}\n",
        encoding="utf-8",
    )
    start_script_path.write_text("#!/usr/bin/env bash\n-maxFPS 60\n", encoding="utf-8")

    with (
        patch("armactl.service_manager.paths.server_dir", return_value=server_dir),
        patch("armactl.service_manager.paths.config_dir", return_value=config_dir),
        patch("armactl.service_manager.paths.config_file", return_value=config_file),
        patch("armactl.service_manager.paths.start_script", return_value=start_script_path),
        patch(
            "armactl.service_manager.paths.runtime_settings_file",
            return_value=runtime_settings_path,
        ),
        patch("armactl.service_manager.paths.backups_dir", return_value=instance_root / "backups"),
        patch("armactl.service_manager.paths._containing_git_marker", return_value=None),
    ):
        result = service_manager.sync_generated_start_script(instance)

    assert result.success is True
    assert "-maxFPS 120" in start_script_path.read_text(encoding="utf-8")


def test_generate_services_preserves_configured_max_fps(tmp_path: Path) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    runtime_settings_path = instance_root / "runtime-settings.json"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    instance_root.mkdir(parents=True)
    runtime_settings_path.write_text(
        "{\"version\": 1, \"scope\": \"armactl-generated-runtime\", \"max_fps\": 120}\n",
        encoding="utf-8",
    )

    def fake_install(
        source: Path,
        destination: Path,
        *,
        mode: str = "0644",
    ) -> service_manager.ServiceResult:
        return service_manager.ServiceResult(True, "installed", 0)

    with (
        patch("armactl.service_manager.paths.instance_root", return_value=instance_root),
        patch(
            "armactl.service_manager.paths.runtime_settings_file",
            return_value=runtime_settings_path,
        ),
        patch("armactl.service_manager.paths.SYSTEMD_DIR", systemd_dir),
        patch("armactl.service_manager.paths._containing_git_marker", return_value=None),
        patch("armactl.service_manager.install_systemd_unit_file", side_effect=fake_install),
        patch(
            "armactl.service_manager.daemon_reload",
            return_value=service_manager.ServiceResult(True, "ok"),
        ),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=service_manager.ServiceResult(True, "timer restarted"),
        ),
        patch.dict("os.environ", {"USER": "tester"}, clear=False),
    ):
        results = service_manager.generate_services(instance=instance)

    assert all(result.success for result in results)
    assert "-maxFPS 120" in (instance_root / "start-armareforger.sh").read_text(
        encoding="utf-8"
    )


def test_update_max_fps_profile_rolls_back_existing_settings_on_sync_failure(
    tmp_path: Path,
) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    runtime_settings_path = instance_root / "runtime-settings.json"
    instance_root.mkdir(parents=True)
    previous = (
        b"{\"version\": 1, "
        b"\"scope\": \"armactl-generated-runtime\", "
        b"\"max_fps\": 60}\n"
    )
    runtime_settings_path.write_bytes(previous)
    runtime_settings_path.chmod(0o600)

    with (
        patch(
            "armactl.service_manager.paths.runtime_settings_file",
            return_value=runtime_settings_path,
        ),
        patch(
            "armactl.service_manager.sync_generated_start_script",
            return_value=service_manager.ServiceResult(False, "/tmp/raw token=secret", 7),
        ),
    ):
        result = service_manager.update_max_fps_profile(instance, 120)

    assert result.success is False
    assert result.exit_code == 7
    assert runtime_settings_path.read_bytes() == previous
    assert runtime_settings_path.stat().st_mode & 0o777 == 0o600
    assert "/tmp/raw" not in result.message
    assert "secret" not in result.message


def test_update_max_fps_profile_removes_new_settings_on_sync_failure(
    tmp_path: Path,
) -> None:
    instance = "alpha"
    instance_root = tmp_path / "armactl-data" / instance
    runtime_settings_path = instance_root / "runtime-settings.json"
    instance_root.mkdir(parents=True)

    with (
        patch(
            "armactl.service_manager.paths.runtime_settings_file",
            return_value=runtime_settings_path,
        ),
        patch(
            "armactl.service_manager.sync_generated_start_script",
            return_value=service_manager.ServiceResult(False, "failed", 9),
        ),
    ):
        result = service_manager.update_max_fps_profile(instance, 120)

    assert result.success is False
    assert result.exit_code == 9
    assert not runtime_settings_path.exists()
