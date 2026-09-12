"""Path constants and helpers for armactl instance layout.

All runtime data lives under a single instance root:
    ~/armactl-data/<instance_name>/

System-level files (systemd units) live in /etc/systemd/system/.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_DATA_ROOT = Path.home() / "armactl-data"
DEFAULT_INSTANCE_NAME = "default"
ARMACTL_LOGS_DIR_NAME = "logs"
INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,62}[A-Za-z0-9])?$")

# systemd
SYSTEMD_DIR = Path("/etc/systemd/system")
SUDOERS_DIR = Path("/etc/sudoers.d")
LOCAL_LIBEXEC_DIR = Path("/usr/local/libexec")
SERVICE_NAME = "armareforger.service"
RESTART_SERVICE_NAME = "armareforger-restart.service"
TIMER_NAME = "armareforger-restart.timer"
BOT_SERVICE_NAME = "armactl-bot.service"
DISCORD_STATS_SERVICE_NAME = "armactl-discord-stats.service"
WEB_SERVICE_NAME = "armactl-web.service"
PLAYER_LOG_INGEST_SERVICE_NAME = "armactl-player-log-ingest.service"
PLAYER_LOG_INGEST_TIMER_NAME = "armactl-player-log-ingest.timer"
PLAYER_SESSION_PIPELINE_SERVICE_NAME = "armactl-player-session-pipeline.service"
PLAYER_SESSION_PIPELINE_TIMER_NAME = "armactl-player-session-pipeline.timer"
INCIDENT_MONITOR_SERVICE_NAME = "armactl-incident-monitor.service"
INCIDENT_MONITOR_TIMER_NAME = "armactl-incident-monitor.timer"
PRIVILEGED_HELPER_NAME = "armactl-systemctl-helper"
PRIVILEGED_SUDOERS_NAME = "armactl-systemctl-helper"
SAFE_RESTART_HELPER_NAME = "armactl-safe-restart"


class UnsafeServerInstallDirError(ValueError):
    """Raised when a server install directory could pollute source control."""


class InvalidInstanceNameError(ValueError):
    """Raised when an instance name cannot be used safely in paths or unit names."""


def validate_instance_name(instance: str = DEFAULT_INSTANCE_NAME) -> str:
    """Return a safe armactl instance name or raise InvalidInstanceNameError."""
    value = str(instance or "")
    if value == DEFAULT_INSTANCE_NAME:
        return value
    if not INSTANCE_NAME_RE.fullmatch(value):
        raise InvalidInstanceNameError(
            "Invalid armactl instance name. Use 1-64 ASCII letters, digits, "
            "dots, dashes, or underscores; start and end with a letter or digit."
        )
    if ".." in value:
        raise InvalidInstanceNameError(
            "Invalid armactl instance name. Consecutive dots are not allowed."
        )
    return value


def project_root() -> Path:
    """Return the armactl source tree root."""
    return Path(__file__).resolve().parents[2]


def _is_path_inside_or_equal(child: Path, parent: Path) -> bool:
    """Return True when child resolves to parent or one of its descendants."""
    child_resolved = child.expanduser().resolve(strict=False)
    parent_resolved = parent.expanduser().resolve(strict=False)
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        return False
    return True


def _containing_git_marker(path: Path) -> Path | None:
    """Return the nearest valid .git marker at or above path, if one exists.

    An empty directory named ``.git`` is not a repository.  Treating such a
    stale marker as a working tree blocks managed runtime staging directories
    even though there is no source checkout to protect.
    """
    resolved = path.expanduser().resolve(strict=False)
    candidates = [resolved, *resolved.parents]
    for candidate in candidates:
        git_marker = candidate / ".git"
        if git_marker.is_file():
            return git_marker
        if git_marker.is_dir() and (git_marker / "HEAD").is_file():
            return git_marker
    return None


def _server_dir_guidance(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> str:
    return str(server_dir(instance, data_root).expanduser().resolve(strict=False))


def validate_server_install_dir(
    install_dir: Path | str,
    *,
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Validate that SteamCMD/server runtime files stay outside Git/source trees."""
    resolved = Path(install_dir).expanduser().resolve(strict=False)
    source_root = project_root().expanduser().resolve(strict=False)
    expected_path = server_dir(instance, data_root).expanduser().resolve(strict=False)
    expected_candidate_path = (
        instance_root(instance, data_root) / "server-update" / "candidate-server"
    ).expanduser().resolve(strict=False)
    expected = str(expected_path)

    if resolved == source_root:
        raise UnsafeServerInstallDirError(
            "Refusing to use the armactl project root as the Arma Reforger "
            f"server install directory: {resolved}. Use {expected} instead."
        )

    if _is_path_inside_or_equal(resolved, source_root):
        raise UnsafeServerInstallDirError(
            "Refusing to use a directory inside the armactl source repository "
            f"as the Arma Reforger server install directory: {resolved}. "
            f"Use {expected} instead."
        )

    if ".git" in resolved.parts:
        raise UnsafeServerInstallDirError(
            "Refusing to use a path inside a .git directory as the Arma Reforger "
            f"server install directory: {resolved}. Use {expected} instead."
        )

    git_marker = _containing_git_marker(resolved)
    if git_marker is not None:
        if resolved in {expected_path, expected_candidate_path}:
            return resolved
        raise UnsafeServerInstallDirError(
            "Refusing to use a directory inside a Git working tree as the "
            f"Arma Reforger server install directory: {resolved} "
            f"(found {git_marker}). Use {expected} instead."
        )

    return resolved


# ---------------------------------------------------------------------------
# Instance paths
# ---------------------------------------------------------------------------


def instance_root(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Return the root directory for a given instance."""
    return data_root / validate_instance_name(instance)


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


def incidents_dir(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Persistent evidence bundles captured by the incident monitor."""
    return instance_root(instance, data_root) / "incidents"


def armactl_logs_dir(data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    """Central directory for armactl-owned log files."""
    return data_root / ARMACTL_LOGS_DIR_NAME


def instance_logs_dir(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Centralized armactl log directory scoped to one server instance."""
    return armactl_logs_dir(data_root) / "instances" / validate_instance_name(instance)


def logs_dir(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Backward-compatible alias for centralized instance armactl logs."""
    return instance_logs_dir(instance, data_root)


def host_test_logs_dir(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Centralized directory for host-check logs."""
    return armactl_logs_dir(data_root) / "host-tests" / validate_instance_name(instance)


def web_logs_dir(data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    """Centralized directory for web-panel log files."""
    return armactl_logs_dir(data_root) / "web"


def web_audit_log_file(data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    """Append-only audit log path for mutating web actions."""
    return web_logs_dir(data_root) / "audit.log"


def web_runtime_log_file(data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    """Optional file path for web runtime diagnostics beyond systemd journal."""
    return web_logs_dir(data_root) / "runtime.log"


def mods_state_file(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """armactl-only metadata for reversibly disabled Workshop mods."""
    return instance_root(instance, data_root) / "mods-state.json"


def admins_state_file(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """armactl-only local Steam operator metadata."""
    return instance_root(instance, data_root) / "admins-state.json"


def runtime_settings_file(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """armactl-only generated-runtime settings for the instance."""
    return instance_root(instance, data_root) / "runtime-settings.json"


def modpacks_dir(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Directory for exported/import-ready JSON mod pack files."""
    return instance_root(instance, data_root) / "modpacks"


def bot_dir(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Directory for optional Telegram bot runtime files."""
    return instance_root(instance, data_root) / "bot"


def bot_env_file(
    instance: str = DEFAULT_INSTANCE_NAME,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Path to the bot `.env` file used as the single source of truth."""
    return bot_dir(instance, data_root) / ".env"


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


def bot_service_file() -> Path:
    """Path to the optional Telegram bot systemd service unit."""
    return SYSTEMD_DIR / BOT_SERVICE_NAME


def discord_stats_service_file() -> Path:
    """Path to the optional Discord statistics systemd service unit."""
    return SYSTEMD_DIR / DISCORD_STATS_SERVICE_NAME


def web_service_file() -> Path:
    """Path to the optional web panel systemd service unit."""
    return SYSTEMD_DIR / WEB_SERVICE_NAME


def privileged_helper_file() -> Path:
    """Path to the root-owned helper used for narrow privileged control."""
    return LOCAL_LIBEXEC_DIR / PRIVILEGED_HELPER_NAME


def safe_restart_helper_file() -> Path:
    """Path to the root-owned bounded restart helper used by timer services."""
    return LOCAL_LIBEXEC_DIR / SAFE_RESTART_HELPER_NAME


def privileged_sudoers_file() -> Path:
    """Path to the sudoers drop-in granting passwordless access to the helper."""
    return SUDOERS_DIR / PRIVILEGED_SUDOERS_NAME
