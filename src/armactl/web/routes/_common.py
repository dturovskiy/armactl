"""Shared HTTP helpers for management domain routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request, status
from fastapi.responses import RedirectResponse, Response

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

PageLoader = Callable[[str], dict[str, Any]]


def redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def render_page(
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


def authenticated_page(
    request: Request,
    *,
    permission: str,
    template: str,
    loader: PageLoader,
) -> Response:
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    return render_page(request, current, permission=permission, template=template, loader=loader)
