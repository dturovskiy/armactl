"""Restart schedule management routes for armactl web."""

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
from armactl.web.auth.permissions import SCHEDULE_MANAGE, SCHEDULE_VIEW
from armactl.web.facade import load_schedule_page
from armactl.web.services import pending_work, schedule_actions

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_schedule(
    request: Request,
    current: CurrentSession,
    *,
    result: schedule_actions.ScheduleActionResult | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, SCHEDULE_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = load_schedule_page(paths.DEFAULT_INSTANCE_NAME)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="schedule.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "result": result,
            "can_manage_schedule": require_permission(current, SCHEDULE_MANAGE),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _run_schedule_action(
    request: Request,
    *,
    action: str,
    csrf_token: str,
    schedule_value: str = "",
    confirm: str = "",
) -> Response:
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, SCHEDULE_MANAGE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    normalized = schedule_actions.normalize_schedule_action(action)
    if not schedule_actions.is_supported_action(normalized):
        return PlainTextResponse(
            "Unknown schedule action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if schedule_actions.requires_confirmation(normalized) and confirm != normalized:
        return _render_schedule(
            request,
            current,
            result=schedule_actions.confirmation_failure(normalized),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = schedule_actions.run_schedule_action_and_audit(
            normalized,
            schedule_value=schedule_value,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            instance=paths.DEFAULT_INSTANCE_NAME,
        )
    except schedule_actions.ScheduleActionError:
        return PlainTextResponse(
            "Unknown schedule action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        result = schedule_actions.ScheduleActionResult(
            action=normalized,
            instance=paths.DEFAULT_INSTANCE_NAME,
            target="",
            success=False,
            message="Schedule action is unavailable.",
            exit_code=1,
            performed=False,
            schedule="",
            schedule_entries=[],
            audit_written=False,
        )
        return _render_schedule(
            request,
            current,
            result=result,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if normalized == schedule_actions.ACTION_RESTART_NOW and result.success and result.performed:
        pending_work.clear_restart_pending_work(
            current.config.db_path,
            instance=paths.DEFAULT_INSTANCE_NAME,
        )

    result_status = status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST
    return _render_schedule(request, current, result=result, status_code=result_status)


@router.get("/schedule", response_class=HTMLResponse)
def schedule_page(request: Request) -> Response:
    """Render restart timer status and schedule controls."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    return _render_schedule(request, current)


@router.post("/schedule/set", response_class=HTMLResponse)
async def set_schedule(request: Request) -> Response:
    """Set the restart timer OnCalendar schedule."""
    form = await request.form()
    csrf_token = str(form.get("csrf_token") or "")
    schedule_times = [
        str(value).strip()
        for value in form.getlist("schedule_time")
        if str(value).strip()
    ]
    schedule = ", ".join(schedule_times) if schedule_times else str(form.get("schedule") or "")
    return _run_schedule_action(
        request,
        action=schedule_actions.ACTION_SET_SCHEDULE,
        csrf_token=csrf_token,
        schedule_value=schedule,
    )


@router.post("/schedule/enable", response_class=HTMLResponse)
def enable_schedule(request: Request, csrf_token: str = Form(default="")) -> Response:
    """Enable the restart timer."""
    return _run_schedule_action(
        request,
        action=schedule_actions.ACTION_ENABLE_TIMER,
        csrf_token=csrf_token,
    )


@router.post("/schedule/disable", response_class=HTMLResponse)
def disable_schedule(request: Request, csrf_token: str = Form(default="")) -> Response:
    """Disable the restart timer."""
    return _run_schedule_action(
        request,
        action=schedule_actions.ACTION_DISABLE_TIMER,
        csrf_token=csrf_token,
    )


@router.post("/schedule/autostart/enable", response_class=HTMLResponse)
def enable_game_autostart(request: Request, csrf_token: str = Form(default="")) -> Response:
    """Enable game server autostart after VM boot."""
    return _run_schedule_action(
        request,
        action=schedule_actions.ACTION_ENABLE_GAME_AUTOSTART,
        csrf_token=csrf_token,
    )


@router.post("/schedule/autostart/disable", response_class=HTMLResponse)
def disable_game_autostart(
    request: Request,
    csrf_token: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Disable game server autostart after VM boot."""
    return _run_schedule_action(
        request,
        action=schedule_actions.ACTION_DISABLE_GAME_AUTOSTART,
        csrf_token=csrf_token,
        confirm=confirm,
    )


@router.post("/schedule/restart-now", response_class=HTMLResponse)
def restart_now(
    request: Request,
    csrf_token: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Run the restart helper service now without host reboot controls."""
    return _run_schedule_action(
        request,
        action=schedule_actions.ACTION_RESTART_NOW,
        csrf_token=csrf_token,
        confirm=confirm,
    )
