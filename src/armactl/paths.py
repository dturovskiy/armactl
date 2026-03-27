"""Path constants and helpers for armactl instance layout.

All runtime data lives under a single instance root:
    ~/armactl-data/<instance_name>/

System-level files (systemd units) live in /etc/systemd/system/.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_DATA_ROOT = Path.home() / "armactl-data"
DEFAULT_INSTANCE_NAME = "default"

# systemd
SYSTEMD_DIR = Path("/etc/systemd/system")
SERVICE_NAME = "armareforger.service"
RESTART_SERVICE_NAME = "armareforger-restart.service"
TIMER_NAME = "armareforger-restart.timer"


# ---------------------------------------------------------------------------
# Instance paths
# ---------------------------------------------------------------------------


def instance_root(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Return the root directory for a given instance."""
    return data_root / instance


def server_dir(instance: str = DEFAULT_INSTANCE_NAME, data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    """SteamCMD install directory (ArmaReforgerServer binary lives here)."""
    return instance_root(instance, data_root) / "server"


def config_dir(instance: str = DEFAULT_INSTANCE_NAME, data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    """Directory containing config.json."""
    return instance_root(instance, data_root) / "config"


def config_file(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Path to config.json."""
    return config_dir(instance, data_root) / "config.json"


def backups_dir(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Directory for automatic backups before config changes."""
    return instance_root(instance, data_root) / "backups"


def state_file(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Path to state.json (discovery/state persistence)."""
    return instance_root(instance, data_root) / "state.json"


def start_script(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Path to the launch script referenced by systemd service."""
    return instance_root(instance, data_root) / "start-armareforger.sh"


def server_binary(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Path to ArmaReforgerServer binary."""
    return server_dir(instance, data_root) / "ArmaReforgerServer"


# ---------------------------------------------------------------------------
# systemd paths
# ---------------------------------------------------------------------------


def service_file() -> Path:
    """Path to the main systemd service unit."""
    return SYSTEMD_DIR / SERVICE_NAME


def restart_service_file() -> Path:
    """Path to the restart helper service unit."""
    return SYSTEMD_DIR / RESTART_SERVICE_NAME


def timer_file() -> Path:
    """Path to the systemd restart timer unit."""
    return SYSTEMD_DIR / TIMER_NAME
