"""POST-only web preference routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from armactl.web.auth.csrf import validate_csrf_token
from armactl.web.auth.dependencies import get_current_session, get_web_runtime_config
from armactl.web.i18n import (
    DEFAULT_THEME,
    normalize_language,
    normalize_theme,
    resolve_language,
    resolve_theme,
    set_language_cookie,
    set_theme_cookie,
)

router = APIRouter()


def _safe_redirect_path(raw_next: str, *, authenticated: bool) -> str:
    value = raw_next.strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/dashboard" if authenticated else "/login"


def _validate_authenticated_csrf(
    request: Request,
    csrf_token: str,
) -> tuple[bool, bool]:
    current = get_current_session(request)
    if current is None:
        return True, False
    return (
        validate_csrf_token(current.config.db_path, current.session.id, csrf_token),
        True,
    )


def _wants_async_response(request: Request) -> bool:
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if requested_with == "fetch":
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept


@router.post("/preferences/language")
def set_language_preference(
    request: Request,
    language: str = Form(default=""),
    csrf_token: str = Form(default=""),
    next: str = Form(default=""),
) -> Response:
    """Set a web-owned language preference cookie."""
    csrf_ok, authenticated = _validate_authenticated_csrf(request, csrf_token)
    if not csrf_ok:
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    config = get_web_runtime_config(request)
    normalized = normalize_language(language) or resolve_language(request)
    if _wants_async_response(request):
        response = JSONResponse({"language": normalized})
    else:
        response = RedirectResponse(
            _safe_redirect_path(next, authenticated=authenticated),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    set_language_cookie(response, normalized, config)
    return response


@router.post("/preferences/theme")
def set_theme_preference(
    request: Request,
    theme: str = Form(default=""),
    csrf_token: str = Form(default=""),
    next: str = Form(default=""),
) -> Response:
    """Set a web-owned light/dark theme preference cookie."""
    csrf_ok, authenticated = _validate_authenticated_csrf(request, csrf_token)
    if not csrf_ok and not _wants_async_response(request):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    config = get_web_runtime_config(request)
    normalized = normalize_theme(theme) or resolve_theme(request) or DEFAULT_THEME
    if _wants_async_response(request):
        response = JSONResponse({"theme": normalized})
    else:
        response = RedirectResponse(
            _safe_redirect_path(next, authenticated=authenticated),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    set_theme_cookie(response, normalized, config)
    return response
