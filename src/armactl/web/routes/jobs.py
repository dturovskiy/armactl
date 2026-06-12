"""Read-only background jobs routes for armactl web."""

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
from armactl.web.auth.permissions import JOBS_VIEW
from armactl.web.jobs.store import list_recent_jobs

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_jobs(request: Request, current: CurrentSession) -> Response:
    form_csrf = get_form_csrf_token(request, current)
    jobs = list_recent_jobs(current.config.db_path, limit=25)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="jobs.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "jobs": jobs,
        },
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/jobs", response_class=HTMLResponse)
def jobs_index(request: Request) -> Response:
    """Render recent background jobs without mutating state."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, JOBS_VIEW):
        return permission_denied_response()
    return _render_jobs(request, current)
