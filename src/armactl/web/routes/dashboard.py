"""Read-only dashboard routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse

from armactl.web.facade import load_dashboard_snapshot

router = APIRouter()


def _render_dashboard(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    try:
        snapshot = load_dashboard_snapshot("default")
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="dashboard_error.html",
            context={
                "error": {
                    "message": "Dashboard data is unavailable.",
                    "type": exc.__class__.__name__,
                }
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"snapshot": snapshot},
    )


@router.get("/", response_class=HTMLResponse)
def dashboard_index(request: Request) -> HTMLResponse:
    """Render the read-only dashboard."""
    return _render_dashboard(request)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Render the read-only dashboard."""
    return _render_dashboard(request)
