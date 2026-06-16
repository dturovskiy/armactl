"""Read-only management detail pages and safe config edits for armactl web."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
from armactl.web.auth.permissions import (
    ADMINS_MANAGE,
    ADMINS_VIEW,
    BOT_VIEW,
    CONFIG_VIEW,
    MODS_MANAGE,
    MODS_VIEW,
    SETTINGS_MANAGE,
)
from armactl.web.facade import (
    load_admins_page,
    load_bot_page,
    load_config_page,
    load_mods_page,
)
from armactl.web.services import (
    admin_actions,
    config_edit,
    mod_actions,
    pending_restart,
    player_moderation,
)

router = APIRouter()
PageLoader = Callable[[str], dict[str, Any]]


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_page(
    request: Request,
    current: CurrentSession,
    *,
    permission: str,
    template: str,
    loader: PageLoader,
) -> Response:
    if not require_permission(current, permission):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = loader(paths.DEFAULT_INSTANCE_NAME)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
        },
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _render_config_page(
    request: Request,
    current: CurrentSession,
    *,
    saved: bool = False,
    save_error: str = "",
    unchanged: bool = False,
    audit_error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, CONFIG_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = load_config_page(paths.DEFAULT_INSTANCE_NAME)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "can_edit_config": require_permission(current, SETTINGS_MANAGE),
            "config_saved": saved,
            "config_save_error": save_error,
            "config_unchanged": unchanged,
            "config_audit_error": audit_error,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _render_mods_page(
    request: Request,
    current: CurrentSession,
    *,
    result: mod_actions.ModActionResult | None = None,
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
            "can_manage_mods": require_permission(current, MODS_MANAGE),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _render_admins_page(
    request: Request,
    current: CurrentSession,
    *,
    result: admin_actions.AdminActionResult | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, ADMINS_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = load_admins_page(paths.DEFAULT_INSTANCE_NAME)
    player_panel = player_moderation.load_player_moderation_panel(
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
            "can_manage_admins": require_permission(current, ADMINS_MANAGE),
            "player_panel": player_panel,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _mark_pending_restart_for_result(
    current: CurrentSession,
    *,
    reason: str,
    source_action: str,
    details: object = "",
) -> None:
    pending_restart.mark_pending_restart(
        current.config.db_path,
        instance=paths.DEFAULT_INSTANCE_NAME,
        reason=reason,
        source_action=source_action,
        username=current.user.username,
        details=details,
    )


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
        return _redirect_to_login(request)
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
    except Exception:
        result = mod_actions.ModActionResult(
            action=action,
            instance=paths.DEFAULT_INSTANCE_NAME,
            target="",
            success=False,
            changed=False,
            message="Mod action is unavailable.",
            exit_code=1,
            audit_written=False,
        )
        return _render_mods_page(
            request,
            current,
            result=result,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if result.success and result.changed:
        _mark_pending_restart_for_result(
            current,
            reason=pending_restart.REASON_MODS,
            source_action=result.action,
            details=result.target,
        )
    result_status = status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST
    return _render_mods_page(request, current, result=result, status_code=result_status)


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
        return _redirect_to_login(request)
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
        )
    except admin_actions.AdminActionError:
        return PlainTextResponse(
            "Unknown admin action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        result = admin_actions.AdminActionResult(
            action=action,
            instance=paths.DEFAULT_INSTANCE_NAME,
            target="",
            success=False,
            changed=False,
            message="Admin action is unavailable.",
            exit_code=1,
            audit_written=False,
        )
        return _render_admins_page(
            request,
            current,
            result=result,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if result.success and result.changed:
        _mark_pending_restart_for_result(
            current,
            reason=pending_restart.REASON_ADMINS,
            source_action=result.action,
            details=result.target,
        )
    result_status = status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST
    return _render_admins_page(request, current, result=result, status_code=result_status)


def _authenticated_page(
    request: Request,
    *,
    permission: str,
    template: str,
    loader: PageLoader,
) -> Response:
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_page(request, current, permission=permission, template=template, loader=loader)


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request) -> Response:
    """Render server config details and safe edit controls when permitted."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_config_page(
        request,
        current,
        saved=request.query_params.get("saved") == "1",
        unchanged=request.query_params.get("unchanged") == "1",
    )


@router.post("/config", response_class=HTMLResponse)
def save_config_page(
    request: Request,
    csrf_token: str = Form(default=""),
    name: str = Form(default=""),
    scenario_id: str = Form(default=""),
    max_players: str = Form(default=""),
    visible: str | None = Form(default=None),
    battleye: str | None = Form(default=None),
    server_max_view_distance: str = Form(default=""),
    server_min_grass_distance: str = Form(default=""),
) -> Response:
    """Save allowlisted basic config fields without restarting the server."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, SETTINGS_MANAGE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    form = {
        "name": name,
        "scenario_id": scenario_id,
        "max_players": max_players,
        "visible": visible,
        "battleye": battleye,
        "server_max_view_distance": server_max_view_distance,
        "server_min_grass_distance": server_min_grass_distance,
    }
    try:
        result = config_edit.save_default_config_and_audit(
            paths.DEFAULT_INSTANCE_NAME,
            form,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
        )
    except config_edit.ConfigEditError as error:
        return _render_config_page(
            request,
            current,
            save_error=str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except config_edit.ConfigAuditError as error:
        return _render_config_page(
            request,
            current,
            saved=True,
            audit_error=str(error),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not result.changed_fields:
        return RedirectResponse("/config?unchanged=1", status_code=status.HTTP_303_SEE_OTHER)
    pending_restart.mark_pending_restart(
        current.config.db_path,
        instance=paths.DEFAULT_INSTANCE_NAME,
        reason=pending_restart.REASON_CONFIG,
        source_action=config_edit.CONFIG_SAVE_ACTION,
        username=current.user.username,
        details=", ".join(result.changed_fields),
    )
    return RedirectResponse("/config?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/mods", response_class=HTMLResponse)
def mods_page(request: Request) -> Response:
    """Render mod details and safe edit controls when permitted."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
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
        return _redirect_to_login(request)
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


@router.get("/admins", response_class=HTMLResponse)
def admins_page(request: Request) -> Response:
    """Render game admin details and safe edit controls when permitted."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
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
        return _redirect_to_login(request)
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


@router.get("/bot", response_class=HTMLResponse)
def bot_page(request: Request) -> Response:
    """Render read-only Telegram bot details."""
    return _authenticated_page(
        request,
        permission=BOT_VIEW,
        template="bot.html",
        loader=load_bot_page,
    )
