"""Shared helpers for web route tests."""

from __future__ import annotations

import re
import warnings

from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME


def _client(
    app,
    base_url: str = "http://testserver",
    *,
    raise_server_exceptions: bool = True,
):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(
            app,
            base_url=base_url,
            raise_server_exceptions=raise_server_exceptions,
        )


def _form_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _login(client, username: str, password: str):
    form_response = client.get("/login")
    csrf_token = _form_token(form_response.text)
    return client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )


def _set_cookie(client, name: str, value: str) -> None:
    client.cookies.set(name, value, path="/")


def _set_cookie_header(response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"{name} cookie was not set")


def _session_set_cookie(response) -> str:
    return _set_cookie_header(response, SESSION_COOKIE_NAME)


def _action_csrf_token(client) -> str:
    dashboard_response = client.get("/dashboard")
    return _form_token(dashboard_response.text)


def _login_action_csrf_token(client, username: str, password: str) -> str:
    login_response = _login(client, username, password)
    csrf_token = login_response.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token
    return csrf_token


