# Server update management routes for armactl web.

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from armactl import paths, safe_update
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
from armactl.web.services import server_job_actions, server_versions
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.views.updates import build_updates_view

router = APIRouter()

_UPDATE_NOTICE_MESSAGES = {
    "check-queued": server_versions.SERVER_VERSION_MESSAGE_CHECK_QUEUED,
    "check-reused": "Recent update check reused.",
    "update-queued": server_job_actions.UPDATE_JOB_QUEUED_MESSAGE,
    "up-to-date": server_versions.SERVER_VERSION_MESSAGE_UP_TO_DATE,
    "server-running": server_job_actions.STOP_RUNNING_SERVER_UPDATE_MESSAGE,
    "update-unavailable": "Run a build check before updating.",
    "profile-queued": "Profile operation queued.",
    "profile-test-queued": "Profile compatibility test queued.",
    "policy-enabled": "Automatic vanilla fallback enabled.",
    "policy-disabled": "Automatic vanilla fallback disabled.",
}


def _updates_redirect(notice: str = "", *, return_to: str = "") -> RedirectResponse:
    target = "/dashboard" if return_to == "dashboard" else "/updates"
    if notice in _UPDATE_NOTICE_MESSAGES:
        target = f"{target}?notice={notice}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def _update_notice(request: Request) -> str:
    notice_key = str(request.query_params.get("notice") or "")
    return _UPDATE_NOTICE_MESSAGES.get(notice_key, "")


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
        action_notice=_update_notice(request),
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
        job = server_job_actions.request_server_update_check_and_start(
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
    return _updates_redirect("check-queued" if job is not None else "check-reused")


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
        return _updates_redirect("update-queued")
    if result.status == server_job_actions.SERVER_UPDATE_ACTION_UP_TO_DATE:
        return _updates_redirect("up-to-date")
    if result.status == server_job_actions.SERVER_UPDATE_ACTION_BLOCKED:
        return _updates_redirect("server-running")
    return _updates_redirect("update-unavailable")


def _profile_job_response(
    current: CurrentSession,
    *,
    action: str,
    name: str = "",
    return_to: str = "",
) -> Response:
    try:
        server_job_actions.request_server_profile_action_and_start(
            current.config.db_path,
            action=action,
            name=name,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
        )
    except server_job_actions.ServerJobActionError as exc:
        return PlainTextResponse(str(exc), status_code=status.HTTP_400_BAD_REQUEST)
    except server_job_actions.ServerJobAuditError as exc:
        return PlainTextResponse(str(exc), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    notice = (
        "profile-test-queued"
        if action in {"test", "test-parked"}
        else "profile-queued"
    )
    return _updates_redirect(notice, return_to=return_to)


@router.post("/updates/vanilla")
def activate_vanilla_profile(
    request: Request,
    csrf_token: str = Form(default=""),
    return_to: str = Form(default=""),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current
    return _profile_job_response(current, action="vanilla", return_to=return_to)


@router.post("/updates/retry-modded")
def retry_modded_profile(
    request: Request,
    csrf_token: str = Form(default=""),
    return_to: str = Form(default=""),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current
    return _profile_job_response(current, action="retry-modded", return_to=return_to)


@router.post("/updates/profile/switch")
def switch_named_profile(
    request: Request,
    csrf_token: str = Form(default=""),
    profile_name: str = Form(default=""),
    return_to: str = Form(default=""),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current
    return _profile_job_response(
        current,
        action="switch",
        name=profile_name,
        return_to=return_to,
    )


@router.post("/updates/profile/test")
def test_profile(
    request: Request,
    csrf_token: str = Form(default=""),
    profile_name: str = Form(default=""),
    profile_source: str = Form(default="named"),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current
    if profile_source == "parked":
        return _profile_job_response(current, action="test-parked")
    if profile_source != "named":
        return PlainTextResponse(
            "Profile test source is invalid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _profile_job_response(current, action="test", name=profile_name)


@router.post("/updates/profile/select")
def select_profile(
    request: Request,
    csrf_token: str = Form(default=""),
    profile_selection: str = Form(default=""),
    return_to: str = Form(default=""),
) -> Response:
    """Dispatch the compact dashboard selector to an explicit profile action."""
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current

    selection = profile_selection.strip()
    if selection == "vanilla":
        return _profile_job_response(current, action="vanilla", return_to=return_to)
    if selection == "retry-modded":
        return _profile_job_response(
            current,
            action="retry-modded",
            return_to=return_to,
        )
    if selection.startswith("profile:"):
        try:
            profile_name = safe_update.validate_profile_name(
                selection.removeprefix("profile:")
            )
        except safe_update.UpdateProfileError:
            profile_name = ""
        if profile_name:
            return _profile_job_response(
                current,
                action="switch",
                name=profile_name,
                return_to=return_to,
            )
    return PlainTextResponse(
        "Profile selection is invalid.",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@router.post("/updates/profile/create")
def create_named_profile(
    request: Request,
    csrf_token: str = Form(default=""),
    profile_name: str = Form(default=""),
    profile_type: str = Form(default="vanilla"),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current
    action = "create-vanilla" if profile_type == "vanilla" else "create"
    if profile_type not in {"vanilla", "current"}:
        return PlainTextResponse(
            "Profile type is invalid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _profile_job_response(current, action=action, name=profile_name)


@router.post("/updates/auto-fallback")
def set_auto_fallback(
    request: Request,
    csrf_token: str = Form(default=""),
    setting: str = Form(default=""),
    return_to: str = Form(default=""),
) -> Response:
    current = _require_update_action(request, csrf_token)
    if isinstance(current, Response):
        return current
    if setting not in {"on", "off"}:
        return PlainTextResponse(
            "Automatic fallback setting is invalid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    enabled = setting == "on"
    install_dir = paths.server_dir(
        paths.DEFAULT_INSTANCE_NAME,
        current.config.data_root,
    )
    config_path = paths.config_file(
        paths.DEFAULT_INSTANCE_NAME,
        current.config.data_root,
    )
    try:
        append_audit_event(
            current.config.audit_log_path,
            username=current.user.username,
            action="server-update.auto-fallback.set",
            instance=paths.DEFAULT_INSTANCE_NAME,
            target="automatic-vanilla-fallback",
            success=True,
            message="Automatic vanilla fallback policy change requested.",
            exit_code=0,
            details={"phase": "intent", "enabled": str(enabled).lower()},
        )
        safe_update.set_automatic_vanilla_fallback(
            install_dir,
            config_path,
            enabled=enabled,
        )
        append_audit_event(
            current.config.audit_log_path,
            username=current.user.username,
            action="server-update.auto-fallback.set",
            instance=paths.DEFAULT_INSTANCE_NAME,
            target="automatic-vanilla-fallback",
            success=True,
            message="Automatic vanilla fallback policy updated.",
            exit_code=0,
            details={"phase": "outcome", "enabled": str(enabled).lower()},
        )
    except (AuditLogError, safe_update.SafeUpdateError) as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return _updates_redirect(
        "policy-enabled" if enabled else "policy-disabled",
        return_to=return_to,
    )
