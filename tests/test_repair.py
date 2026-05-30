"""Tests for repair orchestration."""

from pathlib import Path
from unittest.mock import patch

import pytest

import armactl.i18n as i18n
import armactl.service_manager as service_manager
from armactl.integrity import check_package_integrity
from armactl.repair import RepairError, run_repair
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
    assert check_package_integrity(server_dir).complete is True
    assert i18n._("  OK Package integrity manifest refreshed") in messages


def test_run_repair_refuses_project_root_install_dir(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RepairError, match="project root"):
        list(run_repair("default", repo_root, config_path))
