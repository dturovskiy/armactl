"""Helpers for the foreground web runner."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from armactl.ports import explain_web_port_conflict
from armactl.web.security.exposure import get_exposure_warning

if TYPE_CHECKING:
    from fastapi import FastAPI

    from armactl.web.runtime import WebRuntimeConfig

DEFAULT_WEB_HOST = "127.0.0.1"
WEB_APP_FACTORY = "armactl.web.app:create_app_from_env"
WEB_DATA_ROOT_ENV = "ARMACTL_WEB_DATA_ROOT"
WEB_RELOAD_INCLUDES = ["*.py", "*.html", "*.css"]


class WebRunError(RuntimeError):
    """Raised when the foreground web runner cannot start."""


@dataclass(frozen=True)
class WebRunRequest:
    """Requested foreground web runner settings."""

    host: str | None = None
    port: int | None = None
    dev: bool = False
    data_root: Path | None = None


@dataclass(frozen=True)
class PreparedWebRun:
    """Resolved foreground web runner settings."""

    config: WebRuntimeConfig
    host: str
    port: int
    dev: bool = False


def validate_web_port(port: int) -> None:
    """Raise ValueError when port is reserved for another service."""
    conflict = explain_web_port_conflict(port)
    if conflict is not None:
        raise ValueError(conflict)


def prepare_web_run(request: WebRunRequest) -> PreparedWebRun:
    """Ensure runtime exists and resolve transient foreground bind settings."""
    from armactl.web.runtime import ensure_web_runtime

    if request.port is not None:
        validate_web_port(request.port)

    config = ensure_web_runtime(request.data_root)
    host = request.host if request.host is not None else config.bind_host
    port = request.port if request.port is not None else config.bind_port
    validate_web_port(port)

    return PreparedWebRun(config=config, host=host, port=port, dev=request.dev)


def format_web_run_startup_summary(prepared: PreparedWebRun) -> str:
    """Return a safe foreground startup summary."""
    https_required = "yes" if prepared.config.https_required else "no"
    lines = [
        "Starting armactl web.",
        f"  URL:            http://{prepared.host}:{prepared.port}",
        f"  Data root:      {prepared.config.data_root}",
        f"  Runtime dir:    {prepared.config.runtime_dir}",
        f"  Config file:    {prepared.config.env_path}",
        f"  Database:       {prepared.config.db_path}",
        f"  HTTPS required: {https_required}",
    ]
    warning = get_exposure_warning(prepared.host, prepared.config.https_required)
    if warning is not None:
        lines.append(f"  Exposure warning: {warning.message}")
    if prepared.dev:
        lines.append("  Dev reload:     yes")
    return "\n".join(lines)


def build_web_app(data_root: Path) -> FastAPI:
    """Build the FastAPI app lazily so importing the launcher has no ASGI side effects."""
    from armactl.web.app import create_app

    return create_app(data_root=data_root)


def web_reload_dirs() -> list[str]:
    """Return package-local paths watched by Uvicorn in development reload mode."""
    return [str(Path(__file__).resolve().parent)]


def run_web_foreground(prepared: PreparedWebRun) -> None:
    """Build and run the web app in the foreground with Uvicorn."""
    try:
        import uvicorn
    except ImportError as e:
        raise WebRunError("Web runtime dependency uvicorn is not installed.") from e

    if prepared.dev:
        os.environ[WEB_DATA_ROOT_ENV] = str(prepared.config.data_root)
        uvicorn.run(
            WEB_APP_FACTORY,
            host=prepared.host,
            port=prepared.port,
            factory=True,
            reload=True,
            reload_dirs=web_reload_dirs(),
            reload_includes=WEB_RELOAD_INCLUDES,
        )
        return

    app = build_web_app(prepared.config.data_root)
    uvicorn.run(app, host=prepared.host, port=prepared.port)
