"""Bot and community publisher management routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
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
from armactl.web.auth.permissions import BOT_MANAGE, BOT_VIEW
from armactl.web.page_models import bot as bot_page_model
from armactl.web.routes._common import redirect_to_login
from armactl.web.services import discord_stats_actions

router = APIRouter()


def _render_bot_page(
    request: Request,
    current: CurrentSession,
    *,
    discord_stats_result: discord_stats_actions.DiscordStatsSettingsResult
    | discord_stats_actions.DiscordStatsActionResult
    | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, BOT_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = bot_page_model.load_bot_page(paths.DEFAULT_INSTANCE_NAME)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="bot.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "can_manage_bot": require_permission(current, BOT_MANAGE),
            "discord_stats_result": discord_stats_result,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/bot", response_class=HTMLResponse)
def bot_page(request: Request) -> Response:
    """Render Telegram bot and Discord statistics details."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    return _render_bot_page(request, current)


@router.post("/bot/discord", response_class=HTMLResponse)
async def save_discord_stats_settings(request: Request) -> Response:
    """Save Discord read-only statistics publisher settings."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, BOT_MANAGE):
        return permission_denied_response()

    submitted_form = await request.form()
    csrf_token = str(submitted_form.get("csrf_token") or "")
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        interval_seconds = int(str(submitted_form.get("discord_interval_seconds") or ""))
    except ValueError:
        interval_seconds = -1

    result = discord_stats_actions.save_discord_stats_settings_and_audit(
        enabled=str(submitted_form.get("discord_enabled") or "") == "1",
        webhook_url=str(submitted_form.get("discord_webhook_url") or ""),
        interval_seconds=interval_seconds,
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
        instance=paths.DEFAULT_INSTANCE_NAME,
    )
    return _render_bot_page(
        request,
        current,
        discord_stats_result=result,
        status_code=status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST,
    )


@router.post("/bot/discord/action", response_class=HTMLResponse)
async def run_discord_stats_action(request: Request) -> Response:
    """Run a controlled Discord statistics publish/service action."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, BOT_MANAGE):
        return permission_denied_response()

    submitted_form = await request.form()
    csrf_token = str(submitted_form.get("csrf_token") or "")
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    result = discord_stats_actions.run_discord_stats_action_and_audit(
        action=str(submitted_form.get("discord_action") or ""),
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
        instance=paths.DEFAULT_INSTANCE_NAME,
    )
    return _render_bot_page(
        request,
        current,
        discord_stats_result=result,
        status_code=status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST,
    )
