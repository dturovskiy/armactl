"""Tests for armactl.paths module."""

from pathlib import Path

from armactl.paths import (
    InvalidInstanceNameError,
    armactl_logs_dir,
    backups_dir,
    bot_dir,
    bot_env_file,
    bot_service_file,
    config_dir,
    config_file,
    host_test_logs_dir,
    instance_logs_dir,
    instance_root,
    logs_dir,
    modpacks_dir,
    privileged_helper_file,
    privileged_sudoers_file,
    server_binary,
    server_dir,
    start_script,
    state_file,
    validate_instance_name,
    validate_server_install_dir,
    web_audit_log_file,
    web_logs_dir,
    web_runtime_log_file,
)


def test_instance_root_default():
    root = instance_root()
    assert root == Path.home() / "armactl-data" / "default"


def test_instance_root_custom():
    root = instance_root("training", data_root=Path("/tmp/test"))
    assert root == Path("/tmp/test/training")


def test_validate_instance_name_accepts_safe_names():
    assert validate_instance_name("default") == "default"
    assert validate_instance_name("training-01") == "training-01"
    assert validate_instance_name("alpha_2.prod") == "alpha_2.prod"


def test_validate_instance_name_rejects_path_traversal():
    for value in ("", ".", "..", "../escape", "../../escape", "alpha/beta", "alpha..beta"):
        try:
            validate_instance_name(value)
        except InvalidInstanceNameError:
            continue
        raise AssertionError(f"{value!r} should have been rejected")


def test_instance_root_rejects_path_traversal():
    data_root = Path("/tmp/armactl-data")

    try:
        instance_root("../../escape", data_root=data_root)
    except InvalidInstanceNameError:
        return
    raise AssertionError("instance_root() should reject unsafe instance names")


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
    assert path == armactl_logs_dir() / "instances" / "default"


def test_centralized_armactl_log_dirs():
    data_root = Path("/tmp/armactl-data")

    assert armactl_logs_dir(data_root) == data_root / "logs"
    assert instance_logs_dir("training", data_root) == data_root / "logs" / "instances" / "training"
    assert host_test_logs_dir("training", data_root) == (
        data_root / "logs" / "host-tests" / "training"
    )
    assert web_logs_dir(data_root) == data_root / "logs" / "web"
    assert web_audit_log_file(data_root) == data_root / "logs" / "web" / "audit.log"
    assert web_runtime_log_file(data_root) == data_root / "logs" / "web" / "runtime.log"


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


def test_privileged_helper_and_sudoers_paths_are_distinct():
    assert privileged_helper_file() != privileged_sudoers_file()


def test_validate_server_install_dir_allows_expected_instance_dir_under_git_home(
    tmp_path,
    monkeypatch,
):
    data_root = tmp_path / "armactl-data"
    install_dir = data_root / "default" / "server"
    git_marker = tmp_path / ".git"
    git_marker.mkdir()

    monkeypatch.setattr(
        "armactl.paths._containing_git_marker",
        lambda path: git_marker,
    )

    assert validate_server_install_dir(install_dir, data_root=data_root) == install_dir


def test_validate_server_install_dir_ignores_empty_stale_git_marker(tmp_path):
    install_dir = tmp_path / "runtime" / "server-update" / "candidate-server"
    (tmp_path / "runtime" / ".git").mkdir(parents=True)

    assert validate_server_install_dir(install_dir) == install_dir
