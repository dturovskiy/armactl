"""Read-only dashboard routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from armactl import paths
from armactl.web.auth.cookies import clear_csrf_cookie, clear_session_cookie, set_csrf_cookie
from armactl.web.auth.dependencies import (
    CurrentSession,
    get_current_session,
    get_form_csrf_token,
    get_web_runtime_config,
    permission_denied_response,
    require_permission,
)
from armactl.web.auth.permissions import (
    ACTIONS_RUN,
    ADMINS_VIEW,
    BOT_VIEW,
    CONFIG_VIEW,
    DASHBOARD_VIEW,
    FILES_READ,
    JOBS_VIEW,
    LOGS_VIEW,
    MODS_VIEW,
    PLAYERS_VIEW,
    SCHEDULE_VIEW,
    SERVER_UPDATE,
)
from armactl.web.i18n import resolve_language, translation_helpers
from armactl.web.jobs.store import list_active_jobs
from armactl.web.page_models import dashboard as dashboard_page_model
from armactl.web.page_models import updates as updates_page_model
from armactl.web.services.pending_work import list_pending_work_with_fallback
from armactl.web.views.dashboard import (
    build_dashboard_status_payload,
    build_dashboard_view,
)
from armactl.web.views.updates import build_updates_view

router = APIRouter()

_PROFILE_NOTICES = {
    "profile-queued": "Profile operation queued.",
    "policy-enabled": "Automatic vanilla fallback enabled.",
    "policy-disabled": "Automatic vanilla fallback disabled.",
}


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _dashboard_permission_flags(current: CurrentSession) -> dict[str, bool]:
    return {
        "can_run_actions": require_permission(current, ACTIONS_RUN),
        "can_view_config": require_permission(current, CONFIG_VIEW),
        "can_view_mods": require_permission(current, MODS_VIEW),
        "can_view_admins": require_permission(current, ADMINS_VIEW),
        "can_view_bot": require_permission(current, BOT_VIEW),
        "can_view_jobs": require_permission(current, JOBS_VIEW),
        "can_view_files": require_permission(current, FILES_READ),
        "can_view_logs": require_permission(current, LOGS_VIEW),
        "can_view_players": require_permission(current, PLAYERS_VIEW),
        "can_view_schedule": require_permission(current, SCHEDULE_VIEW),
        "can_update_server": require_permission(current, SERVER_UPDATE),
    }


def _load_dashboard_model(current: CurrentSession) -> tuple[dict, dict, dict[str, bool]]:
    snapshot = dashboard_page_model.load_dashboard_snapshot("default", web_config=current.config)
    permissions = _dashboard_permission_flags(current)
    dashboard = build_dashboard_view(snapshot, **permissions)
    return snapshot, dashboard, permissions


def _render_dashboard(request: Request, current: CurrentSession) -> Response:
    templates = request.app.state.templates
    try:
        snapshot, dashboard, permissions = _load_dashboard_model(current)
    except Exception as exc:  # noqa: BLE001 - dashboard rendering must degrade safely.
        return templates.TemplateResponse(
            request=request,
            name="dashboard_error.html",
            context={
                "error": {
                    "message": "Dashboard data is unavailable.",
                    "type": exc.__class__.__name__,
                }
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    form_csrf = get_form_csrf_token(request, current)
    can_view_jobs = permissions["can_view_jobs"]
    recent_jobs = list_active_jobs(current.config.db_path, limit=3) if can_view_jobs else []
    pending_work_items = list_pending_work_with_fallback(
        current.config.db_path, instance="default", limit=3
    )
    pending_work_detail_href = "/jobs" if can_view_jobs else ""
    profile_control = None
    config_path = paths.config_file("default", current.config.data_root)
    if permissions["can_update_server"] and config_path.is_file():
        try:
            raw_updates = updates_page_model.load_updates_page(
                "default",
                web_config=current.config,
            )
            profile_control = build_updates_view(
                raw_updates,
                can_update_server=True,
                action_notice=_PROFILE_NOTICES.get(
                    str(request.query_params.get("notice") or ""),
                    "",
                ),
            )
        except Exception:  # noqa: BLE001 - the main dashboard must remain available.
            profile_control = None
    response = templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "snapshot": snapshot,
            "dashboard": dashboard,
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "recent_jobs": recent_jobs,
            "pending_work_items": pending_work_items,
            "pending_work_detail_href": pending_work_detail_href,
            "profile_control": profile_control,
        },
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard_index(request: Request) -> Response:
    """Render the read-only dashboard."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, DASHBOARD_VIEW):
        return permission_denied_response()
    return _render_dashboard(request, current)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    """Render the read-only dashboard."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, DASHBOARD_VIEW):
        return permission_denied_response()
    return _render_dashboard(request, current)


@router.get("/dashboard/status.json")
def dashboard_status_json(request: Request) -> Response:
    """Return a small authenticated dashboard status DTO for live polling."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, DASHBOARD_VIEW):
        return permission_denied_response()

    try:
        snapshot, dashboard, _permissions = _load_dashboard_model(current)
    except Exception:  # noqa: BLE001 - dashboard polling must degrade safely.
        return JSONResponse(
            {"ok": False, "error": "Dashboard data is unavailable."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    language = resolve_language(request)
    translate = translation_helpers(language)["t"]
    return JSONResponse(build_dashboard_status_payload(snapshot, dashboard, translate=translate))
