"""Helpers for the planned foreground web runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl.ports import WEB_PANEL_DEFAULT_PORT, explain_web_port_conflict

DEFAULT_WEB_HOST = "127.0.0.1"


@dataclass(frozen=True)
class WebRunOptions:
    """Options accepted by the future foreground web runner."""

    host: str = DEFAULT_WEB_HOST
    port: int = WEB_PANEL_DEFAULT_PORT
    dev: bool = False
    data_root: Path | None = None


def validate_web_port(port: int) -> None:
    """Raise ValueError when port is reserved for another service."""
    conflict = explain_web_port_conflict(port)
    if conflict is not None:
        raise ValueError(conflict)


def format_not_implemented_message(options: WebRunOptions) -> str:
    """Return the placeholder message for the not-yet-implemented web runtime."""
    details = [
        "Web runtime is not implemented yet."
        f" Requested bind: {options.host}:{options.port}."
    ]
    if options.dev:
        details.append(" Development mode requested.")
    if options.data_root is not None:
        details.append(f" Data root: {options.data_root}.")
    return "".join(details)
