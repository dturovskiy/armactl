# Server update management routes for armactl web.

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from armactl import paths
from armactl.web.auth.cookies import set_csrf_cookie
from armactl.web.auth.csrf import validate_csrf_token
from armactl.web.auth.dependencies import (
    CurrentSession,
    get_current_session,
    get_form_csrf_token,
    permission_denied_response,
    require_permission,
)
from armactl.web.auth.permissions import DASHBOARD_VIEW, SERVER_UPDATE
from armactl.web.page_models import updates as updates_page_model
from armactl.web.routes._common import redirect_to_login
from armactl.web.services import server_job_actions
from armactl.web.views.updates import build_updates_view

router = APIRouter()


def _render_updates_page(
    request: Request,
    current: CurrentSession,
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, DASHBOARD_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    raw_page = updates_page_model.load_updates_page(
        paths.DEFAULT_INSTANCE_NAME,
        web_config=current.config,
    )
    page = build_updates_view(
        raw_page,
        can_update_server=require_permission(current, SERVER_UPDATE),
    )
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="updates.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _require_update_action(request: Request, csrf_token: str) -> CurrentSession | Response:
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, SERVER_UPDATE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return current


@router.get("/updates", response_class=HTMLResponse)
def updates_page(request: Request) -> Response:
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    return _render_updates_page(request, current)


@router.post("/updates/check")
def check_for_updates(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current

    try:
        server_job_actions.request_server_update_check_and_start(
            current.config.db_path,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
        )
    except server_job_actions.ServerJobAuditError as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse("/updates", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/updates/update")
def update_server(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current

    try:
        result = server_job_actions.request_server_update_and_start(
            current.config.db_path,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
        )
    except server_job_actions.ServerJobAuditError as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if result.status == server_job_actions.SERVER_UPDATE_ACTION_QUEUED:
        return RedirectResponse("/updates", status_code=status.HTTP_303_SEE_OTHER)
    if result.status == server_job_actions.SERVER_UPDATE_ACTION_UP_TO_DATE:
        return PlainTextResponse(result.message, status_code=status.HTTP_200_OK)
    if result.status == server_job_actions.SERVER_UPDATE_ACTION_BLOCKED:
        return PlainTextResponse(result.message, status_code=status.HTTP_400_BAD_REQUEST)
    return PlainTextResponse(result.message, status_code=status.HTTP_409_CONFLICT)
