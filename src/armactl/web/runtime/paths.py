"""Path helpers for web-panel runtime data."""

from __future__ import annotations

from pathlib import Path

from armactl import paths as armactl_paths

WEB_RUNTIME_DIR_NAME = "web"
WEB_ENV_FILENAME = "web.env"
WEB_DB_FILENAME = "web.db"
WEB_AUDIT_LOG_FILENAME = "audit.log"


def resolved_data_root(data_root: Path | None = None) -> Path:
    """Return the data root that contains web runtime data."""
    if data_root is None:
        return armactl_paths.DEFAULT_DATA_ROOT
    return Path(data_root)


def web_runtime_dir(data_root: Path | None = None) -> Path:
    """Return `~/armactl-data/web/`, separate from any game instance root."""
    return resolved_data_root(data_root) / WEB_RUNTIME_DIR_NAME


def web_env_file(data_root: Path | None = None) -> Path:
    """Return the web runtime `.env` path."""
    return web_runtime_dir(data_root) / WEB_ENV_FILENAME


def web_db_file(data_root: Path | None = None) -> Path:
    """Return the web runtime SQLite database path."""
    return web_runtime_dir(data_root) / WEB_DB_FILENAME


def web_audit_log_file(data_root: Path | None = None) -> Path:
    """Return the centralized web audit log path."""
    return armactl_paths.web_audit_log_file(resolved_data_root(data_root))


__all__ = [
    "resolved_data_root",
    "web_audit_log_file",
    "web_db_file",
    "web_env_file",
    "web_runtime_dir",
]
