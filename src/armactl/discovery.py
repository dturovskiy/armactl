"""Discovery module — find existing Arma Reforger server installations.

Discovery search order:
1. Existing state.json in the instance root
2. Standard paths (~/armactl-data/<instance>/)
3. systemd unit (ExecStart, WorkingDirectory)
4. Legacy paths (~/arma-reforger, ~/.config/ArmaReforgerServer)
5. Fallback: manual mode (caller provides paths)
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from armactl import paths as P
from armactl.state import PortInfo, ServerState, load_state, save_state

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Legacy paths (pre-migration)
# ---------------------------------------------------------------------------

LEGACY_INSTALL_DIRS = [
    Path.home() / "arma-reforger",
]
LEGACY_CONFIG_PATHS = [
    Path.home() / ".config" / "ArmaReforgerServer" / "config.json",
]


# ---------------------------------------------------------------------------
# Low-level checks
# ---------------------------------------------------------------------------


def _binary_exists(install_dir: Path) -> bool:
    """Check if ArmaReforgerServer binary exists in the install dir."""
    return (install_dir / "ArmaReforgerServer").is_file()


def _config_exists(config_path: Path) -> bool:
    """Check if config.json exists."""
    return config_path.is_file()


def _service_exists(service_name: str = P.SERVICE_NAME) -> bool:
    """Check if the systemd service unit file exists."""
    return (P.SYSTEMD_DIR / service_name).is_file()


def _timer_exists(timer_name: str = P.TIMER_NAME) -> bool:
    """Check if the systemd timer unit file exists."""
    return (P.SYSTEMD_DIR / timer_name).is_file()


def _is_service_active(service_name: str = P.SERVICE_NAME) -> bool:
    """Check if the systemd service is currently active (running)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _parse_systemd_unit(service_name: str = P.SERVICE_NAME) -> dict[str, str]:
    """Parse WorkingDirectory and ExecStart from a systemd unit file.

    Returns a dict with keys 'working_directory' and 'exec_start' (if found).
    """
    unit_path = P.SYSTEMD_DIR / service_name
    result: dict[str, str] = {}

    if not unit_path.is_file():
        return result

    try:
        content = unit_path.read_text()
    except OSError:
        return result

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("WorkingDirectory="):
            result["working_directory"] = line.split("=", 1)[1].strip()
        elif line.startswith("ExecStart="):
            result["exec_start"] = line.split("=", 1)[1].strip()

    return result


def _read_ports_from_config(config_path: Path) -> PortInfo:
    """Extract port numbers from config.json."""
    import json

    ports = PortInfo()
    if not config_path.is_file():
        return ports

    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return ports

    ports.game = data.get("bindPort")

    a2s = data.get("a2s", {})
    if isinstance(a2s, dict):
        ports.a2s = a2s.get("port")

    rcon = data.get("rcon", {})
    if isinstance(rcon, dict):
        ports.rcon = rcon.get("port")

    return ports


def _check_listening_ports(port_list: list[int]) -> dict[int, bool]:
    """Check which ports from the list are currently listening (via ss).

    Returns a dict mapping port -> True/False.
    """
    result = {p: False for p in port_list}
    try:
        proc = subprocess.run(
            ["ss", "-lunpt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return result

    for port in port_list:
        # Match :port in the output (both IPv4 and IPv6 formats)
        pattern = rf"[:\s]{port}\b"
        if re.search(pattern, output):
            result[port] = True

    return result


# ---------------------------------------------------------------------------
# Discovery strategies
# ---------------------------------------------------------------------------


def _discover_from_state(
    instance: str,
    data_root: Path,
) -> ServerState | None:
    """Strategy 1: Load existing state.json."""
    sf = P.state_file(instance, data_root)
    state = load_state(sf)
    if state is not None:
        log.info("Loaded existing state from %s", sf)
    return state


def _discover_from_standard_paths(
    instance: str,
    data_root: Path,
) -> ServerState | None:
    """Strategy 2: Check standard armactl-data paths."""
    root = P.instance_root(instance, data_root)
    s_dir = P.server_dir(instance, data_root)
    c_file = P.config_file(instance, data_root)

    if not root.is_dir():
        return None

    binary = _binary_exists(s_dir)
    config = _config_exists(c_file)

    if not binary and not config:
        return None

    log.info("Found instance at standard path: %s", root)

    ports = _read_ports_from_config(c_file) if config else PortInfo()

    return ServerState(
        server_installed=binary,
        binary_exists=binary,
        config_exists=config,
        service_exists=_service_exists(),
        timer_exists=_timer_exists(),
        server_running=_is_service_active(),
        instance_root=str(root),
        install_dir=str(s_dir),
        config_path=str(c_file),
        ports=ports,
    )


def _discover_from_systemd() -> ServerState | None:
    """Strategy 3: Parse systemd unit to find server paths."""
    if not _service_exists():
        return None

    unit_info = _parse_systemd_unit()
    working_dir = unit_info.get("working_directory", "")

    if not working_dir:
        return None

    working_path = Path(working_dir)

    # Try to infer instance_root from working_directory
    # Expected: .../armactl-data/<instance>/server  →  parent.parent = data_root
    # Or legacy: ~/arma-reforger  →  treated as server dir
    if working_path.parent.parent.name == "armactl-data" or working_path.name == "server":
        inst_root = working_path.parent
    else:
        inst_root = working_path

    # Look for config.json in expected locations
    config_path = inst_root / "config" / "config.json"
    if not config_path.is_file():
        # Legacy fallback
        for legacy_cfg in LEGACY_CONFIG_PATHS:
            if legacy_cfg.is_file():
                config_path = legacy_cfg
                break

    config = _config_exists(config_path)
    binary = _binary_exists(working_path)
    ports = _read_ports_from_config(config_path) if config else PortInfo()

    log.info("Found server via systemd unit, working_dir=%s", working_dir)

    return ServerState(
        server_installed=binary,
        binary_exists=binary,
        config_exists=config,
        service_exists=True,
        timer_exists=_timer_exists(),
        server_running=_is_service_active(),
        instance_root=str(inst_root),
        install_dir=str(working_path),
        config_path=str(config_path),
        ports=ports,
    )


def _discover_from_legacy_paths() -> ServerState | None:
    """Strategy 4: Check legacy (pre-armactl) paths."""
    for legacy_dir in LEGACY_INSTALL_DIRS:
        if not legacy_dir.is_dir():
            continue
        binary = _binary_exists(legacy_dir)
        if not binary:
            continue

        # Try to find config
        config_path: Path | None = None
        for legacy_cfg in LEGACY_CONFIG_PATHS:
            if legacy_cfg.is_file():
                config_path = legacy_cfg
                break

        config = config_path is not None and _config_exists(config_path)
        ports = _read_ports_from_config(config_path) if config_path and config else PortInfo()

        log.info("Found legacy server at %s", legacy_dir)

        return ServerState(
            server_installed=True,
            binary_exists=True,
            config_exists=config,
            service_exists=_service_exists(),
            timer_exists=_timer_exists(),
            server_running=_is_service_active(),
            instance_root=str(legacy_dir),
            install_dir=str(legacy_dir),
            config_path=str(config_path) if config_path else "",
            ports=ports,
            migrated_from="legacy",
        )

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover(
    instance: str = P.DEFAULT_INSTANCE_NAME,
    data_root: Path = P.DEFAULT_DATA_ROOT,
    save: bool = True,
) -> ServerState:
    """Run full discovery and return the detected state.

    Tries strategies in order:
    1. Existing state.json
    2. Standard armactl-data paths
    3. systemd unit parsing
    4. Legacy paths

    If nothing is found, returns a "clean" state (server_installed=False).
    If save=True, persists the result to state.json.
    """
    strategies = [
        ("state.json", lambda: _discover_from_state(instance, data_root)),
        ("standard paths", lambda: _discover_from_standard_paths(instance, data_root)),
        ("systemd unit", _discover_from_systemd),
        ("legacy paths", _discover_from_legacy_paths),
    ]

    state: ServerState | None = None

    for name, strategy in strategies:
        log.debug("Trying discovery strategy: %s", name)
        state = strategy()
        if state is not None:
            log.info("Discovery succeeded via: %s", name)
            break

    if state is None:
        log.info("No existing server found")
        state = ServerState()

    # Refresh runtime status even if loaded from state.json
    state.server_running = _is_service_active(state.service_name)
    state.service_exists = _service_exists(state.service_name)
    state.timer_exists = _timer_exists(state.timer_name)
    
    # Always re-read ports from config in case user manually edited config.json
    if state.config_exists and state.config_path:
        state.ports = _read_ports_from_config(Path(state.config_path))

    # Refresh port listening status
    if state.ports and any([state.ports.game, state.ports.a2s, state.ports.rcon]):
        port_list = [
            p for p in [state.ports.game, state.ports.a2s, state.ports.rcon] if p
        ]
        state.listening = _check_listening_ports(port_list)

    if save and state.server_installed:
        sf = P.state_file(instance, data_root)
        save_state(state, sf)
        log.info("Saved state to %s", sf)

    return state


def discover_manual(
    install_dir: Path,
    config_path: Path,
    instance: str = P.DEFAULT_INSTANCE_NAME,
    data_root: Path = P.DEFAULT_DATA_ROOT,
    save: bool = True,
) -> ServerState:
    """Fallback: manually specify paths for an existing server.

    Used when auto-discovery fails and the user provides paths manually.
    """
    binary = _binary_exists(install_dir)
    config = _config_exists(config_path)
    ports = _read_ports_from_config(config_path) if config else PortInfo()

    inst_root = P.instance_root(instance, data_root)

    state = ServerState(
        server_installed=binary,
        binary_exists=binary,
        config_exists=config,
        service_exists=_service_exists(),
        timer_exists=_timer_exists(),
        server_running=_is_service_active(),
        instance_root=str(inst_root),
        install_dir=str(install_dir),
        config_path=str(config_path),
        ports=ports,
    )

    if save:
        sf = P.state_file(instance, data_root)
        save_state(state, sf)
        log.info("Saved manually discovered state to %s", sf)

    return state
