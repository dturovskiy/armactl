"""Read-only management detail pages for armactl web."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

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
from armactl.web.auth.permissions import ADMINS_VIEW, BOT_VIEW, CONFIG_VIEW, MODS_VIEW
from armactl.web.facade import (
    load_admins_page,
    load_bot_page,
    load_config_page,
    load_mods_page,
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
    """Render read-only server config details."""
    return _authenticated_page(
        request,
        permission=CONFIG_VIEW,
        template="config.html",
        loader=load_config_page,
    )


@router.get("/mods", response_class=HTMLResponse)
def mods_page(request: Request) -> Response:
    """Render read-only active mod details."""
    return _authenticated_page(
        request,
        permission=MODS_VIEW,
        template="mods.html",
        loader=load_mods_page,
    )


@router.get("/admins", response_class=HTMLResponse)
def admins_page(request: Request) -> Response:
    """Render read-only game admin details."""
    return _authenticated_page(
        request,
        permission=ADMINS_VIEW,
        template="admins.html",
        loader=load_admins_page,
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
