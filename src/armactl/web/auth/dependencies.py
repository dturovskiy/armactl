"""Request helpers for authenticated web routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Request

from armactl.web.auth.cookies import read_csrf_token, read_session_token
from armactl.web.auth.csrf import create_csrf_token, validate_csrf_token
from armactl.web.auth.models import UserRecord
from armactl.web.auth.sessions import SessionRecord, get_session_user, validate_session
from armactl.web.runtime import WebRuntimeConfig, ensure_web_runtime


@dataclass(frozen=True)
class CurrentSession:
    """Authenticated request context without exposing the raw session token."""

    config: WebRuntimeConfig = field(repr=False)
    user: UserRecord
    session: SessionRecord
    session_token: str = field(repr=False)


@dataclass(frozen=True)
class CsrfTokenForResponse:
    """CSRF token for a rendered form."""

    token: str = field(repr=False)
    should_set_cookie: bool = False


def get_web_runtime_config(request: Request) -> WebRuntimeConfig:
    """Return the configured web runtime, creating runtime files when needed."""
    config = getattr(request.app.state, "web_runtime_config", None)
    if config is not None:
        return config

    data_root: Path | None = getattr(request.app.state, "web_data_root", None)
    config = ensure_web_runtime(data_root)
    request.app.state.web_runtime_config = config
    return config


def get_current_session(request: Request) -> CurrentSession | None:
    """Return the current authenticated session, if the request has one."""
    config = get_web_runtime_config(request)
    session_token = read_session_token(request)
    if session_token is None:
        return None

    session = validate_session(config.db_path, session_token)
    if session is None:
        return None

    user = get_session_user(config.db_path, session_token)
    if user is None:
        return None

    return CurrentSession(
        config=config,
        user=user,
        session=session,
        session_token=session_token,
    )


def get_form_csrf_token(request: Request, current: CurrentSession) -> CsrfTokenForResponse:
    """Return a valid CSRF token for authenticated forms."""
    cookie_token = read_csrf_token(request)
    if (
        cookie_token is not None
        and validate_csrf_token(current.config.db_path, current.session.id, cookie_token)
    ):
        return CsrfTokenForResponse(token=cookie_token)

    created = create_csrf_token(current.config.db_path, current.session.id)
    return CsrfTokenForResponse(token=created.token, should_set_cookie=True)
