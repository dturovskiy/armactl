"""POST-only server service action routes for armactl web."""

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
from armactl.web.auth.permissions import ACTIONS_RUN
from armactl.web.services import service_actions

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _backend_success(result: service_actions.ServiceActionResult) -> bool:
    return result.success if result.backend_success is None else result.backend_success


def _operator_result_title(result: service_actions.ServiceActionResult) -> str:
    if result.action == "restart":
        return "Server restart completed." if _backend_success(result) else "Server restart failed."
    if result.action == "start":
        return "Server start completed." if _backend_success(result) else "Server start failed."
    if result.action == "stop":
        return "Server stop completed." if _backend_success(result) else "Server stop failed."
    return "Service action completed." if _backend_success(result) else "Service action failed."


def _operator_result_message(
    result: service_actions.ServiceActionResult,
) -> str:
    backend_success = _backend_success(result)
    if result.action in {"start", "restart"} and backend_success:
        if not result.audit_written:
            return result.message
        if result.pending_restart_work_cleared:
            return "Pending restart work cleared."
        if result.action == "restart":
            return "No pending restart work was waiting."
    if result.action == "restart" and result.performed:
        return "Pending restart work was not cleared."
    if result.success:
        return "The service action completed successfully."
    if not result.performed:
        return result.message
    return "Review diagnostic details below."


def _service_result_view(
    result: service_actions.ServiceActionResult,
) -> dict[str, object]:
    show_backend_message = not (result.action in {"start", "restart"} and result.success)
    return {
        "title": _operator_result_title(result),
        "message": _operator_result_message(result),
        "backend_success": _backend_success(result),
        "pending_restart_work_cleared": result.pending_restart_work_cleared,
        "pending_restart_work_warning": result.pending_restart_work_warning,
        "show_backend_message": show_backend_message,
    }


def _render_result(
    request: Request,
    current: CurrentSession,
    result: service_actions.ServiceActionResult,
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    form_csrf = get_form_csrf_token(request, current)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="service_result.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "result": result,
            "result_view": _service_result_view(result),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.post("/service/{action}", response_class=HTMLResponse)
def service_action(
    request: Request,
    action: str,
    csrf_token: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Run a controlled service action for the default instance."""
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

    normalized = service_actions.normalize_service_action(action)
    if not service_actions.is_supported_action(normalized):
        return PlainTextResponse(
            "Unknown service action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if service_actions.requires_confirmation(normalized) and confirm != normalized:
        return _render_result(
            request,
            current,
            service_actions.confirmation_failure(normalized),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = service_actions.run_service_action_and_audit(
            normalized,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            instance=paths.DEFAULT_INSTANCE_NAME,
            db_path=current.config.db_path,
        )
    except service_actions.ServiceActionError:
        return PlainTextResponse(
            "Unknown service action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return _render_result(request, current, result)
