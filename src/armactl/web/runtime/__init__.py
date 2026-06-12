"""Runtime storage helpers for the armactl web panel."""

from __future__ import annotations

from pathlib import Path

from armactl.web.runtime.config import (
    WebRuntimeConfig,
    WebRuntimeConfigError,
    ensure_web_runtime_config,
    load_web_runtime_config,
    render_web_runtime_config,
    save_web_runtime_config,
)
from armactl.web.runtime.db import ensure_web_db
from armactl.web.runtime.paths import (
    web_audit_log_file,
    web_db_file,
    web_env_file,
    web_runtime_dir,
)


def ensure_web_runtime(data_root: Path | None = None) -> WebRuntimeConfig:
    """Ensure web-specific config and database files exist."""
    config = ensure_web_runtime_config(data_root)
    ensure_web_db(config.db_path)
    return config


__all__ = [
    "WebRuntimeConfig",
    "WebRuntimeConfigError",
    "ensure_web_db",
    "ensure_web_runtime",
    "ensure_web_runtime_config",
    "load_web_runtime_config",
    "render_web_runtime_config",
    "save_web_runtime_config",
    "web_audit_log_file",
    "web_db_file",
    "web_env_file",
    "web_runtime_dir",
]
