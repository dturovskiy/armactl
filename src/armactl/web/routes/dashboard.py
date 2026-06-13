"""Read-only dashboard routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from armactl.web.auth.cookies import clear_csrf_cookie, clear_session_cookie, set_csrf_cookie
from armactl.web.auth.dependencies import (
    CurrentSession,
    get_current_session,
    get_form_csrf_token,
    get_web_runtime_config,
    permission_denied_response,
    require_permission,
)
from armactl.web.auth.permissions import (
    ACTIONS_RUN,
    ADMINS_VIEW,
    BOT_VIEW,
    CONFIG_VIEW,
    DASHBOARD_VIEW,
    JOBS_VIEW,
    MODS_VIEW,
)
from armactl.web.facade import load_dashboard_snapshot
from armactl.web.jobs.store import list_recent_jobs
from armactl.web.views.dashboard import build_dashboard_view

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_dashboard(request: Request, current: CurrentSession) -> Response:
    templates = request.app.state.templates
    try:
        snapshot = load_dashboard_snapshot("default", web_config=current.config)
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

    form_csrf = get_form_csrf_token(request, current)
    can_run_actions = require_permission(current, ACTIONS_RUN)
    can_view_jobs = require_permission(current, JOBS_VIEW)
    can_view_config = require_permission(current, CONFIG_VIEW)
    can_view_mods = require_permission(current, MODS_VIEW)
    can_view_admins = require_permission(current, ADMINS_VIEW)
    can_view_bot = require_permission(current, BOT_VIEW)
    dashboard = build_dashboard_view(
        snapshot,
        can_run_actions=can_run_actions,
        can_view_config=can_view_config,
        can_view_mods=can_view_mods,
        can_view_admins=can_view_admins,
        can_view_bot=can_view_bot,
        can_view_jobs=can_view_jobs,
    )
    recent_jobs = list_recent_jobs(current.config.db_path, limit=3) if can_view_jobs else []
    response = templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "snapshot": snapshot,
            "dashboard": dashboard,
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "recent_jobs": recent_jobs,
        },
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard_index(request: Request) -> Response:
    """Render the read-only dashboard."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, DASHBOARD_VIEW):
        return permission_denied_response()
    return _render_dashboard(request, current)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    """Render the read-only dashboard."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, DASHBOARD_VIEW):
        return permission_denied_response()
    return _render_dashboard(request, current)
