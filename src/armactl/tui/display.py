"""Display labels for TUI instance names."""

from __future__ import annotations

from pathlib import Path

from armactl import paths
from armactl.status_summary import load_status_summaries


def get_instance_server_name(
    instance: str,
    config_path: Path | str | None = None,
) -> str | None:
    """Return the public server name for an instance, if config is readable."""
    try:
        config_summary, _ = load_status_summaries(
            config_path if config_path is not None else paths.config_file(instance)
        )
    except Exception:
        return None

    return (config_summary.server_name or "").strip() or None


def get_instance_display_label(
    instance: str,
    config_path: Path | str | None = None,
) -> str:
    """Return a user-facing TUI label for an instance."""
    server_name = get_instance_server_name(instance, config_path=config_path)
    if server_name:
        return f"{server_name} [{instance}]"
    return instance
