"""Cookie helpers for web authentication."""

from __future__ import annotations

from fastapi import Request
from starlette.responses import Response

from armactl.web.auth.csrf import DEFAULT_CSRF_TTL_SECONDS
from armactl.web.auth.sessions import DEFAULT_SESSION_TTL_SECONDS
from armactl.web.auth.tokens import digest_token, generate_token, token_digest_matches
from armactl.web.runtime import WebRuntimeConfig

SESSION_COOKIE_NAME = "armactl_web_session"
CSRF_COOKIE_NAME = "armactl_web_csrf"
LOGIN_CSRF_COOKIE_NAME = "armactl_web_login_csrf"
COOKIE_SAMESITE = "lax"
LOGIN_CSRF_TTL_SECONDS = 10 * 60


def _cookie_secure(config: WebRuntimeConfig) -> bool:
    return config.https_required


def read_session_token(request: Request) -> str | None:
    """Read the raw session token from the request cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return token


def set_session_cookie(response: Response, token: str, config: WebRuntimeConfig) -> None:
    """Attach a private session cookie to a response."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=DEFAULT_SESSION_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def clear_session_cookie(response: Response, config: WebRuntimeConfig) -> None:
    """Clear the session cookie."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(config),
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )


def read_csrf_token(request: Request) -> str | None:
    """Read the raw authenticated CSRF token from the request cookie."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        return None
    return token


def set_csrf_cookie(response: Response, token: str, config: WebRuntimeConfig) -> None:
    """Attach a CSRF cookie used to render authenticated forms."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=DEFAULT_CSRF_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def clear_csrf_cookie(response: Response, config: WebRuntimeConfig) -> None:
    """Clear the authenticated CSRF cookie."""
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(config),
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )


def create_login_csrf_token(response: Response, config: WebRuntimeConfig) -> str:
    """Create a pre-auth login CSRF token and set its HttpOnly cookie."""
    token = generate_token()
    set_login_csrf_cookie(response, token, config)
    return token


def set_login_csrf_cookie(
    response: Response,
    token: str,
    config: WebRuntimeConfig,
) -> None:
    """Attach the pre-auth login CSRF cookie."""
    response.set_cookie(
        key=LOGIN_CSRF_COOKIE_NAME,
        value=token,
        max_age=LOGIN_CSRF_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite=COOKIE_SAMESITE,
        path="/login",
    )


def clear_login_csrf_cookie(response: Response, config: WebRuntimeConfig) -> None:
    """Clear the pre-auth login CSRF cookie."""
    response.delete_cookie(
        key=LOGIN_CSRF_COOKIE_NAME,
        path="/login",
        secure=_cookie_secure(config),
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )


def validate_login_csrf(request: Request, submitted_token: str) -> bool:
    """Validate the pre-auth login CSRF double-submit token."""
    cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    cookie_digest = digest_token(cookie_token)
    submitted_digest = digest_token(submitted_token)
    if cookie_digest is None or submitted_digest is None:
        return False
    return token_digest_matches(cookie_digest, submitted_digest)
