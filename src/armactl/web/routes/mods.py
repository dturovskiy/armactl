"""Workshop mod management routes for armactl web."""

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
from armactl.web.auth.permissions import MODS_MANAGE, MODS_VIEW
from armactl.web.page_models.mods import load_mods_page
from armactl.web.routes._common import mark_restart_pending_for_result, redirect_to_login
from armactl.web.services import mod_actions, pending_work

router = APIRouter()


def _render_mods_page(
    request: Request,
    current: CurrentSession,
    *,
    result: mod_actions.ModActionResult | None = None,
    pending_work_warning: str = "",
    pending_work_error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, MODS_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = load_mods_page(paths.DEFAULT_INSTANCE_NAME)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="mods.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "result": result,
            "pending_work_warning": pending_work_warning,
            "pending_work_error": pending_work_error,
            "can_manage_mods": require_permission(current, MODS_MANAGE),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _run_mod_action(
    request: Request,
    *,
    action: str,
    csrf_token: str,
    mod_id: str = "",
    name: str = "",
    version: str = "",
) -> Response:
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, MODS_MANAGE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        result = mod_actions.run_mod_action_and_audit(
            action,
            instance=paths.DEFAULT_INSTANCE_NAME,
            mod_id=mod_id,
            name=name,
            version=version,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
        )
    except mod_actions.ModActionError:
        return PlainTextResponse(
            "Unknown mod action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    pending_warning = ""
    if result.changed:
        try:
            pending_warning = mark_restart_pending_for_result(
                current,
                kind=pending_work.KIND_MODS,
                source_action=result.action,
                details=result.target,
            )
        except pending_work.PendingWorkFallbackError as error:
            return _render_mods_page(
                request,
                current,
                result=result,
                pending_work_error=str(error),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    result_status = status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST
    if pending_warning:
        result_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    return _render_mods_page(
        request,
        current,
        result=result,
        pending_work_warning=pending_warning,
        status_code=result_status,
    )


@router.get("/mods", response_class=HTMLResponse)
def mods_page(request: Request) -> Response:
    """Render mod details and safe edit controls when permitted."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    return _render_mods_page(request, current)


@router.post("/mods/add", response_class=HTMLResponse)
def add_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
    name: str = Form(default=""),
    version: str = Form(default=""),
) -> Response:
    """Add or update one Workshop mod."""
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_ADD,
        csrf_token=csrf_token,
        mod_id=mod_id,
        name=name,
        version=version,
    )


@router.post("/mods/disable", response_class=HTMLResponse)
def disable_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
) -> Response:
    """Disable one active Workshop mod without deleting addon files."""
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_DISABLE,
        csrf_token=csrf_token,
        mod_id=mod_id,
    )


@router.post("/mods/enable", response_class=HTMLResponse)
def enable_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
) -> Response:
    """Enable one disabled Workshop mod."""
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_ENABLE,
        csrf_token=csrf_token,
        mod_id=mod_id,
    )


@router.post("/mods/remove", response_class=HTMLResponse)
def remove_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Remove one Workshop mod after explicit confirmation."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, MODS_MANAGE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if confirm != "remove":
        return _render_mods_page(
            request,
            current,
            result=mod_actions.confirmation_failure(
                instance=paths.DEFAULT_INSTANCE_NAME,
                target=mod_id,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_REMOVE,
        csrf_token=csrf_token,
        mod_id=mod_id,
    )
