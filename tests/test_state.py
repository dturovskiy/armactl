"""Tests for armactl.state module."""

import json
from pathlib import Path

from armactl.state import PortInfo, ServerState, load_state, save_state


def test_server_state_defaults():
    """New ServerState should have all flags False."""
    state = ServerState()
    assert state.server_installed is False
    assert state.binary_exists is False
    assert state.config_exists is False
    assert state.server_running is False
    assert state.instance_root == ""
    assert state.ports.game is None


def test_port_info_defaults():
    """PortInfo should have all ports as None by default."""
    ports = PortInfo()
    assert ports.game is None
    assert ports.a2s is None
    assert ports.rcon is None


def test_server_state_to_dict():
    """to_dict should produce a JSON-serializable dict."""
    state = ServerState(
        server_installed=True,
        binary_exists=True,
        instance_root="/tmp/test",
        install_dir="/tmp/test/server",
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )
    data = state.to_dict()

    assert data["server_installed"] is True
    assert data["binary_exists"] is True
    assert data["instance_root"] == "/tmp/test"
    assert data["ports"]["game"] == 2001
    assert data["ports"]["a2s"] == 17777
    assert data["ports"]["rcon"] == 19999
    assert "discovered_at" in data
    # Ensure it's JSON-serializable
    json.dumps(data)


def test_server_state_from_dict():
    """from_dict should reconstruct a ServerState."""
    data = {
        "server_installed": True,
        "binary_exists": True,
        "config_exists": False,
        "service_exists": True,
        "timer_exists": False,
        "server_running": True,
        "instance_root": "/home/user/armactl-data/default",
        "install_dir": "/home/user/armactl-data/default/server",
        "config_path": "",
        "service_name": "armareforger.service",
        "timer_name": "armareforger-restart.timer",
        "ports": {"game": 2001, "a2s": 17777, "rcon": 19999},
        "discovered_at": "2025-01-01T00:00:00",
        "migrated_from": "",
    }
    state = ServerState.from_dict(data)

    assert state.server_installed is True
    assert state.server_running is True
    assert state.ports.game == 2001
    assert state.ports.rcon == 19999
    assert state.service_name == "armareforger.service"


def test_save_and_load_state(tmp_path: Path):
    """save_state + load_state should roundtrip correctly."""
    state = ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        instance_root=str(tmp_path),
        install_dir=str(tmp_path / "server"),
        config_path=str(tmp_path / "config" / "config.json"),
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )

    state_path = tmp_path / "state.json"
    save_state(state, state_path)

    assert state_path.is_file()

    loaded = load_state(state_path)
    assert loaded is not None
    assert loaded.server_installed is True
    assert loaded.binary_exists is True
    assert loaded.config_exists is True
    assert loaded.ports.game == 2001
    assert loaded.instance_root == str(tmp_path)


def test_load_state_missing_file(tmp_path: Path):
    """load_state should return None for missing file."""
    result = load_state(tmp_path / "nonexistent.json")
    assert result is None


def test_load_state_invalid_json(tmp_path: Path):
    """load_state should return None for invalid JSON."""
    bad_file = tmp_path / "state.json"
    bad_file.write_text("not valid json {{{")
    result = load_state(bad_file)
    assert result is None


def test_save_state_creates_parent_dirs(tmp_path: Path):
    """save_state should create parent directories if needed."""
    deep_path = tmp_path / "a" / "b" / "c" / "state.json"
    state = ServerState(server_installed=True)
    save_state(state, deep_path)
    assert deep_path.is_file()

    loaded = load_state(deep_path)
    assert loaded is not None
    assert loaded.server_installed is True
