"""Game-admin management routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

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
from armactl.web.auth.permissions import ADMINS_MANAGE, ADMINS_VIEW
from armactl.web.page_models import admins as admins_page_model
from armactl.web.page_models import players as players_page_model
from armactl.web.routes._common import redirect_to_login
from armactl.web.services import admin_actions

router = APIRouter()


def _render_admins_page(
    request: Request,
    current: CurrentSession,
    *,
    result: admin_actions.AdminActionResult | None = None,
    pending_work_warning: str = "",
    pending_work_error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, ADMINS_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = admins_page_model.load_admins_page(paths.DEFAULT_INSTANCE_NAME)
    player_panel = players_page_model.load_player_moderation_panel(
        paths.DEFAULT_INSTANCE_NAME,
        query=request.query_params.get("player_search", ""),
    )
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="admins.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "result": result,
            "pending_work_warning": pending_work_warning,
            "pending_work_error": pending_work_error,
            "can_manage_admins": require_permission(current, ADMINS_MANAGE),
            "player_panel": player_panel,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _run_admin_action(
    request: Request,
    *,
    action: str,
    csrf_token: str,
    admin_reference: str = "",
    label: str = "",
) -> Response:
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, ADMINS_MANAGE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        result = admin_actions.run_admin_action_and_audit(
            action,
            instance=paths.DEFAULT_INSTANCE_NAME,
            admin_reference=admin_reference,
            label=label,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            db_path=current.config.db_path,
        )
    except admin_actions.AdminActionError:
        return PlainTextResponse(
            "Unknown admin action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result_status = status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST
    if result.pending_work_warning or result.pending_work_error:
        result_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    return _render_admins_page(
        request,
        current,
        result=result,
        pending_work_warning=result.pending_work_warning,
        pending_work_error=result.pending_work_error,
        status_code=result_status,
    )


@router.get("/admins", response_class=HTMLResponse)
def admins_page(request: Request) -> Response:
    """Render game admin details and safe edit controls when permitted."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    return _render_admins_page(request, current)


@router.post("/admins/add", response_class=HTMLResponse)
def add_admin_page(
    request: Request,
    csrf_token: str = Form(default=""),
    admin_reference: str = Form(default=""),
    label: str = Form(default=""),
) -> Response:
    """Add or update one game admin."""
    return _run_admin_action(
        request,
        action=admin_actions.ACTION_ADD,
        csrf_token=csrf_token,
        admin_reference=admin_reference,
        label=label,
    )


@router.post("/admins/remove", response_class=HTMLResponse)
def remove_admin_page(
    request: Request,
    csrf_token: str = Form(default=""),
    admin_reference: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Remove one game admin after explicit confirmation."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, ADMINS_MANAGE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if confirm != "remove":
        return _render_admins_page(
            request,
            current,
            result=admin_actions.confirmation_failure(
                instance=paths.DEFAULT_INSTANCE_NAME,
                target=admin_reference,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _run_admin_action(
        request,
        action=admin_actions.ACTION_REMOVE,
        csrf_token=csrf_token,
        admin_reference=admin_reference,
    )
