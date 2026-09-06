"""Read-only logs and diagnostic report routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
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
from armactl.web.auth.permissions import LOGS_VIEW
from armactl.web.page_models import incidents as incidents_page_model
from armactl.web.services import log_views

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_log_source(
    request: Request,
    current: CurrentSession,
    source_id: str,
    *,
    lines: str | None,
) -> Response:
    form_csrf = get_form_csrf_token(request, current)
    status_code = status.HTTP_200_OK
    try:
        view = log_views.build_log_view(current.config, source_id, lines=lines)
    except log_views.UnknownLogSourceError:
        view = log_views.build_unknown_log_view(source_id, lines=lines)
        status_code = status.HTTP_404_NOT_FOUND

    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "sources": log_views.list_log_sources(),
            "view": view,
            "max_lines": log_views.MAX_LOG_LINES,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _authenticated_log_source(
    request: Request,
    source_id: str,
    *,
    lines: str | None,
) -> Response:
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, LOGS_VIEW):
        return permission_denied_response()
    return _render_log_source(request, current, source_id, lines=lines)


@router.get("/logs", response_class=HTMLResponse)
def logs_index(request: Request, lines: str | None = Query(default=None)) -> Response:
    """Render the default audit log source."""
    return _authenticated_log_source(request, log_views.SOURCE_AUDIT, lines=lines)


@router.get("/incidents", response_class=HTMLResponse)
def incidents_index(request: Request) -> Response:
    """Render bounded historical game crash evidence."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, LOGS_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    try:
        page = incidents_page_model.load_incidents_page(
            "default",
            data_root=current.config.data_root,
        )
        page["error"] = ""
    except Exception:  # noqa: BLE001 - incident history must fail closed.
        page = {
            "instance": "default",
            "incidents": [],
            "count": 0,
            "history_days": incidents_page_model.INCIDENT_HISTORY_DAYS,
            "history_limit": incidents_page_model.INCIDENT_HISTORY_LIMIT,
            "error": "Incident history is unavailable.",
        }

    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="incidents.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
        },
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/logs/{source_id}", response_class=HTMLResponse)
def logs_source(
    request: Request,
    source_id: str,
    lines: str | None = Query(default=None),
) -> Response:
    """Render an allowlisted log source."""
    return _authenticated_log_source(request, source_id, lines=lines)


@router.get("/report", response_class=HTMLResponse)
def report_preview(request: Request, lines: str | None = Query(default=None)) -> Response:
    """Render the redacted diagnostic report preview."""
    return _authenticated_log_source(request, log_views.SOURCE_REPORT, lines=lines)
