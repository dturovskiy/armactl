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
from armactl.web.auth.permissions import ACTIONS_RUN, JOBS_VIEW, SERVER_UPDATE
from armactl.web.jobs.store import list_recent_jobs
from armactl.web.services import job_integrity, server_job_actions
from armactl.web.services.pending_work import list_pending_work_with_fallback

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_jobs(request: Request, current: CurrentSession) -> Response:
    form_csrf = get_form_csrf_token(request, current)
    job_store_diagnostics = job_integrity.job_store_integrity_diagnostics(
        current.config.db_path
    )
    pending_work_items = list_pending_work_with_fallback(
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
            "job_store_diagnostics": job_store_diagnostics,
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

    try:
        server_job_actions.enqueue_server_job_and_start(
            current.config.db_path,
            action=action,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
        )
    except server_job_actions.ServerJobActionError:
        return PlainTextResponse(
            "Unknown job action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except server_job_actions.ServerJobAuditError as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse("/jobs", status_code=status.HTTP_303_SEE_OTHER)


def _enqueue_server_update_job(
    request: Request,
    csrf_token: str,
    confirm: str,
) -> Response:
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, SERVER_UPDATE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        result = server_job_actions.request_server_update_and_start(
            current.config.db_path,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
            confirm_running=confirm == "running-update",
        )
    except server_job_actions.ServerJobAuditError as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if result.status == server_job_actions.SERVER_UPDATE_ACTION_QUEUED:
        return RedirectResponse("/jobs", status_code=status.HTTP_303_SEE_OTHER)
    if result.status == server_job_actions.SERVER_UPDATE_ACTION_UP_TO_DATE:
        return PlainTextResponse(result.message, status_code=status.HTTP_200_OK)
    if result.status == server_job_actions.SERVER_UPDATE_ACTION_BLOCKED:
        return PlainTextResponse(result.message, status_code=status.HTTP_400_BAD_REQUEST)
    return PlainTextResponse(result.message, status_code=status.HTTP_409_CONFLICT)


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


@router.post("/jobs/server/update")
def enqueue_server_update_route(
    request: Request,
    csrf_token: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Queue server update only after the service-layer version gate passes."""
    return _enqueue_server_update_job(request, csrf_token, confirm)


@router.get("/jobs", response_class=HTMLResponse)
def jobs_index(request: Request) -> Response:
    """Render recent background jobs without mutating state."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, JOBS_VIEW):
        return permission_denied_response()
    return _render_jobs(request, current)
