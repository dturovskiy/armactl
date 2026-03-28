"""Tests for armactl.paths module."""

from pathlib import Path

from armactl.paths import (
    backups_dir,
    config_dir,
    config_file,
    instance_root,
    logs_dir,
    server_binary,
    server_dir,
    start_script,
    state_file,
)


def test_instance_root_default():
    root = instance_root()
    assert root == Path.home() / "armactl-data" / "default"


def test_instance_root_custom():
    root = instance_root("training", data_root=Path("/tmp/test"))
    assert root == Path("/tmp/test/training")


def test_server_dir():
    path = server_dir()
    assert path == instance_root() / "server"


def test_config_file():
    path = config_file()
    assert path == instance_root() / "config" / "config.json"


def test_config_dir():
    path = config_dir()
    assert path == instance_root() / "config"


def test_backups_dir():
    path = backups_dir()
    assert path == instance_root() / "backups"


def test_logs_dir():
    path = logs_dir()
    assert path == instance_root() / "logs"


def test_state_file():
    path = state_file()
    assert path == instance_root() / "state.json"


def test_start_script():
    path = start_script()
    assert path == instance_root() / "start-armareforger.sh"


def test_server_binary():
    path = server_binary()
    assert path == server_dir() / "ArmaReforgerServer"
