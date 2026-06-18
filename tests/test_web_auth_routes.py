"""Route tests for web login, logout, sessions, and auth cookies."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from web_route_helpers import (
    _client,
    _form_token,
    _login,
    _session_set_cookie,
    _set_cookie,
    _set_cookie_header,
)

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, LOGIN_CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.sessions import create_session, revoke_session, validate_session
from armactl.web.auth.setup import setup_owner_user
from armactl.web.runtime import ensure_web_runtime, save_web_runtime_config


def test_get_login_returns_form(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path), base_url="https://testserver")

    response = client.get("/login")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '<form method="post" action="/login"' in response.text
    assert 'name="username"' in response.text
    assert 'name="password"' in response.text
    assert 'name="csrf_token"' in response.text


def test_login_form_sets_httponly_login_csrf_cookie(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/login")
    header = _set_cookie_header(response, LOGIN_CSRF_COOKIE_NAME)

    assert response.status_code == 200
    assert response.cookies.get(LOGIN_CSRF_COOKIE_NAME) == _form_token(response.text)
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/login" in header
    assert "Secure" not in header


def test_owner_not_configured_login_state_is_controlled(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/login")

    assert response.status_code == 200
    assert "Web owner is not configured yet." in response.text
    assert "Traceback" not in response.text
    assert '<form method="post" action="/login"' not in response.text


def test_successful_login_sets_session_cookie_and_redirects(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner login password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))

    response = _login(client, "owner", password)
    session_cookie = response.cookies.get(SESSION_COOKIE_NAME)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert session_cookie
    assert validate_session(tmp_path / "web" / "web.db", session_cookie) is not None
    assert password not in response.text


def test_wrong_credentials_do_not_set_cookie_and_show_controlled_error(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner login password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))

    response = _login(client, "owner", "wrong password")

    assert response.status_code == 401
    assert SESSION_COOKIE_NAME not in response.cookies
    assert "Username or password is invalid." in response.text
    assert "owner login password" not in response.text
    assert "wrong password" not in response.text
    assert "Traceback" not in response.text


def test_login_without_csrf_fails_safely(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner login password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))

    response = client.post(
        "/login",
        data={"username": "owner", "password": password},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert SESSION_COOKIE_NAME not in response.cookies
    assert "Login form expired. Try again." in response.text
    assert password not in response.text
    assert "Traceback" not in response.text


def test_invalid_expired_or_revoked_session_cookie_redirects_to_login(
    tmp_path: Path,
):
    from armactl.web.app import create_app

    user = setup_owner_user(tmp_path, "owner", "owner password").user
    db_path = tmp_path / "web" / "web.db"
    expired_session = create_session(db_path, user.id)
    revoked_session = create_session(db_path, user.id)
    revoke_session(db_path, revoked_session.session.id)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_sessions
            SET expires_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (expired_session.session.id,),
        )

    client = _client(create_app(data_root=tmp_path))
    _set_cookie(client, SESSION_COOKIE_NAME, "not-a-valid-session")
    invalid_response = client.get("/dashboard", follow_redirects=False)

    client = _client(create_app(data_root=tmp_path))
    _set_cookie(client, SESSION_COOKIE_NAME, expired_session.token)
    expired_response = client.get("/dashboard", follow_redirects=False)

    client = _client(create_app(data_root=tmp_path))
    _set_cookie(client, SESSION_COOKIE_NAME, revoked_session.token)
    revoked_response = client.get("/dashboard", follow_redirects=False)

    assert invalid_response.status_code == 303
    assert invalid_response.headers["location"] == "/login"
    assert expired_response.status_code == 303
    assert expired_response.headers["location"] == "/login"
    assert revoked_response.status_code == 303
    assert revoked_response.headers["location"] == "/login"


def test_logout_with_valid_csrf_revokes_session_and_clears_cookie(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner logout password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)
    csrf_token = login_response.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token

    response = client.post(
        "/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert session_token
    assert validate_session(tmp_path / "web" / "web.db", session_token) is None
    assert "Max-Age=0" in _session_set_cookie(response)


def test_logout_without_or_wrong_csrf_fails_safely(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner logout password"
    user = setup_owner_user(tmp_path, "owner", password).user
    db_path = tmp_path / "web" / "web.db"
    session = create_session(db_path, user.id)
    client = _client(create_app(data_root=tmp_path))
    _set_cookie(client, SESSION_COOKIE_NAME, session.token)

    missing_response = client.post("/logout", data={}, follow_redirects=False)
    wrong_response = client.post(
        "/logout",
        data={"csrf_token": "wrong-token"},
        follow_redirects=False,
    )

    assert missing_response.status_code == 403
    assert wrong_response.status_code == 403
    assert "Invalid CSRF token." in missing_response.text
    assert "Traceback" not in wrong_response.text
    assert validate_session(db_path, session.token) is not None


def test_cookie_flags_follow_https_required(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner cookie password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))

    response = _login(client, "owner", password)
    header = _session_set_cookie(response)
    csrf_header = _set_cookie_header(response, CSRF_COOKIE_NAME)

    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Secure" not in header
    assert "HttpOnly" in csrf_header
    assert "SameSite=lax" in csrf_header
    assert "Secure" not in csrf_header


def test_cookie_flags_set_secure_when_https_required(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner secure cookie password"
    config = ensure_web_runtime(tmp_path)
    save_web_runtime_config(replace(config, https_required=True))
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path), base_url="https://testserver")

    form_response = client.get("/login")
    login_csrf_header = _set_cookie_header(form_response, LOGIN_CSRF_COOKIE_NAME)
    response = client.post(
        "/login",
        data={
            "username": "owner",
            "password": password,
            "csrf_token": _form_token(form_response.text),
        },
        follow_redirects=False,
    )
    header = _session_set_cookie(response)
    csrf_header = _set_cookie_header(response, CSRF_COOKIE_NAME)

    assert "HttpOnly" in login_csrf_header
    assert "SameSite=lax" in login_csrf_header
    assert "Secure" in login_csrf_header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Secure" in header
    assert "HttpOnly" in csrf_header
    assert "SameSite=lax" in csrf_header
    assert "Secure" in csrf_header


def test_repeated_wrong_login_attempts_return_rate_limit(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner login password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))

    for _ in range(5):
        response = _login(client, "owner", "wrong password")
        assert response.status_code == 401
        assert SESSION_COOKIE_NAME not in response.cookies

    limited_response = _login(client, "owner", "wrong password")

    assert limited_response.status_code == 429
    assert SESSION_COOKIE_NAME not in limited_response.cookies
    assert "Too many login attempts. Try again later." in limited_response.text
    assert "wrong password" not in limited_response.text
    assert password not in limited_response.text
    assert "Traceback" not in limited_response.text


def test_correct_password_is_blocked_during_login_lockout(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner login password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))

    for _ in range(5):
        _login(client, "owner", "wrong password")

    response = _login(client, "owner", password)

    assert response.status_code == 429
    assert SESSION_COOKIE_NAME not in response.cookies
    assert "Too many login attempts. Try again later." in response.text
    assert password not in response.text
    assert "Traceback" not in response.text
