"""Login and logout routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from armactl.web.auth.cookies import (
    clear_csrf_cookie,
    clear_login_csrf_cookie,
    clear_session_cookie,
    set_csrf_cookie,
    set_login_csrf_cookie,
    set_session_cookie,
    validate_login_csrf,
)
from armactl.web.auth.csrf import create_csrf_token, validate_csrf_token
from armactl.web.auth.dependencies import get_current_session, get_web_runtime_config
from armactl.web.auth.models import InvalidAuthInputError, WebAuthError
from armactl.web.auth.rate_limit import (
    check_login_allowed,
    clear_login_failures,
    record_login_failure,
)
from armactl.web.auth.sessions import create_session, revoke_session
from armactl.web.auth.setup import owner_user_exists
from armactl.web.auth.tokens import generate_token
from armactl.web.auth.users import get_user_by_username, verify_user_password

router = APIRouter()

GENERIC_LOGIN_ERROR = "Username or password is invalid."
TOO_MANY_LOGIN_ATTEMPTS_ERROR = "Too many login attempts. Try again later."
OWNER_NOT_CONFIGURED_MESSAGE = "Web owner is not configured yet."


def _login_template(
    request: Request,
    *,
    error: str | None = None,
    username: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    config = get_web_runtime_config(request)
    owner_configured = owner_user_exists(config.db_path)
    login_csrf_token = generate_token() if owner_configured else ""
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "error": error,
            "username": username,
            "owner_configured": owner_configured,
            "owner_not_configured_message": OWNER_NOT_CONFIGURED_MESSAGE,
            "login_csrf_token": login_csrf_token,
        },
        status_code=status_code,
    )

    if owner_configured:
        set_login_csrf_cookie(response, login_csrf_token, config)

    return response


def _redirect_to_login(config) -> RedirectResponse:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    """Render the login form."""
    if get_current_session(request) is not None:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return _login_template(request)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    csrf_token: str = Form(default=""),
) -> Response:
    """Validate credentials and establish a web session."""
    config = get_web_runtime_config(request)
    if not owner_user_exists(config.db_path):
        return _login_template(
            request,
            error=OWNER_NOT_CONFIGURED_MESSAGE,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not validate_login_csrf(request, csrf_token, config):
        return _login_template(
            request,
            error="Login form expired. Try again.",
            username=username,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    client_ip = _client_ip(request)
    try:
        limit_status = check_login_allowed(
            config.db_path,
            config.session_secret,
            client_ip,
            username,
        )
    except WebAuthError:
        return _login_template(
            request,
            error="Login is unavailable.",
            username=username,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not limit_status.allowed:
        return _login_template(
            request,
            error=TOO_MANY_LOGIN_ATTEMPTS_ERROR,
            username=username,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        credentials_ok = verify_user_password(config.db_path, username, password)
    except InvalidAuthInputError:
        credentials_ok = False
    except WebAuthError:
        return _login_template(
            request,
            error="Login is unavailable.",
            username=username,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not credentials_ok:
        try:
            record_login_failure(
                config.db_path,
                config.session_secret,
                client_ip,
                username,
            )
        except WebAuthError:
            return _login_template(
                request,
                error="Login is unavailable.",
                username=username,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return _login_template(
            request,
            error=GENERIC_LOGIN_ERROR,
            username=username,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        user = get_user_by_username(config.db_path, username)
        if user is None:
            raise WebAuthError("Authenticated web user was not found.")
        clear_login_failures(
            config.db_path,
            config.session_secret,
            client_ip,
            username,
        )
        session = create_session(config.db_path, user.id)
        csrf = create_csrf_token(config.db_path, session.session.id)
    except WebAuthError:
        return _login_template(
            request,
            error="Login is unavailable.",
            username=username,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, session.token, config)
    set_csrf_cookie(response, csrf.token, config)
    clear_login_csrf_cookie(response, config)
    return response


@router.post("/logout")
def logout(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Revoke the current session and clear auth cookies."""
    config = get_web_runtime_config(request)
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(config)

    if not validate_csrf_token(config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    revoke_session(config.db_path, current.session.id)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response
