"""Tests for repair orchestration."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

import armactl.i18n as i18n
import armactl.service_manager as service_manager
from armactl.installer import InstallError
from armactl.integrity import (
    check_package_integrity,
    install_marker_path,
    mark_install_started,
    write_package_manifest,
)
from armactl.repair import (
    RepairError,
    _restore_root_repair_file_ownership,
    run_repair,
)
from armactl.server_config_schema import generated_default_config_values
from armactl.state import ServerState


def test_run_repair_defaults_empty_paths_and_refreshes_package_manifest(
    tmp_path: Path,
) -> None:
    instance_root = tmp_path / "default"
    server_dir = instance_root / "server"
    config_path = instance_root / "config" / "config.json"
    start_script = instance_root / "start-armareforger.sh"
    server_dir.mkdir(parents=True)
    start_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (server_dir / "ArmaReforgerServer").write_text("fake binary", encoding="utf-8")

    state = ServerState(
        server_running=False,
        service_name="armareforger.service",
        install_dir=str(server_dir),
        config_path=str(config_path),
    )

    with (
        patch("armactl.repair.paths.server_dir", return_value=server_dir),
        patch("armactl.repair.paths.config_file", return_value=config_path),
        patch("armactl.repair.paths.start_script", return_value=start_script),
        patch("armactl.repair.paths._containing_git_marker", return_value=None),
        patch("armactl.repair.discover_manual", return_value=state),
        patch(
            "armactl.repair.stream_server_update",
            return_value=iter(["  steamcmd progress"]),
        ) as stream_mock,
        patch(
            "armactl.repair.generate_services",
            return_value=[service_manager.ServiceResult(True, "generated service")],
        ),
        patch(
            "armactl.repair.install_privileged_systemctl_channel",
            return_value=[service_manager.ServiceResult(True, "installed helper")],
        ),
    ):
        messages = list(run_repair("default", "", ""))

    stream_mock.assert_called_once_with(
        server_dir.resolve(strict=False),
        instance="default",
    )
    assert config_path.is_file()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload == generated_default_config_values(
        rcon_password=payload["rcon"]["password"],
        password_admin=payload["game"]["passwordAdmin"],
    )
    assert check_package_integrity(server_dir).complete is True
    assert i18n._("  OK Package integrity manifest refreshed") in messages


def test_run_repair_installs_privileged_helper_before_systemd_units(
    tmp_path: Path,
) -> None:
    instance_root = tmp_path / "default"
    server_dir = instance_root / "server"
    config_path = instance_root / "config" / "config.json"
    start_script = instance_root / "start-armareforger.sh"
    server_dir.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            generated_default_config_values(
                rcon_password="repair-rcon-secret",
                password_admin="repair-admin-secret",
            )
        ),
        encoding="utf-8",
    )
    start_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (server_dir / "ArmaReforgerServer").write_text("fake binary", encoding="utf-8")
    state = ServerState(
        server_running=False,
        service_name="armareforger.service",
        install_dir=str(server_dir),
        config_path=str(config_path),
    )
    order: list[str] = []

    with (
        patch("armactl.repair.paths._containing_git_marker", return_value=None),
        patch("armactl.repair.paths.start_script", return_value=start_script),
        patch("armactl.repair.discover_manual", return_value=state),
        patch("armactl.repair.stream_server_update", return_value=iter(())),
        patch(
            "armactl.repair.install_privileged_systemctl_channel",
            side_effect=lambda: (
                order.append("helper")
                or [service_manager.ServiceResult(True, "installed helper")]
            ),
        ),
        patch(
            "armactl.repair.generate_services",
            side_effect=lambda **kwargs: (
                order.append("units")
                or [service_manager.ServiceResult(True, "generated service")]
            ),
        ),
    ):
        list(run_repair("default", server_dir, config_path))

    assert order == ["helper", "units"]


def test_root_repair_restores_only_bounded_root_owned_runtime_files(
    tmp_path: Path,
) -> None:
    instance_root = tmp_path / "default"
    server_dir = instance_root / "server"
    config_path = instance_root / "config" / "config.json"
    backup = instance_root / "backups" / "start-armareforger.sh.test.bak"
    files = [
        server_dir / ".armactl-package-manifest.json",
        config_path,
        instance_root / "admins-state.json",
        instance_root / "state.json",
        instance_root / "start-armareforger.sh",
        backup,
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    original_lstat = Path.lstat

    def root_owned_lstat(path: Path):
        stat = original_lstat(path)
        return SimpleNamespace(st_uid=0, st_mode=stat.st_mode)

    with (
        patch("armactl.repair.os.geteuid", return_value=0),
        patch("armactl.repair.resolve_linux_user", return_value="operator"),
        patch(
            "armactl.repair.pwd.getpwnam",
            return_value=SimpleNamespace(pw_uid=1234, pw_gid=1234),
        ),
        patch(
            "armactl.repair.Path.lstat",
            autospec=True,
            side_effect=root_owned_lstat,
        ),
        patch("armactl.repair.os.chown") as chown_mock,
    ):
        owner, changed = _restore_root_repair_file_ownership(
            instance_root,
            server_dir,
            config_path,
        )

    assert owner == "operator"
    assert changed == len(files)
    assert chown_mock.call_args_list == [
        call(path, 1234, 1234, follow_symlinks=False) for path in files
    ]


def test_run_repair_refuses_project_root_install_dir(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RepairError, match="project root"):
        list(run_repair("default", repo_root, config_path))


def test_run_repair_clears_new_install_marker_after_steamcmd_failure(
    tmp_path: Path,
) -> None:
    instance_root = tmp_path / "default"
    server_dir = instance_root / "server"
    config_path = instance_root / "config" / "config.json"
    server_dir.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    (server_dir / "ArmaReforgerServer").write_text("fake binary", encoding="utf-8")
    config_path.write_text("{}", encoding="utf-8")

    state = ServerState(
        server_running=False,
        service_name="armareforger.service",
        install_dir=str(server_dir),
        config_path=str(config_path),
    )

    with (
        patch("armactl.repair.paths._containing_git_marker", return_value=None),
        patch("armactl.repair.discover_manual", return_value=state),
        patch(
            "armactl.repair.stream_server_update",
            side_effect=InstallError("SteamCMD failed"),
        ),
    ):
        with pytest.raises(RepairError, match="SteamCMD failed"):
            list(run_repair("default", server_dir, config_path))

    assert not install_marker_path(server_dir).exists()


def test_run_repair_clears_stale_marker_for_previous_complete_install(
    tmp_path: Path,
) -> None:
    instance_root = tmp_path / "default"
    server_dir = instance_root / "server"
    config_path = instance_root / "config" / "config.json"
    server_dir.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    (server_dir / "ArmaReforgerServer").write_text("fake binary", encoding="utf-8")
    config_path.write_text("{}", encoding="utf-8")
    write_package_manifest(server_dir)
    mark_install_started(server_dir)

    state = ServerState(
        server_running=False,
        service_name="armareforger.service",
        install_dir=str(server_dir),
        config_path=str(config_path),
    )

    with (
        patch("armactl.repair.paths._containing_git_marker", return_value=None),
        patch("armactl.repair.discover_manual", return_value=state),
        patch(
            "armactl.repair.stream_server_update",
            side_effect=InstallError("SteamCMD failed"),
        ),
    ):
        with pytest.raises(RepairError, match="SteamCMD failed"):
            list(run_repair("default", server_dir, config_path))

    assert not install_marker_path(server_dir).exists()
