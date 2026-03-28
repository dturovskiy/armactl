"""Tests for armactl.discovery module.

These tests use tmp_path to simulate filesystem layouts and mock
subprocess calls to avoid requiring a real systemd environment.
"""

import json
from pathlib import Path
from unittest.mock import patch

from armactl.discovery import (
    _binary_exists,
    _config_exists,
    _discover_from_legacy_paths,
    _discover_from_standard_paths,
    _parse_systemd_unit,
    _read_ports_from_config,
    discover,
    discover_manual,
)
from armactl.state import PortInfo, save_state, ServerState

# ---------------------------------------------------------------------------
# Low-level check tests
# ---------------------------------------------------------------------------


def test_binary_exists_true(tmp_path: Path):
    """Should return True when ArmaReforgerServer binary exists."""
    binary = tmp_path / "ArmaReforgerServer"
    binary.write_text("fake binary")
    assert _binary_exists(tmp_path) is True


def test_binary_exists_false(tmp_path: Path):
    """Should return False when binary is missing."""
    assert _binary_exists(tmp_path) is False


def test_config_exists_true(tmp_path: Path):
    """Should return True when config.json exists."""
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    assert _config_exists(cfg) is True


def test_config_exists_false(tmp_path: Path):
    """Should return False when config.json is missing."""
    assert _config_exists(tmp_path / "config.json") is False


# ---------------------------------------------------------------------------
# Port reading from config
# ---------------------------------------------------------------------------


def test_read_ports_from_config(tmp_path: Path):
    """Should extract ports from a valid config.json."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "bindPort": 2001,
        "a2s": {"port": 17777},
        "rcon": {"port": 19999, "password": "secret"},
    }))
    ports = _read_ports_from_config(cfg)
    assert ports.game == 2001
    assert ports.a2s == 17777
    assert ports.rcon == 19999


def test_read_ports_from_missing_config(tmp_path: Path):
    """Should return empty PortInfo for missing config."""
    ports = _read_ports_from_config(tmp_path / "nope.json")
    assert ports.game is None
    assert ports.a2s is None
    assert ports.rcon is None


def test_read_ports_from_invalid_json(tmp_path: Path):
    """Should return empty PortInfo for invalid JSON."""
    cfg = tmp_path / "config.json"
    cfg.write_text("not json")
    ports = _read_ports_from_config(cfg)
    assert ports.game is None


# ---------------------------------------------------------------------------
# systemd unit parsing
# ---------------------------------------------------------------------------


def test_parse_systemd_unit(tmp_path: Path):
    """Should extract WorkingDirectory and ExecStart from a unit file."""
    unit_content = """[Unit]
Description=Test

[Service]
WorkingDirectory=/home/user/armactl-data/default/server
ExecStart=/home/user/armactl-data/default/start-armareforger.sh

[Install]
WantedBy=multi-user.target
"""
    unit_file = tmp_path / "armareforger.service"
    unit_file.write_text(unit_content)

    with patch("armactl.discovery.paths.SYSTEMD_DIR", tmp_path):
        result = _parse_systemd_unit()

    assert result["working_directory"] == "/home/user/armactl-data/default/server"
    assert result["exec_start"] == "/home/user/armactl-data/default/start-armareforger.sh"


def test_parse_systemd_unit_missing(tmp_path: Path):
    """Should return empty dict for missing unit file."""
    with patch("armactl.discovery.paths.SYSTEMD_DIR", tmp_path):
        result = _parse_systemd_unit()
    assert result == {}


# ---------------------------------------------------------------------------
# Strategy: discover from standard paths
# ---------------------------------------------------------------------------


def _setup_standard_instance(tmp_path: Path) -> Path:
    """Create a standard armactl-data instance layout."""
    data_root = tmp_path / "armactl-data"
    inst = data_root / "default"
    server = inst / "server"
    config = inst / "config"
    server.mkdir(parents=True)
    config.mkdir(parents=True)

    # Create binary
    (server / "ArmaReforgerServer").write_text("fake")

    # Create config
    (config / "config.json").write_text(json.dumps({
        "bindPort": 2001,
        "a2s": {"port": 17777},
        "rcon": {"port": 19999},
    }))

    return data_root


def test_discover_from_standard_paths(tmp_path: Path):
    """Should find a server at standard armactl-data paths."""
    data_root = _setup_standard_instance(tmp_path)

    with patch("armactl.discovery._service_exists", return_value=False), \
         patch("armactl.discovery._timer_exists", return_value=False), \
         patch("armactl.discovery._is_service_active", return_value=False):
        state = _discover_from_standard_paths("default", data_root)

    assert state is not None
    assert state.server_installed is True
    assert state.binary_exists is True
    assert state.config_exists is True
    assert state.ports.game == 2001


def test_discover_from_standard_paths_empty(tmp_path: Path):
    """Should return None when no instance exists."""
    data_root = tmp_path / "armactl-data"
    state = _discover_from_standard_paths("default", data_root)
    assert state is None


# ---------------------------------------------------------------------------
# Strategy: discover from legacy paths
# ---------------------------------------------------------------------------


def test_discover_from_legacy_paths(tmp_path: Path):
    """Should find a server at legacy paths."""
    legacy_dir = tmp_path / "arma-reforger"
    legacy_dir.mkdir()
    (legacy_dir / "ArmaReforgerServer").write_text("fake")

    legacy_cfg_dir = tmp_path / ".config" / "ArmaReforgerServer"
    legacy_cfg_dir.mkdir(parents=True)
    (legacy_cfg_dir / "config.json").write_text(json.dumps({"bindPort": 2001}))

    with patch("armactl.discovery.LEGACY_INSTALL_DIRS", [legacy_dir]), \
         patch("armactl.discovery.LEGACY_CONFIG_PATHS", [legacy_cfg_dir / "config.json"]), \
         patch("armactl.discovery._service_exists", return_value=False), \
         patch("armactl.discovery._timer_exists", return_value=False), \
         patch("armactl.discovery._is_service_active", return_value=False):
        state = _discover_from_legacy_paths()

    assert state is not None
    assert state.server_installed is True
    assert state.migrated_from == "legacy"
    assert state.ports.game == 2001


# ---------------------------------------------------------------------------
# Full discover()
# ---------------------------------------------------------------------------


def test_discover_finds_standard_instance(tmp_path: Path):
    """Full discover() should find a standard instance."""
    data_root = _setup_standard_instance(tmp_path)

    with patch("armactl.discovery._service_exists", return_value=False), \
         patch("armactl.discovery._timer_exists", return_value=False), \
         patch("armactl.discovery._is_service_active", return_value=False), \
         patch("armactl.discovery._check_listening_ports", return_value={}):
        state = discover(instance="default", data_root=data_root, save=True)

    assert state.server_installed is True
    assert state.binary_exists is True
    assert state.ports.game == 2001

    # Verify state.json was saved
    sf = data_root / "default" / "state.json"
    assert sf.is_file()


def test_discover_clean_system(tmp_path: Path):
    """Full discover() on a clean system should return empty state."""
    data_root = tmp_path / "armactl-data"

    with patch("armactl.discovery._service_exists", return_value=False), \
         patch("armactl.discovery._timer_exists", return_value=False), \
         patch("armactl.discovery._is_service_active", return_value=False), \
         patch("armactl.discovery.LEGACY_INSTALL_DIRS", []), \
         patch("armactl.discovery.LEGACY_CONFIG_PATHS", []):
        state = discover(instance="default", data_root=data_root, save=True)

    assert state.server_installed is False
    assert state.binary_exists is False


def test_discover_loads_existing_state(tmp_path: Path):
    """discover() should load existing state.json first."""
    data_root = tmp_path / "armactl-data"
    inst = data_root / "default"
    inst.mkdir(parents=True)

    existing_state = ServerState(
        server_installed=True,
        binary_exists=True,
        instance_root=str(inst),
        install_dir=str(inst / "server"),
        config_path=str(inst / "config" / "config.json"),
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )
    save_state(existing_state, inst / "state.json")

    with patch("armactl.discovery._service_exists", return_value=True), \
         patch("armactl.discovery._timer_exists", return_value=True), \
         patch("armactl.discovery._is_service_active", return_value=False), \
         patch("armactl.discovery._check_listening_ports", return_value={}):
        state = discover(instance="default", data_root=data_root, save=False)

    assert state.server_installed is True
    assert state.ports.game == 2001


# ---------------------------------------------------------------------------
# Manual discovery
# ---------------------------------------------------------------------------


def test_discover_manual(tmp_path: Path):
    """discover_manual should use provided paths."""
    server = tmp_path / "my-server"
    server.mkdir()
    (server / "ArmaReforgerServer").write_text("fake")

    cfg = tmp_path / "my-config.json"
    cfg.write_text(json.dumps({"bindPort": 3000, "a2s": {"port": 18000}}))

    data_root = tmp_path / "armactl-data"

    with patch("armactl.discovery._service_exists", return_value=False), \
         patch("armactl.discovery._timer_exists", return_value=False), \
         patch("armactl.discovery._is_service_active", return_value=False):
        state = discover_manual(
            install_dir=server,
            config_path=cfg,
            instance="default",
            data_root=data_root,
            save=True,
        )

    assert state.server_installed is True
    assert state.binary_exists is True
    assert state.config_exists is True
    assert state.ports.game == 3000
    assert state.ports.a2s == 18000

    # Verify state.json was saved
    sf = data_root / "default" / "state.json"
    assert sf.is_file()

