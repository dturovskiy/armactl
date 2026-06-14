"""FastAPI application factory for the armactl web panel."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from armactl import __version__
from armactl.web.i18n import web_template_context
from armactl.web.routes.auth import router as auth_router
from armactl.web.routes.dashboard import router as dashboard_router
from armactl.web.routes.files import router as files_router
from armactl.web.routes.health import router as health_router
from armactl.web.routes.jobs import router as jobs_router
from armactl.web.routes.logs import router as logs_router
from armactl.web.routes.management import router as management_router
from armactl.web.routes.preferences import router as preferences_router
from armactl.web.routes.service import router as service_router

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
WEB_DATA_ROOT_ENV = "ARMACTL_WEB_DATA_ROOT"


def create_app(data_root: Path | None = None) -> FastAPI:
    """Create the ASGI app without starting a server."""
    app = FastAPI(
        title="armactl web",
        version=__version__,
        description="Browser management panel for armactl.",
    )
    app.state.web_data_root = data_root
    app.state.templates = Jinja2Templates(
        directory=str(TEMPLATES_DIR),
        context_processors=[web_template_context],
    )

    @app.middleware("http")
    async def no_store_operator_pages(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            return response
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type.lower():
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(preferences_router)
    app.include_router(dashboard_router)
    app.include_router(files_router)
    app.include_router(jobs_router)
    app.include_router(logs_router)
    app.include_router(management_router)
    app.include_router(service_router)
    return app


def create_app_from_env() -> FastAPI:
    """Create the ASGI app for Uvicorn import-string/factory reload mode."""
    data_root = os.environ.get(WEB_DATA_ROOT_ENV)
    return create_app(data_root=Path(data_root) if data_root else None)
