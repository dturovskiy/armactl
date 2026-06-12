"""FastAPI application factory for the armactl web panel."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from armactl import __version__
from armactl.web.routes.auth import router as auth_router
from armactl.web.routes.dashboard import router as dashboard_router
from armactl.web.routes.health import router as health_router

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def create_app(data_root: Path | None = None) -> FastAPI:
    """Create the ASGI app without starting a server."""
    app = FastAPI(
        title="armactl web",
        version=__version__,
        description="Browser management panel for armactl.",
    )
    app.state.web_data_root = data_root
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    return app
