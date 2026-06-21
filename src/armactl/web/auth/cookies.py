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
COOKIE_PREFIX = "armactl_web"
COOKIE_SAMESITE = "lax"
LOGIN_CSRF_TTL_SECONDS = 10 * 60


def _cookie_secure(config: WebRuntimeConfig) -> bool:
    return config.https_required


def _cookie_name(config: WebRuntimeConfig, suffix: str) -> str:
    return f"{COOKIE_PREFIX}_{config.cookie_namespace}_{suffix}"


def session_cookie_name(config: WebRuntimeConfig) -> str:
    """Return the runtime-scoped session cookie name."""
    return _cookie_name(config, "session")


def csrf_cookie_name(config: WebRuntimeConfig) -> str:
    """Return the runtime-scoped authenticated CSRF cookie name."""
    return _cookie_name(config, "csrf")


def login_csrf_cookie_name(config: WebRuntimeConfig) -> str:
    """Return the runtime-scoped pre-auth login CSRF cookie name."""
    return _cookie_name(config, "login_csrf")


def read_session_token(request: Request, config: WebRuntimeConfig) -> str | None:
    """Read the raw session token from the runtime-scoped request cookie."""
    token = request.cookies.get(session_cookie_name(config))
    if not token:
        return None
    return token


def set_session_cookie(response: Response, token: str, config: WebRuntimeConfig) -> None:
    """Attach a private session cookie to a response."""
    response.set_cookie(
        key=session_cookie_name(config),
        value=token,
        max_age=DEFAULT_SESSION_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def _delete_cookie(response: Response, config: WebRuntimeConfig, *, key: str, path: str) -> None:
    response.delete_cookie(
        key=key,
        path=path,
        secure=_cookie_secure(config),
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )


def clear_session_cookie(response: Response, config: WebRuntimeConfig) -> None:
    """Clear the runtime-scoped session cookie and the legacy shared cookie."""
    for key in dict.fromkeys((session_cookie_name(config), SESSION_COOKIE_NAME)):
        _delete_cookie(response, config, key=key, path="/")


def read_csrf_token(request: Request, config: WebRuntimeConfig) -> str | None:
    """Read the raw authenticated CSRF token from the runtime-scoped cookie."""
    token = request.cookies.get(csrf_cookie_name(config))
    if not token:
        return None
    return token


def set_csrf_cookie(response: Response, token: str, config: WebRuntimeConfig) -> None:
    """Attach a CSRF cookie used to render authenticated forms."""
    response.set_cookie(
        key=csrf_cookie_name(config),
        value=token,
        max_age=DEFAULT_CSRF_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def clear_csrf_cookie(response: Response, config: WebRuntimeConfig) -> None:
    """Clear the runtime-scoped authenticated CSRF cookie and legacy cookie."""
    for key in dict.fromkeys((csrf_cookie_name(config), CSRF_COOKIE_NAME)):
        _delete_cookie(response, config, key=key, path="/")


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
        key=login_csrf_cookie_name(config),
        value=token,
        max_age=LOGIN_CSRF_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite=COOKIE_SAMESITE,
        path="/login",
    )


def clear_login_csrf_cookie(response: Response, config: WebRuntimeConfig) -> None:
    """Clear the runtime-scoped pre-auth login CSRF cookie and legacy cookie."""
    for key in dict.fromkeys((login_csrf_cookie_name(config), LOGIN_CSRF_COOKIE_NAME)):
        _delete_cookie(response, config, key=key, path="/login")


def validate_login_csrf(
    request: Request,
    submitted_token: str,
    config: WebRuntimeConfig,
) -> bool:
    """Validate the pre-auth login CSRF double-submit token."""
    cookie_token = request.cookies.get(login_csrf_cookie_name(config))
    cookie_digest = digest_token(cookie_token)
    submitted_digest = digest_token(submitted_token)
    if cookie_digest is None or submitted_digest is None:
        return False
    return token_digest_matches(cookie_digest, submitted_digest)
