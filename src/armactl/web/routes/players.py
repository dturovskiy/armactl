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
from armactl.web.page_models import players as players_page_model
from armactl.web.services import player_actions, player_log_collection

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_current_players_page(
    request: Request,
    current: CurrentSession,
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()

    query = request.query_params.get("player_search", "")
    page = players_page_model.load_current_players_page(
        paths.DEFAULT_INSTANCE_NAME,
        data_root=current.config.data_root,
        query=query,
    )
    form_csrf = get_form_csrf_token(request, current)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="players.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "query": page.query,
            "players": page.players,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _render_known_players_page(
    request: Request,
    current: CurrentSession,
    *,
    refresh_result: player_actions.PlayerRefreshResult | None = None,
    audit_error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()

    query = request.query_params.get("player_search", "")
    page = players_page_model.load_player_registry_page(
        paths.DEFAULT_INSTANCE_NAME,
        data_root=current.config.data_root,
        query=query,
    )
    form_csrf = get_form_csrf_token(request, current)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="players_known.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "query": page.query,
            "players": page.players,
            "refresh_result": refresh_result,
            "audit_error": audit_error,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _collection_notice_from_query(request: Request) -> dict[str, str] | None:
    notice_type = request.query_params.get("log_collection", "")
    if notice_type == "queued":
        return {
            "title": "Player log collection queued.",
            "message": "Allowlisted server logs will be scanned in the background.",
        }
    if notice_type == "active":
        return {
            "title": "Player log collection already running.",
            "message": "An existing player log collection job is already queued or running.",
        }
    return None


def _render_player_history_page(
    request: Request,
    current: CurrentSession,
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()

    page = players_page_model.load_player_history_page(
        paths.DEFAULT_INSTANCE_NAME,
        data_root=current.config.data_root,
        query=request.query_params.get("q", ""),
        event_type=request.query_params.get("event_type", ""),
        reliable_id=request.query_params.get("player_id", ""),
        limit=request.query_params.get("limit", ""),
    )
    form_csrf = get_form_csrf_token(request, current)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="players_history.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "query": page.query,
            "event_type": page.event_type,
            "event_type_options": page.event_type_options,
            "event_type_labels": page.event_type_labels,
            "player_id": page.reliable_id,
            "limit": page.limit,
            "events": page.events,
            "collection_notice": _collection_notice_from_query(request),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/players/history", response_class=HTMLResponse)
def player_history_page(request: Request) -> Response:
    """Render stored player log events from the instance registry."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_player_history_page(request, current)


@router.get("/players", response_class=HTMLResponse)
def players_page(request: Request) -> Response:
    """Render the live current-player roster."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_current_players_page(request, current)


@router.get("/players/known", response_class=HTMLResponse)
def known_players_page(request: Request) -> Response:
    """Render known reliable players from the instance registry."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_known_players_page(request, current)


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

    result = player_actions.refresh_registry_and_audit(
        paths.DEFAULT_INSTANCE_NAME,
        data_root=current.config.data_root,
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
    )
    return _render_known_players_page(
        request,
        current,
        refresh_result=result,
        audit_error=result.audit_error,
        status_code=status.HTTP_200_OK if result.success else status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@router.post("/players/history/collect-logs", response_class=HTMLResponse)
def collect_player_history_logs(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Queue allowlisted player log collection without running it in the request."""
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

    try:
        result = player_log_collection.request_player_log_collection_and_start(
            current.config.db_path,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
        )
    except player_log_collection.PlayerLogCollectionActionAuditError as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    notice = "queued" if result.created else "active"
    return RedirectResponse(
        f"/players/history?log_collection={notice}&job_id={result.job.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
