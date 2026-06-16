"""Read-only background jobs routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from armactl import paths
from armactl.web.auth.cookies import clear_csrf_cookie, clear_session_cookie, set_csrf_cookie
from armactl.web.auth.csrf import validate_csrf_token
from armactl.web.auth.dependencies import (
    CurrentSession,
    get_current_session,
    get_form_csrf_token,
    get_web_runtime_config,
    permission_denied_response,
    require_permission,
)
from armactl.web.auth.permissions import ACTIONS_RUN, JOBS_VIEW
from armactl.web.jobs import server as server_jobs
from armactl.web.jobs.store import list_recent_jobs
from armactl.web.services.pending_work import list_pending_work

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_jobs(request: Request, current: CurrentSession) -> Response:
    form_csrf = get_form_csrf_token(request, current)
    pending_work_items = list_pending_work(
        current.config.db_path,
        instance=paths.DEFAULT_INSTANCE_NAME,
    )
    jobs = list_recent_jobs(current.config.db_path, limit=25)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="jobs.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "jobs": jobs,
            "pending_work_items": pending_work_items,
        },
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _enqueue_server_job(request: Request, csrf_token: str, action: str) -> Response:
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, ACTIONS_RUN):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if action == "install":
        job = server_jobs.enqueue_server_install(
            current.config.db_path,
            requested_by_username=current.user.username,
            requested_by_user_id=current.user.id,
        )
    elif action == "repair":
        job = server_jobs.enqueue_server_repair(
            current.config.db_path,
            requested_by_username=current.user.username,
            requested_by_user_id=current.user.id,
        )
    else:
        return PlainTextResponse(
            "Unknown job action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    server_jobs.start_server_job_worker(current.config.db_path, job.id)
    return RedirectResponse("/jobs", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/jobs/server/install")
def enqueue_server_install_route(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Queue server installation without running it in the HTTP request."""
    return _enqueue_server_job(request, csrf_token, "install")


@router.post("/jobs/server/repair")
def enqueue_server_repair_route(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Queue server repair without running it in the HTTP request."""
    return _enqueue_server_job(request, csrf_token, "repair")


@router.get("/jobs", response_class=HTMLResponse)
def jobs_index(request: Request) -> Response:
    """Render recent background jobs without mutating state."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, JOBS_VIEW):
        return permission_denied_response()
    return _render_jobs(request, current)
