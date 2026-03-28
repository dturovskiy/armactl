"""Tests for armactl.paths module."""

from pathlib import Path

from armactl.paths import (
    backups_dir,
    bot_dir,
    bot_env_file,
    bot_service_file,
    config_dir,
    config_file,
    instance_root,
    logs_dir,
    modpacks_dir,
    privileged_helper_file,
    privileged_sudoers_file,
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


def test_modpacks_dir():
    path = modpacks_dir()
    assert path == instance_root() / "modpacks"


def test_bot_dir():
    path = bot_dir()
    assert path == instance_root() / "bot"


def test_bot_env_file():
    path = bot_env_file()
    assert path == instance_root() / "bot" / ".env"


def test_state_file():
    path = state_file()
    assert path == instance_root() / "state.json"


def test_start_script():
    path = start_script()
    assert path == instance_root() / "start-armareforger.sh"


def test_server_binary():
    path = server_binary()
    assert path == server_dir() / "ArmaReforgerServer"


def test_bot_service_file():
    path = bot_service_file()
    assert path == Path("/etc/systemd/system") / "armactl-bot.service"


def test_privileged_helper_file():
    path = privileged_helper_file()
    assert path == Path("/usr/local/libexec") / "armactl-systemctl-helper"


def test_privileged_sudoers_file():
    path = privileged_sudoers_file()
    assert path == Path("/etc/sudoers.d") / "armactl-systemctl-helper"
