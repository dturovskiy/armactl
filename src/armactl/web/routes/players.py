"""Player registry pages for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

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
from armactl.web.services import (
    player_current_refresh,
    player_live_session_scan,
    player_log_collection,
    player_log_sessionization,
    player_session_details,
    player_session_maintenance,
)

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
            "refresh_notice": _current_refresh_notice_from_query(request),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _current_players_json_payload(
    page: players_page_model.CurrentPlayersPage,
) -> dict[str, object]:
    return {
        "instance": page.instance,
        "available": page.available,
        "source": page.source,
        "status": page.status,
        "error": page.error,
        "refresh_error": page.refresh_error,
        "collected_at": page.collected_at,
        "updated_at": page.updated_at,
        "age_seconds": page.age_seconds,
        "cache_age_seconds": page.age_seconds,
        "is_stale": page.is_stale,
        "freshness": page.freshness,
        "cache_status": page.cache_status,
        "observed_count": page.observed_count,
        "total_count": page.total_count,
        "filtered_count": page.filtered_count,
        "count_source": page.count_source,
        "roster_available": page.roster_available,
        "roster_configured": page.roster_configured,
        "stale_named_roster": page.stale_named_roster,
        "players": [
            {
                "display_name": player.display_name,
                "reliable_id": player.reliable_id,
                "source": player.source,
                "kills": player.kills,
                "deaths": player.deaths,
                "teamkills": player.teamkills,
                "faction": player.faction,
                "first_observed_at": player.first_observed_at,
                "stats_available": player.stats_available,
                "stats_source_label": player.stats_source_label,
                "stats_unavailable_reason": player.stats_unavailable_reason,
                "stats_freshness_status": player.stats_freshness_status,
                "stats_freshness_at": player.stats_freshness_at,
                "stats_window_started_at": player.stats_window_started_at,
                "stats_window_ended_at": player.stats_window_ended_at,
                "stats_reconnect_merged": player.stats_reconnect_merged,
            }
            for player in page.players
        ],
    }


def _render_known_players_page(
    request: Request,
    current: CurrentSession,
    *,
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
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _current_refresh_notice_from_query(request: Request) -> dict[str, str] | None:
    notice_type = request.query_params.get("refresh_current", "")
    if notice_type == "queued":
        return {
            "level": "success",
            "title": "Current player refresh queued.",
            "message": "Known players will be updated from the current roster in the background.",
        }
    if notice_type == "active":
        return {
            "level": "warning",
            "title": "Current player refresh already running.",
            "message": (
                "No duplicate job was created; the active refresh is already queued or running."
            ),
        }
    return None


def _collection_notice_from_query(request: Request) -> dict[str, str] | None:
    notice_type = request.query_params.get("log_collection", "")
    if notice_type == "queued":
        return {
            "level": "success",
            "title": "Player log collection queued.",
            "message": "Allowlisted server logs will be scanned in the background.",
        }
    if notice_type == "active":
        return {
            "level": "warning",
            "title": "Player log collection already running.",
            "message": (
                "No duplicate job was created; the active collection is already queued or running."
            ),
        }
    return None


_SESSION_JOB_NOTICES = {
    "live-scan": {
        "queued": {
            "level": "success",
            "title": "Live player session scan queued.",
            "message": "Current roster will be scanned once in the background.",
        },
        "active": {
            "level": "warning",
            "title": "Live player session scan already running.",
            "message": (
                "No duplicate job was created; the active scan is already queued or running."
            ),
        },
        "failed": {
            "level": "error",
            "title": "Live player session scan was not queued.",
            "message": "Audit logging failed before the job could be queued.",
        },
    },
    "log-sessionization": {
        "queued": {
            "level": "success",
            "title": "Player log sessionization queued.",
            "message": "Stored player log events will be sessionized in the background.",
        },
        "active": {
            "level": "warning",
            "title": "Player log sessionization already running.",
            "message": (
                "No duplicate job was created; the active sessionization job "
                "is already queued or running."
            ),
        },
        "failed": {
            "level": "error",
            "title": "Player log sessionization was not queued.",
            "message": "Audit logging failed before the job could be queued.",
        },
    },
    "maintenance": {
        "queued": {
            "level": "success",
            "title": "Player session maintenance queued.",
            "message": "Stale-close and retention maintenance will run in the background.",
        },
        "active": {
            "level": "warning",
            "title": "Player session maintenance already running.",
            "message": (
                "No duplicate job was created; active session maintenance "
                "is already queued or running."
            ),
        },
        "failed": {
            "level": "error",
            "title": "Player session maintenance was not queued.",
            "message": "Audit logging failed before the job could be queued.",
        },
    },
}


def _player_session_job_notice_url(
    action: str,
    notice_type: str,
    *,
    job_id: int | None = None,
) -> str:
    location = f"/players/sessions?session_job={action}&job_status={notice_type}"
    if job_id is not None:
        location = f"{location}&job_id={job_id}"
    return location


def _session_job_notice_from_query(request: Request) -> dict[str, str] | None:
    action = request.query_params.get("session_job", "")
    notice_type = request.query_params.get("job_status", "")
    return _SESSION_JOB_NOTICES.get(action, {}).get(notice_type)


def _enqueue_session_operator_job(
    request: Request,
    csrf_token: str,
    *,
    action: str,
    enqueue,
) -> Response:
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
        result = enqueue(
            current.config.db_path,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
        )
    except (
        player_live_session_scan.PlayerLiveSessionScanActionAuditError,
        player_log_sessionization.PlayerLogSessionizationActionAuditError,
        player_session_maintenance.PlayerSessionMaintenanceActionAuditError,
    ):
        return RedirectResponse(
            _player_session_job_notice_url(action, "failed"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    notice_type = "queued" if result.created else "active"
    return RedirectResponse(
        _player_session_job_notice_url(
            action,
            notice_type,
            job_id=result.job.id,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


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
        mode=request.query_params.get("mode", ""),
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
            "mode": page.mode,
            "mode_options": page.mode_options,
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


def _render_player_sessions_page(
    request: Request,
    current: CurrentSession,
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()

    page = players_page_model.load_player_sessions_page(
        paths.DEFAULT_INSTANCE_NAME,
        data_root=current.config.data_root,
        query=request.query_params.get("q", ""),
        reliable_id=request.query_params.get("player_id", ""),
        status=request.query_params.get("status", ""),
        end_reason=request.query_params.get("end_reason", ""),
        source=request.query_params.get("source", ""),
        from_time=request.query_params.get("from", ""),
        to_time=request.query_params.get("to", ""),
        before_time=request.query_params.get("before_time", ""),
        before_session_id=request.query_params.get("before_session_id", ""),
        limit=request.query_params.get("limit", ""),
        web_db_path=current.config.db_path,
    )
    form_csrf = get_form_csrf_token(request, current)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="players_sessions.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "query": page.query,
            "player_id": page.reliable_id,
            "status": page.status,
            "end_reason": page.end_reason,
            "source": page.source,
            "from_time": page.from_time,
            "to_time": page.to_time,
            "limit": page.limit,
            "sessions": page.sessions,
            "session_job_notice": _session_job_notice_from_query(request),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _render_player_session_detail_page(
    request: Request,
    current: CurrentSession,
    session_id: str,
) -> Response:
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()

    page = players_page_model.load_player_session_detail_page(
        session_id,
        paths.DEFAULT_INSTANCE_NAME,
        data_root=current.config.data_root,
        query=request.query_params.get("q", ""),
        reliable_id=request.query_params.get("player_id", ""),
        status=request.query_params.get("status", ""),
        end_reason=request.query_params.get("end_reason", ""),
        source=request.query_params.get("source", ""),
        from_time=request.query_params.get("from", ""),
        to_time=request.query_params.get("to", ""),
        limit=request.query_params.get("limit", ""),
        before_time=request.query_params.get("before_time", ""),
        before_session_id=request.query_params.get("before_session_id", ""),
        timeline_limit=request.query_params.get("timeline_limit", ""),
        timeline_before_time=request.query_params.get(
            "timeline_before_time",
            "",
        ),
        timeline_before_event_id=request.query_params.get(
            "timeline_before_event_id",
            "",
        ),
    )
    response_status = status.HTTP_200_OK
    if page.status in {
        player_session_details.PLAYER_SESSION_DETAIL_STATUS_INVALID_ID,
        player_session_details.PLAYER_SESSION_DETAIL_STATUS_NOT_FOUND,
    }:
        response_status = status.HTTP_404_NOT_FOUND
    form_csrf = get_form_csrf_token(request, current)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="player_session_detail.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "detail": page.detail,
            "timeline_rows": page.timeline_rows,
        },
        status_code=response_status,
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


@router.get("/players/sessions", response_class=HTMLResponse)
def player_sessions_page(request: Request) -> Response:
    """Render stored player sessions from the instance registry."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_player_sessions_page(request, current)


@router.get("/players/sessions/{session_id}", response_class=HTMLResponse)
def player_session_detail_page(request: Request, session_id: str) -> Response:
    """Render one stored player session and its bounded safe timeline."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_player_session_detail_page(request, current, session_id)


@router.get("/players", response_class=HTMLResponse)
def players_page(request: Request) -> Response:
    """Render the live current-player roster."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_current_players_page(request, current)


@router.get("/players/current.json")
def current_players_json(request: Request) -> Response:
    """Return cached current-player roster DTO for lightweight refreshes."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, PLAYERS_VIEW):
        return permission_denied_response()
    page = players_page_model.load_current_players_page(
        paths.DEFAULT_INSTANCE_NAME,
        data_root=current.config.data_root,
        query=request.query_params.get("player_search", ""),
    )
    return JSONResponse(_current_players_json_payload(page))


@router.get("/players/known", response_class=HTMLResponse)
def known_players_page(request: Request) -> Response:
    """Render known reliable players from the instance registry."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_known_players_page(request, current)


def _enqueue_current_player_refresh(request: Request, csrf_token: str) -> Response:
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
        result = player_current_refresh.request_player_current_refresh_and_start(
            current.config.db_path,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            user_id=current.user.id,
        )
    except player_current_refresh.PlayerCurrentRefreshActionAuditError as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    notice = "queued" if result.created else "active"
    return RedirectResponse(
        f"/players?refresh_current={notice}&job_id={result.job.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/players/refresh-current", response_class=HTMLResponse)
def refresh_current_players_page(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Queue current-roster registry refresh without running it in the request."""
    return _enqueue_current_player_refresh(request, csrf_token)


@router.post("/players/refresh", response_class=HTMLResponse)
def refresh_players_page(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Compatibility alias for current-roster refresh enqueue."""
    return _enqueue_current_player_refresh(request, csrf_token)


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


@router.post("/players/sessions/scan-live", response_class=HTMLResponse)
def scan_live_player_sessions(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Queue one explicit live player-session scan."""
    return _enqueue_session_operator_job(
        request,
        csrf_token,
        action="live-scan",
        enqueue=player_live_session_scan.request_player_live_session_scan_and_start,
    )


@router.post("/players/sessions/sessionize-log-events", response_class=HTMLResponse)
def sessionize_player_log_events(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Queue stored-log player-session sessionization."""
    return _enqueue_session_operator_job(
        request,
        csrf_token,
        action="log-sessionization",
        enqueue=player_log_sessionization.request_player_log_sessionization_and_start,
    )


@router.post("/players/sessions/maintenance", response_class=HTMLResponse)
def maintain_player_sessions(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Queue explicit player-session stale-close and retention maintenance."""
    return _enqueue_session_operator_job(
        request,
        csrf_token,
        action="maintenance",
        enqueue=player_session_maintenance.request_player_session_maintenance_and_start,
    )
