"""Tests for the minimal FastAPI web app foundation."""

from __future__ import annotations

import builtins
import importlib
import json
import re
import sqlite3
import sys
import warnings
from dataclasses import replace
from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, LOGIN_CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.sessions import create_session, revoke_session, validate_session
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username
from armactl.web.runtime import ensure_web_runtime, save_web_runtime_config


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if _matches_prefix(module_name, prefixes):
            sys.modules.pop(module_name)


def _snapshot() -> dict:
    return {
        "instance": "default",
        "lifecycle": "running",
        "installed": True,
        "running": True,
        "overview": {
            "label": "running",
            "empty_state": False,
            "empty_title": "",
            "empty_message": "",
        },
        "state": {},
        "paths": {
            "instance_root": "/srv/armactl-data/default",
            "install_dir": "/srv/armactl-data/default/server",
            "config_path": "/srv/armactl-data/default/config/config.json",
            "config_dir": "/srv/armactl-data/default/config",
            "logs_dir": "/srv/armactl-data/default/config/logs",
            "service_name": "armareforger.service",
            "timer_name": "armareforger-restart.timer",
        },
        "service": {
            "available": True,
            "active": True,
            "enabled": True,
            "active_state": "active",
            "main_pid": 123,
        },
        "timer": {
            "available": True,
            "active": True,
            "enabled": True,
            "schedule": "*-*-* 06:00:00",
            "next_run": "Fri 2026-06-12 06:00:00 UTC",
        },
        "service_runtime": {
            "available": True,
            "cpu_text": "2.5%",
            "memory_text": "2.0 KiB",
        },
        "operational_status": {
            "available": True,
            "message": "Ready",
            "age_text": "12s",
        },
        "players": {
            "available": True,
            "current": 3,
            "max_players": 64,
            "count_text": "3 / 64",
            "count_source": "rcon",
            "a2s_available": False,
            "a2s_count": None,
            "roster_available": False,
            "warning": "",
        },
        "fps_metrics": {
            "available": True,
            "fps": 59.8,
            "fps_text": "59.8",
            "frame_avg_text": "16.7 ms",
            "frame_max_text": "24.0 ms",
            "age_text": "10s",
            "freshness": "fresh",
            "engine_memory_text": "512.0 KiB",
            "source": "/srv/armactl-data/default/config/logs/latest/console.log",
        },
        "host_metrics": {
            "available": True,
            "cpu_percent": 12.0,
            "cpu_text": "12.0%",
            "memory_text": "512 B / 1.0 KiB",
            "disk_text": "2.0 KiB / 4.0 KiB",
            "load_text": "0.10 / 0.20 / 0.30",
            "uptime_text": "1h 1m 1s",
        },
        "config": {
            "available": True,
            "server_name": "Mock Server",
            "scenario_id": "Scenario.conf",
            "max_players": 64,
            "visible_text": "yes",
            "battleye_text": "yes",
            "bind_port": 2001,
            "a2s_port": 17777,
            "rcon_port": 19999,
        },
        "mods": {
            "available": True,
            "count": 2,
            "preview_labels": ["Core Mod (mod-a)"],
            "remaining_count": 1,
        },
        "ports": {
            "available": True,
            "ports": {
                "game": {"port": 2001, "listening": True},
                "a2s": {"port": 17777, "listening": True},
                "rcon": {"port": 19999, "listening": False},
            },
        },
        "web": {
            "available": True,
            "bind_host": "127.0.0.1",
            "bind_port": 8765,
            "https_required_text": "no",
            "runtime_dir": "/tmp/armactl-web/web",
            "db_path": "/tmp/armactl-web/web/web.db",
            "audit_log_path": "/tmp/armactl-web/web/audit.log",
        },
        "bot": {
            "available": True,
            "enabled": False,
            "token_configured": False,
            "admin_chat_count": 0,
            "language": "uk",
            "env_path": "/srv/armactl-data/default/bot/.env",
            "service": {"available": False},
        },
        "errors": [],
    }


def _no_server_snapshot() -> dict:
    snapshot = _snapshot()
    snapshot.update(
        lifecycle="not_installed",
        installed=False,
        running=False,
        overview={
            "label": "no server",
            "empty_state": True,
            "empty_title": "No server found",
            "empty_message": "Discovery did not find an installed server.",
        },
        paths={
            "instance_root": "",
            "install_dir": "",
            "config_path": "",
            "config_dir": "",
            "logs_dir": "",
            "service_name": "armareforger.service",
            "timer_name": "armareforger-restart.timer",
        },
        service={"available": False, "error": "server service is not installed"},
        timer={"available": False, "error": "restart timer is not installed"},
        service_runtime={
            "available": False,
            "cpu_text": "Unknown",
            "memory_text": "Unknown",
        },
        operational_status={
            "available": False,
            "message": "Unknown",
            "age_text": "Unknown",
        },
        players={"available": False, "count_text": "unavailable"},
        ports={"available": False, "error": "server is not installed"},
    )
    return snapshot


def _client(app, base_url: str = "http://testserver"):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(app, base_url=base_url)


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


def _stub_dashboard(monkeypatch):
    from armactl.web.routes import dashboard

    calls: list[str] = []

    def fake_load_dashboard_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        calls.append(instance)
        return _snapshot()

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_load_dashboard_snapshot)
    return calls


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


def _audit_log_text(data_root: Path) -> str:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return audit_path.read_text(encoding="utf-8")


def _audit_events(data_root: Path) -> list[dict]:
    return [json.loads(line) for line in _audit_log_text(data_root).splitlines()]


def _service_action_state(
    *,
    installed: bool = True,
    running: bool = False,
    config_exists: bool = True,
):
    from armactl.state import ServerState

    return ServerState(
        server_installed=installed,
        config_exists=config_exists,
        server_running=running,
        service_name="armareforger.service",
    )


def _stub_service_backend(
    monkeypatch,
    *,
    running: bool = False,
    success: bool = True,
    message: str = "service ok",
    exit_code: int = 0,
):
    from armactl.service_manager import ServiceResult
    from armactl.web.services import service_actions

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service_actions.discovery,
        "discover",
        lambda instance, save=False: _service_action_state(running=running),
    )

    def start(service_name: str) -> ServiceResult:
        calls.append(("start", service_name))
        return ServiceResult(success, message, exit_code)

    def stop(service_name: str) -> ServiceResult:
        calls.append(("stop", service_name))
        return ServiceResult(success, message, exit_code)

    def restart(service_name: str) -> ServiceResult:
        calls.append(("restart", service_name))
        return ServiceResult(success, message, exit_code)

    monkeypatch.setattr(service_actions.service_manager, "start_service", start)
    monkeypatch.setattr(service_actions.service_manager, "stop_service", stop)
    monkeypatch.setattr(service_actions.service_manager, "restart_service", restart)
    return calls


def test_create_app_import_does_not_import_tui_or_textual(monkeypatch):
    forbidden = ("armactl.tui", "textual")
    _forget_modules("armactl.web.app", *forbidden)
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, forbidden):
            blocked_imports.append(name)
            raise AssertionError(f"web app imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.app")

    assert callable(module.create_app)
    assert blocked_imports == []
    assert "armactl.tui" not in sys.modules
    assert "textual" not in sys.modules


def test_healthz_returns_ok():
    from armactl.web.app import create_app

    client = _client(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_unauthenticated_dashboard_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/dashboard", follow_redirects=False)
    root_response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert root_response.status_code == 303
    assert root_response.headers["location"] == "/login"


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


def test_session_cookie_authenticates_dashboard(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))

    login_response = _login(client, "owner", password)
    response = client.get("/dashboard", follow_redirects=False)

    assert login_response.status_code == 303
    assert response.status_code == 200
    assert "Mock Server" in response.text
    assert "Log out" in response.text
    assert calls == ["default"]


def test_dashboard_permission_denied_returns_controlled_403(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _stub_dashboard(monkeypatch)
    monkeypatch.setattr(dashboard, "require_permission", lambda current, permission: False)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text
    assert calls == []


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


def test_logout_with_valid_csrf_revokes_session_and_clears_cookie(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner logout password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)
    dashboard_response = client.get("/dashboard")
    csrf_token = _form_token(dashboard_response.text)

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


def test_password_hash_and_session_token_do_not_appear_in_dashboard_html(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner secret password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert password not in response.text
    assert user.password_hash not in response.text
    assert session_token
    assert session_token not in response.text


def test_dashboard_routes_render_html(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)

    root_response = client.get("/", follow_redirects=False)
    dashboard_response = client.get("/dashboard", follow_redirects=False)

    assert login_response.status_code == 303
    assert root_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert "text/html" in root_response.headers["content-type"]
    assert "Mock Server" in root_response.text
    assert "running" in root_response.text
    assert "3 / 64" in root_response.text
    assert "owner" in root_response.text
    assert 'action="/service/start"' in root_response.text
    assert 'action="/service/stop"' in root_response.text
    assert 'action="/service/restart"' in root_response.text
    assert calls == ["default", "default"]



def test_dashboard_no_server_empty_state_renders_controlled_html(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)

    def fake_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        return _no_server_snapshot()

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_snapshot)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "No server found" in response.text
    assert "Discovery did not find an installed server." in response.text
    assert "Traceback" not in response.text


def test_dashboard_partial_data_renders_controlled_section(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    snapshot = _snapshot()
    snapshot["host_metrics"] = {
        "available": False,
        "cpu_text": "Unknown",
        "memory_text": "Unknown",
        "disk_text": "Unknown",
        "load_text": "Unknown",
        "uptime_text": "Unknown",
        "error": "host boom",
    }
    snapshot["errors"] = [{"section": "host_metrics", "message": "host boom"}]

    def fake_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        return snapshot

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_snapshot)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Partial data" in response.text
    assert "host_metrics: host boom" in response.text
    assert "Traceback" not in response.text





def test_unauthenticated_service_action_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.post("/service/start", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_service_action_permission_denied_returns_controlled_403(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.routes import service
    from armactl.web.services import service_actions

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)

    def fail_action(*args, **kwargs):
        raise AssertionError("backend action should not be called")

    monkeypatch.setattr(service, "require_permission", lambda current, permission: False)
    monkeypatch.setattr(service_actions, "run_service_action_and_audit", fail_action)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    csrf_token = login_response.cookies.get(CSRF_COOKIE_NAME)

    response = client.post(
        "/service/start",
        data={"csrf_token": csrf_token or ""},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_service_action_invalid_csrf_does_not_call_backend(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import service_actions

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)

    def fail_action(*args, **kwargs):
        raise AssertionError("backend action should not be called")

    monkeypatch.setattr(service_actions, "run_service_action_and_audit", fail_action)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/service/start",
        data={"csrf_token": "wrong-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert "Traceback" not in response.text


def test_service_start_calls_backend_and_writes_audit(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    calls = _stub_service_backend(
        monkeypatch,
        running=False,
        success=True,
        message="started token=route-secret",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    response = client.post(
        "/service/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    events = _audit_events(tmp_path)
    assert response.status_code == 200
    assert "Service action result" in response.text
    assert "Start success" in response.text
    assert "route-secret" not in response.text
    assert calls == [("start", "armareforger.service")]
    assert len(events) == 1
    assert events[0]["username"] == "owner"
    assert events[0]["action"] == "start"
    assert events[0]["instance"] == "default"
    assert events[0]["target"] == "armareforger.service"
    assert events[0]["success"] is True
    assert "route-secret" not in _audit_log_text(tmp_path)


def test_service_stop_and_restart_require_confirmation(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import service_actions

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)

    def fail_action(*args, **kwargs):
        raise AssertionError("backend action should not be called")

    monkeypatch.setattr(service_actions, "run_service_action_and_audit", fail_action)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    stop_response = client.post(
        "/service/stop",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    restart_response = client.post(
        "/service/restart",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert stop_response.status_code == 400
    assert "Confirmation is required to stop the server." in stop_response.text
    assert restart_response.status_code == 400
    assert "Confirmation is required to restart the server." in restart_response.text
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_service_backend_failure_renders_controlled_result_and_audit(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    calls = _stub_service_backend(
        monkeypatch,
        running=False,
        success=False,
        message="failed password=backend-secret token=route-token",
        exit_code=7,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    response = client.post(
        "/service/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    events = _audit_events(tmp_path)
    assert response.status_code == 200
    assert "Start failure" in response.text
    assert "password=***" in response.text
    assert "token=***" in response.text
    assert "backend-secret" not in response.text
    assert "route-token" not in response.text
    assert "Traceback" not in response.text
    assert calls == [("start", "armareforger.service")]
    assert events[0]["success"] is False
    assert events[0]["exit_code"] == 7
    assert "backend-secret" not in _audit_log_text(tmp_path)
    assert "route-token" not in _audit_log_text(tmp_path)


def test_service_action_html_and_audit_do_not_expose_auth_secrets(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner action secret password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    _stub_dashboard(monkeypatch)
    _stub_service_backend(
        monkeypatch,
        running=False,
        success=False,
        message="failed password=backend-secret token=backend-token",
        exit_code=9,
    )
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)
    csrf_token = _action_csrf_token(client)

    response = client.post(
        "/service/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    audit_text = _audit_log_text(tmp_path)

    assert response.status_code == 200
    assert session_token
    for secret in (
        password,
        user.password_hash,
        session_token,
        "backend-secret",
        "backend-token",
    ):
        assert secret not in response.text
        assert secret not in audit_text

def test_dashboard_facade_error_returns_controlled_html(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)

    def fail_dashboard(instance: str, *, web_config=None) -> dict:
        raise RuntimeError("boom with traceback-looking details")

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fail_dashboard)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 500
    assert "text/html" in response.headers["content-type"]
    assert "Dashboard data is unavailable." in response.text
    assert "RuntimeError" in response.text
    assert "boom" not in response.text
    assert "Traceback" not in response.text


def test_template_and_static_paths_are_package_local():
    from armactl.web.app import STATIC_DIR, TEMPLATES_DIR, create_app

    assert (TEMPLATES_DIR / "dashboard.html").is_file()
    assert (TEMPLATES_DIR / "dashboard_error.html").is_file()
    assert (TEMPLATES_DIR / "login.html").is_file()
    assert (TEMPLATES_DIR / "service_result.html").is_file()
    assert (STATIC_DIR / "css" / "app.css").is_file()

    client = _client(create_app())
    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ".summary-band" in response.text
    assert ".auth-panel" in response.text
