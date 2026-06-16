"""Player registry pages for armactl web."""

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
from armactl.web.auth.permissions import PLAYERS_VIEW
from armactl.web.services import player_moderation, player_registry
from armactl.web.services.audit import AuditLogError, append_audit_event

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _registry_path(config: object) -> object:
    return player_registry.player_registry_db_path(
        paths.DEFAULT_INSTANCE_NAME,
        data_root=config.data_root,
    )


def _render_players_page(
    request: Request,
    current: CurrentSession,
    *,
    refresh_result: player_registry.PlayerSnapshotResult | None = None,
    audit_error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()

    query = request.query_params.get("player_search", "")
    db_path = _registry_path(current.config)
    form_csrf = get_form_csrf_token(request, current)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="players.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "query": query,
            "players": player_registry.list_known_players(db_path, query=query),
            "registry_path": db_path,
            "refresh_result": refresh_result,
            "audit_error": audit_error,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/players", response_class=HTMLResponse)
def players_page(request: Request) -> Response:
    """Render known reliable players from the instance registry."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_players_page(request, current)


@router.post("/players/refresh", response_class=HTMLResponse)
def refresh_players_page(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Record reliable current players from the safe moderation view."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    panel = player_moderation.load_player_moderation_panel(paths.DEFAULT_INSTANCE_NAME)
    db_path = _registry_path(current.config)
    result = player_registry.record_current_players_snapshot(db_path, panel.players)
    audit_error = ""
    try:
        append_audit_event(
            current.config.audit_log_path,
            username=current.user.username,
            action="players.refresh",
            instance=paths.DEFAULT_INSTANCE_NAME,
            target=str(db_path),
            success=True,
            message="Player registry refreshed.",
            exit_code=0,
            details={
                "stored_count": str(result.stored_count),
                "ignored_count": str(result.ignored_count),
            },
        )
    except AuditLogError as error:
        audit_error = str(error)
    return _render_players_page(
        request,
        current,
        refresh_result=result,
        audit_error=audit_error,
        status_code=(
            status.HTTP_200_OK if not audit_error else status.HTTP_500_INTERNAL_SERVER_ERROR
        ),
    )
