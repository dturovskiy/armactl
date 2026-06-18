"""Tests for the minimal FastAPI web app foundation."""

from __future__ import annotations

import json
import re
import sqlite3
import warnings
from dataclasses import replace
from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, LOGIN_CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.sessions import create_session, revoke_session, validate_session
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username
from armactl.web.i18n import LANGUAGE_COOKIE_NAME, THEME_COOKIE_NAME
from armactl.web.runtime import ensure_web_runtime, save_web_runtime_config


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
            "memory_used_bytes": 512,
            "memory_total_bytes": 1024,
            "disk_used_bytes": 2048,
            "disk_total_bytes": 4096,
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
        "sat": {
            "available": True,
            "valid_json": True,
            "desired_admins": ["21761a7f-c9b4-4bff-8375-b4b43abb95ec"],
            "missing_mappings": [],
            "default_only_admins": False,
            "default_only_game_masters": False,
            "missing_admins": [],
            "missing_game_masters": [],
            "warning": "",
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
        sat={"available": False, "error": "config path is not available"},
    )
    return snapshot


def _stopped_snapshot() -> dict:
    snapshot = _snapshot()
    snapshot.update(
        lifecycle="stopped",
        running=False,
        overview={
            "label": "stopped",
            "empty_state": False,
            "empty_title": "",
            "empty_message": "",
        },
        service={
            "available": True,
            "active": False,
            "enabled": True,
            "active_state": "inactive",
            "main_pid": 0,
        },
        service_runtime={
            "available": False,
            "cpu_text": "Unknown",
            "memory_text": "Unknown",
        },
        players={"available": False, "count_text": "unavailable"},
        fps_metrics={
            "available": False,
            "fps_text": "unavailable",
            "age_text": "unknown",
            "freshness": "unavailable",
        },
    )
    return snapshot


def _starting_snapshot() -> dict:
    snapshot = _snapshot()
    snapshot.update(
        lifecycle="starting",
        running=False,
        overview={
            "label": "starting",
            "empty_state": False,
            "empty_title": "",
            "empty_message": "",
        },
        service={
            "available": True,
            "active": True,
            "enabled": True,
            "active_state": "active",
            "main_pid": 123,
        },
        operational_status={
            "available": True,
            "message": "Waiting for server telemetry",
            "age_text": "0s",
        },
        players={"available": False, "count_text": "unavailable"},
        fps_metrics={
            "available": False,
            "fps_text": "Unknown",
            "age_text": "Unknown",
            "freshness": "unavailable",
        },
    )
    return snapshot


def _incomplete_snapshot() -> dict:
    snapshot = _no_server_snapshot()
    snapshot.update(
        lifecycle="incomplete",
        overview={
            "label": "incomplete",
            "empty_state": True,
            "empty_title": "Installation incomplete",
            "empty_message": (
                "Some server evidence exists, but the install or config is incomplete."
            ),
        },
        paths={
            "instance_root": "/srv/armactl-data/default",
            "install_dir": "/srv/armactl-data/default/server",
            "config_path": "/srv/armactl-data/default/config/config.json",
            "config_dir": "/srv/armactl-data/default/config",
            "logs_dir": "/srv/armactl-data/default/config/logs",
            "service_name": "armareforger.service",
            "timer_name": "armareforger-restart.timer",
        },
    )
    return snapshot


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


def _stub_dashboard(monkeypatch):
    from armactl.web.routes import dashboard

    calls: list[str] = []

    def fake_load_dashboard_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        calls.append(instance)
        return _snapshot()

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_load_dashboard_snapshot)
    return calls


def _management_pages() -> dict[str, dict]:
    return {
        "config": {
            "instance": "default",
            "available": True,
            "error": "",
            "status": {
                "lifecycle": "running",
                "installed": True,
                "running": True,
                "label": "running",
            },
            "paths": {
                "config_path": "/srv/armactl-data/default/config/config.json",
                "instance_root": "/srv/armactl-data/default",
                "install_dir": "/srv/armactl-data/default/server",
            },
            "config": {
                "available": True,
                "server_name": "Read Only Server",
                "scenario_id": "Scenario.conf",
                "max_players": 42,
                "visible_text": "yes",
                "battleye_text": "no",
                "bind_port": 2400,
                "a2s_port": 17778,
                "rcon_port": 20000,
                "unused_secret": "raw-rcon-secret",
            },
        },
        "mods": {
            "instance": "default",
            "available": True,
            "error": "",
            "paths": {"config_path": "/srv/armactl-data/default/config/config.json"},
            "count": 1,
            "mods": [{"mod_id": "mod-a", "name": "Mod A", "version": "1.0"}],
            "disabled_count": 1,
            "disabled_mods": [
                {"mod_id": "mod-b", "name": "Mod B Disabled", "version": ""}
            ],
            "disabled_mods_path": "/srv/armactl-data/default/mods-state.json",
            "disabled_mods_error": "",
        },
        "admins": {
            "instance": "default",
            "available": True,
            "error": "",
            "paths": {"config_path": "/srv/armactl-data/default/config/config.json"},
            "official_count": 1,
            "local_label_count": 1,
            "local_labels_path": "/srv/armactl-data/default/config/admins-state.json",
            "local_labels_error": "",
            "official_admins": [
                {"identity_id": "ABC123", "name": "Local Captain", "source": "local"}
            ],
        },
        "bot": {
            "instance": "default",
            "available": True,
            "error": "",
            "bot": {
                "available": True,
                "enabled": True,
                "token_configured": True,
                "token": "raw-bot-token-secret",
                "admin_chat_count": 2,
                "language": "uk",
                "env_path": "/srv/armactl-data/default/bot/.env",
                "service": {
                    "available": True,
                    "service_name": "armactl-bot.service",
                    "active": False,
                    "enabled": True,
                    "runtime_ready": None,
                },
            },
            "errors": [],
        },
    }


def _stub_management_pages(monkeypatch, pages: dict[str, dict] | None = None) -> list[str]:
    from armactl.web import facade
    from armactl.web.services import player_moderation

    page_data = pages or _management_pages()
    calls: list[str] = []

    def fake_loader(name: str):
        def load(instance: str) -> dict:
            calls.append(name)
            assert instance == "default"
            return page_data[name]

        return load

    monkeypatch.setattr(facade, "load_config_page", fake_loader("config"))
    monkeypatch.setattr(facade, "load_mods_page", fake_loader("mods"))
    monkeypatch.setattr(facade, "load_admins_page", fake_loader("admins"))
    monkeypatch.setattr(
        player_moderation,
        "load_player_moderation_panel",
        lambda instance, query="": {
            "available": True,
            "query": query,
            "players": [],
            "total_count": 0,
            "filtered_count": 0,
            "source": "rcon.roster",
            "status": "available",
            "error": "",
        },
    )
    monkeypatch.setattr(facade, "load_bot_page", fake_loader("bot"))
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


def _jobs_csrf_token(client) -> str:
    jobs_response = client.get("/jobs")
    assert jobs_response.status_code == 200
    return _form_token(jobs_response.text)


def _audit_log_text(data_root: Path) -> str:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return audit_path.read_text(encoding="utf-8")


def _audit_events(data_root: Path) -> list[dict]:
    events = [json.loads(line) for line in _audit_log_text(data_root).splitlines()]
    return [event for event in events if (event.get('details') or {}).get('phase') != 'intent']


def _insert_raw_web_job(
    db_path: Path,
    *,
    kind: str,
    status: str,
    created_at: str,
) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO web_jobs (
                kind,
                status,
                requested_by_username,
                instance,
                current_step,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'owner', 'default', 'legacy active row', ?, ?)
            """,
            (kind, status, created_at, created_at),
        )
        job_id = cursor.lastrowid
    assert job_id is not None
    return int(job_id)


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


def test_create_app_import_does_not_import_tui_or_textual(
    assert_import_does_not_import_modules,
):
    forbidden = ("armactl.tui", "textual")
    assert_import_does_not_import_modules("armactl.web.app", forbidden)


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



def test_login_template_has_language_and_theme_controls(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/login")

    assert response.status_code == 200
    assert 'action="/preferences/language"' in response.text
    assert 'action="/preferences/theme"' in response.text
    assert 'data-preference-form="language"' in response.text
    assert 'data-preference-form="theme"' in response.text
    assert 'class="language-menu"' in response.text
    assert 'class="icon-control language-summary"' in response.text
    assert 'class="language-option language-option-active"' in response.text
    assert 'class="icon-control theme-toggle-button"' in response.text
    assert 'data-theme-label' in response.text
    assert 'class="control-svg language-icon"' in response.text
    assert "data-theme-icon-dark" in response.text
    assert "data-theme-icon-light" in response.text
    assert "control-image" not in response.text
    assert "control-chevron" not in response.text
    assert "◎" not in response.text
    assert "☾" not in response.text
    assert "☀" not in response.text
    assert '/static/js/preferences.js' in response.text
    assert '/static/img/armactl_dashboard.png?v=' in response.text
    assert '/static/css/app.css?v=' in response.text
    assert '/static/js/preferences.js?v=' in response.text
    assert 'data-theme="light"' in response.text
    assert "English" in response.text
    assert "Українська" in response.text
    assert 'aria-label="Theme: dark"' in response.text


def test_preferences_js_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/preferences.js")

    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "document.documentElement.dataset.theme" in response.text
    assert "pageshow" in response.text
    assert "event.persisted" in response.text
    assert "window.location.reload()" in response.text
    assert "armactl_web_session" not in response.text
    assert "csrf_token" not in response.text


def test_preferences_js_persists_requested_theme_before_flipping_next_value():
    script = Path("src/armactl/web/static/js/preferences.js").read_text(encoding="utf-8")

    persist_index = script.index("const persist = postPreference(form);")
    flip_index = script.index("updateThemeButton(form, oppositeTheme(requestedTheme));")

    assert persist_index < flip_index


def test_login_renders_ukrainian_from_language_preference(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path))
    preference_response = client.post(
        "/preferences/language",
        data={"language": "uk", "next": "/login"},
        follow_redirects=False,
    )

    response = client.get("/login")

    assert preference_response.status_code == 303
    assert preference_response.cookies.get(LANGUAGE_COOKIE_NAME) == "uk"
    assert '<html lang="uk" data-theme="light">' in response.text
    assert "Увійти" in response.text
    assert "Ім&#39;я користувача" in response.text
    assert 'class="language-menu"' in response.text
    assert "Українська" in response.text
    assert 'data-theme-label-prefix="Тема"' in response.text


def test_invalid_language_and_theme_preferences_are_normalized(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    language_response = client.post(
        "/preferences/language",
        data={"language": "not-a-language", "next": "/login"},
        follow_redirects=False,
    )
    theme_response = client.post(
        "/preferences/theme",
        data={"theme": "solarized", "next": "/login"},
        follow_redirects=False,
    )

    assert language_response.status_code == 303
    assert language_response.cookies.get(LANGUAGE_COOKIE_NAME) == "en"
    assert theme_response.status_code == 303
    assert theme_response.cookies.get(THEME_COOKIE_NAME) == "light"


def test_theme_preference_async_sets_cookie_without_redirect(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "next": "/dashboard"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json() == {"theme": "dark"}
    assert response.cookies.get(THEME_COOKIE_NAME) == "dark"
    assert "location" not in response.headers


def test_accept_language_localizes_login_without_cookie(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/login", headers={"accept-language": "uk-UA, en;q=0.2"})

    assert response.status_code == 200
    assert '<html lang="uk" data-theme="light">' in response.text
    assert "Увійти" in response.text


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
    assert 'action="/preferences/language"' in response.text
    assert 'action="/preferences/theme"' in response.text
    assert calls == ["default"]



def test_authenticated_owner_can_fetch_dashboard_status_json(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard/status.json", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["ok"] is True
    assert payload["lifecycle"] == "running"
    assert payload["installed"] is True
    assert payload["running"] is True
    assert payload["fields"]["heading"] == "Mock Server"
    assert payload["fields"]["overview.players"] == "3 / 64"
    assert payload["fields"]["live.fps"] == "59.8"
    assert payload["host"]["cpu"] == "12.0%"
    assert payload["mods"]["count"] == 2
    assert payload["metrics"]["fps"] == {
        "available": True,
        "value": 59.8,
        "percent": 99.67,
        "text": "59.8",
    }
    assert payload["metrics"]["cpu"]["percent"] == 12.0
    assert payload["metrics"]["memory"]["percent"] == 50.0
    assert payload["metrics"]["disk"]["percent"] == 50.0
    assert [action["name"] for action in payload["actions"]] == ["stop", "restart"]
    assert calls == ["default"]


def test_unauthenticated_dashboard_status_json_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/dashboard/status.json", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_status_json_permission_denied_returns_controlled_403(
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

    response = client.get("/dashboard/status.json", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert calls == []


def test_dashboard_status_json_does_not_include_secrets_or_paths(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner status secret password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)

    response = client.get("/dashboard/status.json", follow_redirects=False)

    assert response.status_code == 200
    body = response.text
    assert password not in body
    assert user.password_hash not in body
    assert session_token
    assert session_token not in body
    assert "csrf_token" not in body
    assert "session" not in body.lower()
    assert "config_path" not in body
    assert "/srv/armactl-data" not in body
    assert "ARMACTL_WEB_SESSION_SECRET" not in body


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
    assert "/static/js/preferences.js" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET" not in response.text
    assert "/static/js/dashboard.js" in response.text
    assert "/static/js/service_actions.js" in response.text
    assert "data-dashboard-root" in response.text
    assert 'data-dashboard-field="overview.players"' in response.text
    assert "data-dashboard-live-status" in response.text
    assert 'data-dashboard-meter="fps"' in response.text
    assert 'data-dashboard-meter="cpu"' in response.text
    assert 'data-dashboard-meter="memory"' in response.text
    assert 'data-dashboard-meter="disk"' in response.text


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
    assert dashboard_response.headers["Cache-Control"] == "no-store, max-age=0"
    assert dashboard_response.headers["Pragma"] == "no-cache"
    assert dashboard_response.headers["Expires"] == "0"
    assert "text/html" in root_response.headers["content-type"]
    assert "Mock Server" in root_response.text
    assert "running" in root_response.text
    assert "3 / 64" in root_response.text
    assert "owner" in root_response.text
    assert "Quick actions" in root_response.text
    assert "Server snapshot" in root_response.text
    assert "Host" in root_response.text
    assert "Web Runtime" not in root_response.text
    assert "ServerAdminTools" not in root_response.text
    assert 'server-snapshot-grid' in root_response.text
    assert "status-pill" in root_response.text
    assert "key-value-list" in root_response.text
    assert "value-block" in root_response.text
    assert 'summary-card-wide' in root_response.text
    assert 'action="/service/start"' not in root_response.text
    assert 'action="/service/stop"' in root_response.text
    assert 'action="/service/restart"' in root_response.text
    assert 'href="/config"' in root_response.text
    assert 'href="/mods"' in root_response.text
    assert 'href="/admins"' in root_response.text
    assert 'href="/bot"' in root_response.text
    assert 'href="/jobs"' in root_response.text
    assert 'href="/files"' in root_response.text
    assert 'href="/logs"' in root_response.text
    assert "Pending operator work" in root_response.text
    assert "No pending operator work." in root_response.text
    assert "All saved changes are applied. No action is required." in root_response.text
    assert "Background jobs" not in root_response.text
    assert "No background jobs." not in root_response.text
    assert "/static/js/dashboard.js" in root_response.text
    assert "/static/js/service_actions.js" in root_response.text
    assert 'data-service-action-form' in root_response.text
    assert 'data-service-action="stop"' in root_response.text
    assert 'data-service-action="restart"' in root_response.text
    assert 'data-service-action-label="Stopping server..."' in root_response.text
    assert 'data-service-action-label="Restarting server..."' in root_response.text
    assert 'data-service-action-submit' in root_response.text
    assert re.search(
        r'<input type="checkbox" name="confirm" value="stop" required>',
        root_response.text,
    )
    assert re.search(
        r'<input type="checkbox" name="confirm" value="restart" required>',
        root_response.text,
    )
    service_action_values = re.findall(
        r'data-service-action(?:-[\w-]+)?="([^"]*)"',
        root_response.text,
    )
    assert service_action_values
    for value in service_action_values:
        assert "csrf" not in value.lower()
        assert "session" not in value.lower()
        assert "token" not in value.lower()
    assert 'data-dashboard-endpoint="/dashboard/status.json"' in root_response.text
    assert 'data-dashboard-field="host.cpu"' in root_response.text
    assert 'data-dashboard-meter="fps"' in root_response.text
    assert 'data-dashboard-sparkline="fps"' not in root_response.text
    assert 'data-dashboard-meter="cpu"' in root_response.text
    assert 'data-dashboard-metric-fill="disk"' in root_response.text
    assert calls == ["default", "default"]




def test_dashboard_js_static_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/dashboard.js", follow_redirects=False)

    assert response.status_code == 200
    assert "dashboard/status.json" in response.text
    assert "data-dashboard-root" in response.text
    assert "data-dashboard-meter" in response.text
    assert "data-dashboard-sparkline" not in response.text
    assert "metricHistory" not in response.text
    assert "ARMACTL_WEB_SESSION_SECRET" not in response.text
    assert "csrf_token" not in response.text
    assert "password" not in response.text.lower()


def test_service_action_js_static_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/service_actions.js", follow_redirects=False)

    assert response.status_code == 200
    assert "data-service-action-form" in response.text
    assert 'addEventListener("submit"' in response.text
    assert "service-action-overlay" in response.text
    assert "reportValidity" in response.text
    assert "disabled = true" in response.text
    assert "csrf" not in response.text.lower()
    assert "token" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_dashboard_stopped_server_shows_start_only(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)

    def fake_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        return _stopped_snapshot()

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_snapshot)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/service/start"' in response.text
    assert 'action="/service/stop"' not in response.text
    assert 'action="/service/restart"' not in response.text
    assert 'data-service-action="start"' in response.text
    assert 'data-service-action-label="Starting server..."' in response.text
    assert "/static/js/service_actions.js" in response.text
    assert "Server snapshot" in response.text
    assert 'href="/config"' in response.text


def test_dashboard_starting_server_hides_service_actions(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)

    def fake_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        return _starting_snapshot()

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_snapshot)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "starting" in response.text
    assert "Server is starting; actions are unavailable until telemetry is ready." in response.text
    assert "action=\"/service/start\"" not in response.text
    assert "action=\"/service/stop\"" not in response.text
    assert "action=\"/service/restart\"" not in response.text
    assert "data-service-action-form" not in response.text
    assert "/static/js/service_actions.js" in response.text
    assert "Live server" not in response.text

def test_dashboard_running_server_shows_stop_restart_only(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/service/start"' not in response.text
    assert 'action="/service/stop"' in response.text
    assert 'action="/service/restart"' in response.text
    assert "3 / 64" in response.text
    assert "59.8" in response.text


def test_dashboard_renders_ukrainian_and_dark_theme_preference(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    language_response = client.post(
        "/preferences/language",
        data={"language": "uk", "csrf_token": csrf_token, "next": "/dashboard"},
        follow_redirects=False,
    )
    theme_response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "csrf_token": csrf_token, "next": "/dashboard"},
        follow_redirects=False,
    )
    response = client.get("/dashboard", follow_redirects=False)

    assert language_response.status_code == 303
    assert language_response.cookies.get(LANGUAGE_COOKIE_NAME) == "uk"
    assert theme_response.status_code == 303
    assert theme_response.cookies.get(THEME_COOKIE_NAME) == "dark"
    assert response.status_code == 200
    assert '<html lang="uk" data-theme="dark">' in response.text
    assert "Швидкі дії" in response.text
    assert "Зупинити" in response.text
    assert "Перезапустити" in response.text
    assert "Хост" in response.text
    assert "Web runtime" not in response.text
    assert "Вийти" in response.text
    assert "Тема: світла" in response.text


def test_authenticated_theme_preference_requires_valid_csrf(
    tmp_path: Path,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "csrf_token": "wrong-token", "next": "/dashboard"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert response.cookies.get(THEME_COOKIE_NAME) is None


def test_authenticated_language_preference_requires_valid_csrf(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/preferences/language",
        data={"language": "uk", "csrf_token": "wrong-token", "next": "/dashboard"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert response.cookies.get(LANGUAGE_COOKIE_NAME) is None


def test_dashboard_async_preferences_do_not_reload_heavy_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _stub_dashboard(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)
    assert calls == ["default"]

    theme_response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "csrf_token": csrf_token, "next": "/dashboard"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        follow_redirects=False,
    )
    language_response = client.post(
        "/preferences/language",
        data={"language": "uk", "csrf_token": csrf_token, "next": "/dashboard"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        follow_redirects=False,
    )

    assert theme_response.status_code == 200
    assert theme_response.json() == {"theme": "dark"}
    assert language_response.status_code == 200
    assert language_response.json() == {"language": "uk"}
    assert calls == ["default"]


def test_dashboard_incomplete_server_shows_repair_job_action(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)

    def fake_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        return _incomplete_snapshot()

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_snapshot)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/jobs/server/repair"' in response.text
    assert 'action="/service/start"' not in response.text
    assert 'action="/service/stop"' not in response.text
    assert 'action="/service/restart"' not in response.text
    assert "Repair" in response.text
    assert "Installation incomplete" in response.text


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
    assert 'action="/jobs/server/install"' in response.text
    assert "Install" in response.text
    assert "Host" in response.text
    assert "Web Runtime" not in response.text
    assert "Mock Server" not in response.text
    assert 'action="/service/start"' not in response.text
    assert 'action="/service/stop"' not in response.text
    assert 'action="/service/restart"' not in response.text
    assert 'href="/config"' not in response.text
    assert 'href="/mods"' not in response.text
    assert 'href="/admins"' not in response.text
    assert 'href="/bot"' not in response.text
    assert 'href="/files"' not in response.text
    assert ">Players<" not in response.text
    assert ">Telemetry<" not in response.text
    assert ">Ports<" not in response.text
    assert ">ServerAdminTools<" not in response.text
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
    assert "host_metrics" in response.text
    assert "host boom" in response.text
    assert "Traceback" not in response.text







def test_unauthenticated_management_pages_redirect_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    for path in ("/config", "/mods", "/admins", "/bot"):
        response = client.get(path, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_authenticated_owner_can_view_management_pages(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _stub_management_pages(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    config_response = client.get("/config", follow_redirects=False)
    mods_response = client.get("/mods", follow_redirects=False)
    admins_response = client.get("/admins", follow_redirects=False)
    bot_response = client.get("/bot", follow_redirects=False)

    assert config_response.status_code == 200
    assert "Read Only Server" in config_response.text
    assert "Scenario.conf" in config_response.text
    assert mods_response.status_code == 200
    assert "mod-a" in mods_response.text
    assert "Mod A" in mods_response.text
    assert admins_response.status_code == 200
    assert "ABC123" in admins_response.text
    assert "Local Captain" in admins_response.text
    assert bot_response.status_code == 200
    assert "Token configured" in bot_response.text
    assert "raw-bot-token-secret" not in bot_response.text
    assert calls == ["config", "mods", "admins", "bot"]


def test_management_permission_denied_returns_controlled_403_and_skips_backend(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    calls = _stub_management_pages(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    for path in ("/config", "/mods", "/admins", "/bot"):
        response = client.get(path, follow_redirects=False)

        assert response.status_code == 403
        assert response.text == "Permission denied."
        assert "Traceback" not in response.text

    assert calls == []


def test_management_pages_render_controlled_empty_states(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    pages = {
        name: {
            "instance": "default",
            "available": False,
            "error": "config path is not available",
        }
        for name in ("config", "mods", "admins", "bot")
    }
    _stub_management_pages(monkeypatch, pages)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    responses = {
        path: client.get(path, follow_redirects=False)
        for path in ("/config", "/mods", "/admins", "/bot")
    }

    assert responses["/config"].status_code == 200
    assert "Config data is unavailable." in responses["/config"].text
    assert "config path is not available" in responses["/config"].text
    assert "Mods data is unavailable." in responses["/mods"].text
    assert "Admins data is unavailable." in responses["/admins"].text
    assert "Bot data is unavailable." in responses["/bot"].text
    assert all("Traceback" not in response.text for response in responses.values())


def test_management_pages_render_ukrainian_labels(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_management_pages(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    config_response = client.get("/config", follow_redirects=False)
    mods_response = client.get("/mods", follow_redirects=False)
    admins_response = client.get("/admins", follow_redirects=False)
    bot_response = client.get("/bot", follow_redirects=False)

    assert config_response.status_code == 200
    assert "Конфіг сервера" in config_response.text
    assert "Активні моди" in mods_response.text
    assert "Офіційні ігрові адміни" in admins_response.text
    assert "Стан Telegram-бота" in bot_response.text


def test_management_pages_do_not_render_secrets(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    _stub_management_pages(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)

    html = "\n".join(
        client.get(path, follow_redirects=False).text
        for path in ("/config", "/mods", "/admins", "/bot")
    )

    assert password not in html
    assert user.password_hash not in html
    assert session_token
    assert session_token not in html
    assert "raw-rcon-secret" not in html
    assert "raw-bot-token-secret" not in html
    assert SESSION_COOKIE_NAME not in html
    assert CSRF_COOKIE_NAME not in html


def test_unauthenticated_jobs_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_owner_sees_jobs_page(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running, mark_job_succeeded

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:test", requested_by_username="owner")
    mark_job_running(db_path, job.id, current_step="Working", progress_current=1, progress_total=2)
    mark_job_succeeded(db_path, job.id, result_message="Job completed.", current_step="Done")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Operations" in response.text
    assert "Background jobs" in response.text
    assert "No pending operator work." in response.text
    assert "safe:test" in response.text
    assert "succeeded" in response.text
    assert "Job completed." in response.text
    assert "owner" in response.text
    assert 'action="/logout"' in response.text


def test_jobs_permission_denied_returns_controlled_403(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import jobs

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(jobs, "require_permission", lambda current, permission: False)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_jobs_page_renders_ukrainian_labels(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    create_job(tmp_path / "web" / "web.db", kind="safe:test", requested_by_username="owner")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert '<html lang="uk"' in response.text
    assert "Операції" in response.text
    assert "Фонові завдання" in response.text
    assert "Запитав" in response.text
    assert "у черзі" in response.text


def test_jobs_output_is_escaped_and_secrets_are_not_rendered(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import append_job_output, create_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:test", requested_by_username="owner")
    mark_job_running(db_path, job.id)
    append_job_output(
        db_path,
        job.id,
        stdout="<script>alert(1)</script> password=hunter2",
        stderr="ARMACTL_WEB_SESSION_SECRET=secret-value",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "hunter2" not in response.text
    assert "secret-value" not in response.text
    assert "password=***" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET=***" in response.text
    assert SESSION_COOKIE_NAME not in response.text
    assert CSRF_COOKIE_NAME not in response.text


def test_jobs_page_distinguishes_empty_pending_work_and_background_jobs(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Pending operator work" in response.text
    assert "Background jobs" in response.text
    assert "No pending operator work." in response.text
    assert "No background jobs." in response.text
    assert "No jobs yet." not in response.text


def test_jobs_page_shows_active_duplicate_job_store_integrity_warning(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import job_integrity

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(
        job_integrity,
        "job_store_integrity_diagnostics",
        lambda db_path: (
            job_integrity.JobStoreIntegrityDiagnostic(
                report_type="active_duplicate",
                severity="warning",
                job_kind="server:install",
                instance="default",
                active_job_ids=(11, 12),
                kept_job_id=11,
                duplicate_job_ids=(12,),
            ),
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Job-store integrity" in response.text
    assert "Duplicate active jobs detected." in response.text
    assert "Current duplicate active jobs are still present." in response.text
    assert "Maintenance is required to cancel duplicate active rows." in response.text
    assert "notice-panel notice-warning" in response.text
    assert "Job kind" in response.text
    assert "Active job IDs" in response.text
    assert "#11" in response.text
    assert "#12" in response.text
    assert "Job-store maintenance completed." not in response.text
    assert "Active jobs #" not in response.text
    assert "Review cancelled jobs below." not in response.text
    assert "Web job-store maintenance cancelled duplicate active jobs." not in response.text
    assert "Pending operator work" in response.text
    assert "No pending operator work." in response.text


def test_jobs_page_shows_repaired_job_store_report_as_neutral_notice(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import SERVER_INSTALL_JOB_KIND

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    kept = _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="queued",
        created_at="2026-01-01T00:00:00+00:00",
    )
    _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="running",
        created_at="2026-01-01T00:00:01+00:00",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Job-store integrity" in response.text
    assert "Job-store maintenance completed." in response.text
    assert "Duplicate active jobs were repaired. Oldest active jobs were kept." in response.text
    assert "Cancelled duplicate jobs remain visible below for audit context." in response.text
    assert "Repaired duplicate active jobs" in response.text
    assert "Last repair" in response.text
    assert "notice-panel diagnostic-notice" in response.text
    assert "notice-panel notice-warning" not in response.text
    assert "Duplicate active jobs detected." not in response.text
    assert "Web job-store maintenance cancelled duplicate active jobs." not in response.text
    assert "Review cancelled jobs below." not in response.text
    assert "Cancelled by web job-store maintenance; older active job kept." in response.text
    assert "Duplicate active job cancelled" in response.text
    with sqlite3.connect(db_path) as connection:
        active_rows = connection.execute(
            """
            SELECT id
            FROM web_jobs
            WHERE kind = ?
              AND status IN ('queued', 'running')
            ORDER BY id
            """,
            (SERVER_INSTALL_JOB_KIND,),
        ).fetchall()
    assert [row[0] for row in active_rows] == [kept]


def test_jobs_page_localizes_job_store_integrity_diagnostics_to_ukrainian(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import SERVER_INSTALL_JOB_KIND

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="queued",
        created_at="2026-01-01T00:00:00+00:00",
    )
    _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="running",
        created_at="2026-01-01T00:00:01+00:00",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Цілісність сховища завдань" in response.text
    assert "Обслуговування сховища завдань завершено." in response.text
    assert "Дублікати активних завдань виправлено." in response.text
    assert "Виправлені дублікати активних завдань" in response.text
    assert "Job-store maintenance completed." not in response.text
    assert "Duplicate active jobs were repaired." not in response.text
    assert "Repaired duplicate active jobs" not in response.text
    assert "Review cancelled jobs below." not in response.text


def test_dashboard_and_jobs_show_fallback_pending_work_without_leaking_secrets(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_CONFIG, mark_restart_pending_fallback

    password = "owner fallback password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    mark_restart_pending_fallback(
        tmp_path / "web" / "web.db",
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner token=raw-user-secret",
        details="max_players password=raw-detail-secret token=raw-token",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    dashboard_response = client.get("/dashboard", follow_redirects=False)
    jobs_response = client.get("/jobs", follow_redirects=False)

    for response in (dashboard_response, jobs_response):
        assert response.status_code == 200
        assert "Pending operator work" in response.text
        assert "Config changes" in response.text
        assert "Fallback storage" in response.text
        assert "Restart game server" in response.text
        assert "raw-user-secret" not in response.text
        assert "raw-detail-secret" not in response.text
        assert "raw-token" not in response.text
    assert "max_players" in jobs_response.text
    assert "No pending operator work." not in dashboard_response.text
    assert "No pending operator work." not in jobs_response.text


def test_jobs_page_shows_pending_work_when_background_jobs_empty_and_redacts_details(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_CONFIG, mark_restart_pending

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    mark_restart_pending(
        tmp_path / "web" / "web.db",
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details=(
            "max_players password=hunter2 token=raw-token "
            "ARMACTL_WEB_SESSION_SECRET=session-secret"
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Pending operator work" in response.text
    assert "These are not background jobs" in response.text
    assert "Background jobs" in response.text
    assert "Config changes" in response.text
    assert 'href="/config"' in response.text
    assert "config.save" in response.text
    assert "max_players" in response.text
    assert "No background jobs." in response.text
    assert "No jobs yet." not in response.text
    assert "pending-work-table" in response.text
    assert "pending-work-card" not in response.text
    assert "hunter2" not in response.text
    assert "raw-token" not in response.text
    assert "session-secret" not in response.text
    assert "password=***" in response.text
    assert "token=***" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET=***" in response.text
    assert SESSION_COOKIE_NAME not in response.text
    assert CSRF_COOKIE_NAME not in response.text


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
    assert "Server start completed." in response.text
    assert "The service action completed successfully." in response.text
    assert "route-secret" not in response.text
    assert calls == [("start", "armareforger.service")]
    assert len(events) == 1
    assert events[0]["username"] == "owner"
    assert events[0]["action"] == "start"
    assert events[0]["instance"] == "default"
    assert events[0]["target"] == "armareforger.service"
    assert events[0]["success"] is True
    assert "route-secret" not in _audit_log_text(tmp_path)


def test_dashboard_shows_compact_pending_work_summary(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_CONFIG, mark_restart_pending

    password = "owner pending password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    mark_restart_pending(
        tmp_path / "web" / "web.db",
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details="pending-detail-field password=raw-secret",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Pending operator work" in response.text
    assert "Saved changes waiting for manual action" in response.text
    assert "Config changes" in response.text
    assert 'href="/config"' in response.text
    assert "Restart game server" in response.text
    assert "View all work" in response.text
    assert response.text.count('href="/jobs"') == 1
    assert "View all jobs" not in response.text
    assert "Background jobs" not in response.text
    assert "No background jobs." not in response.text
    assert "pending-work-dashboard-table" in response.text
    assert response.text.count("View all work") == 1
    assert "pending-detail-field" not in response.text
    assert "raw-secret" not in response.text


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


def test_service_restart_clears_all_restart_related_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import (
        KIND_ADMINS,
        KIND_CONFIG,
        KIND_MODS,
        list_fallback_pending_work,
        list_pending_work,
        mark_restart_pending,
        mark_restart_pending_fallback,
        upsert_pending_work,
    )

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    _stub_service_backend(
        monkeypatch,
        running=True,
        success=True,
        message="restarted raw helper text token=backend-secret",
    )
    db_path = tmp_path / "web" / "web.db"
    mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )
    mark_restart_pending(
        db_path,
        kind=KIND_ADMINS,
        source_action="admin.add",
        username="owner",
    )
    mark_restart_pending(
        db_path,
        kind=KIND_MODS,
        source_action="mod.add",
        username="owner",
    )
    mark_restart_pending_fallback(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details="fallback config",
    )
    assert list_fallback_pending_work(db_path)
    upsert_pending_work(
        db_path,
        kind="schedule",
        source_path="/schedule",
        source_action="schedule.set",
        title="Schedule changes",
        username="owner",
        resolution_action="reload schedule",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    response = client.post(
        "/service/restart",
        data={"csrf_token": csrf_token, "confirm": "restart"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Server restart completed." in response.text
    assert "Pending restart work cleared." in response.text
    assert "All saved changes that required restart have been applied." in response.text
    assert "Dashboard and jobs" not in response.text
    assert "restart-related pending work" not in response.text
    assert "raw helper text" not in response.text
    assert "backend-secret" not in response.text
    remaining = list_pending_work(db_path)
    assert list_fallback_pending_work(db_path) == []
    assert len(remaining) == 1
    assert remaining[0].kind == "schedule"
    assert remaining[0].resolution_action == "reload schedule"

    dashboard_response = client.get("/dashboard", follow_redirects=False)
    jobs_response = client.get("/jobs", follow_redirects=False)
    assert dashboard_response.status_code == 200
    assert jobs_response.status_code == 200
    assert "Pending operator work" in dashboard_response.text
    assert "No pending operator work." not in dashboard_response.text
    assert "All saved changes are applied. No action is required." not in dashboard_response.text
    assert "Config changes" not in dashboard_response.text
    assert "Admin changes" not in dashboard_response.text
    assert "Mod changes" not in dashboard_response.text
    assert "Config changes" not in jobs_response.text
    assert "Admin changes" not in jobs_response.text
    assert "Mod changes" not in jobs_response.text
    assert "Schedule changes" in jobs_response.text


def test_failed_service_restart_does_not_clear_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import (
        KIND_CONFIG,
        get_pending_work,
        mark_restart_pending,
    )

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    _stub_service_backend(
        monkeypatch,
        running=True,
        success=False,
        message="restart failed password=backend-secret",
        exit_code=7,
    )
    db_path = tmp_path / "web" / "web.db"
    mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    response = client.post(
        "/service/restart",
        data={"csrf_token": csrf_token, "confirm": "restart"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Server restart failed." in response.text
    assert "Pending restart work cleared." not in response.text
    assert "backend-secret" not in response.text
    assert get_pending_work(db_path, kind=KIND_CONFIG) is not None


def test_service_unexpected_backend_exception_does_not_clear_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import service_actions
    from armactl.web.services.pending_work import (
        KIND_CONFIG,
        get_pending_work,
        mark_restart_pending,
    )

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)

    def fail_action(*args, **kwargs):
        raise RuntimeError("unexpected service bug token=raw-service-secret")

    monkeypatch.setattr(service_actions, "run_service_action_and_audit", fail_action)
    db_path = tmp_path / "web" / "web.db"
    mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )
    client = _client(
        create_app(data_root=tmp_path),
        raise_server_exceptions=False,
    )
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    response = client.post(
        "/service/restart",
        data={"csrf_token": csrf_token, "confirm": "restart"},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Internal Server Error" in response.text
    assert "Server restart completed." not in response.text
    assert "Service action is unavailable." not in response.text
    assert "raw-service-secret" not in response.text
    assert "Traceback" not in response.text
    assert get_pending_work(db_path, kind=KIND_CONFIG) is not None
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_service_restart_clears_migrated_legacy_pending_restart(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_CONFIG, list_pending_work

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    _stub_service_backend(
        monkeypatch,
        running=True,
        success=True,
        message="restarted",
    )
    db_path = tmp_path / "web" / "web.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_pending_restarts (
                instance TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                source_action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by_username TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_pending_restarts(
                instance,
                reason,
                source_action,
                details,
                created_at,
                updated_at,
                created_by_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "default",
                KIND_CONFIG,
                "config.save",
                "max_players",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "owner",
            ),
        )
    assert list_pending_work(db_path)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _action_csrf_token(client)

    response = client.post(
        "/service/restart",
        data={"csrf_token": csrf_token, "confirm": "restart"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert list_pending_work(db_path) == []
    dashboard_response = client.get("/dashboard", follow_redirects=False)
    jobs_response = client.get("/jobs", follow_redirects=False)
    assert "Pending operator work" in dashboard_response.text
    assert "No pending operator work." in dashboard_response.text
    assert "All saved changes are applied. No action is required." in dashboard_response.text
    assert "Config changes" not in jobs_response.text
    assert "No pending operator work." in jobs_response.text
    assert "No background jobs." in jobs_response.text


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
    assert "Server start failed." in response.text
    assert "Review diagnostic details below." in response.text
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
    assert (STATIC_DIR / "img" / "armactl_dashboard.png").is_file()

    client = _client(create_app())
    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ".summary-band" in response.text
    assert ".status-pill" in response.text
    assert ".key-value-list" in response.text
    assert ".notice-panel" in response.text

    logo_response = client.get("/static/img/armactl_dashboard.png")

    assert logo_response.status_code == 200
    assert "image/png" in logo_response.headers["content-type"]
    assert ".notice-restart" in response.text
    assert ".auth-panel" in response.text
    assert "[data-theme=\"dark\"]" in response.text


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


def test_dashboard_renders_external_bind_warning(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard
    from armactl.web.security.exposure import (
        EXTERNAL_BIND_WITHOUT_HTTPS_WARNING,
        get_exposure_warning,
    )

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    snapshot = _snapshot()
    warning = get_exposure_warning("0.0.0.0", https_required=False)
    assert warning is not None
    snapshot["web"].update(
        bind_host="0.0.0.0",
        https_required=False,
        https_required_text="no",
        exposure_warning=warning.to_dict(),
    )

    def fake_snapshot(instance: str, *, web_config=None) -> dict:
        assert web_config is not None
        return snapshot

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_snapshot)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Exposure warning" not in response.text
    assert EXTERNAL_BIND_WITHOUT_HTTPS_WARNING not in response.text
    assert password not in response.text
    assert "ARMACTL_WEB_SESSION_SECRET" not in response.text


def test_unauthenticated_install_and_repair_jobs_redirect_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    install_response = client.post("/jobs/server/install", data={}, follow_redirects=False)
    repair_response = client.post("/jobs/server/repair", data={}, follow_redirects=False)

    assert install_response.status_code == 303
    assert install_response.headers["location"] == "/login"
    assert repair_response.status_code == 303
    assert repair_response.headers["location"] == "/login"


def test_install_repair_job_permission_denied_returns_403(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import jobs

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(jobs, "require_permission", lambda current, permission: False)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    install_response = client.post("/jobs/server/install", data={}, follow_redirects=False)
    repair_response = client.post("/jobs/server/repair", data={}, follow_redirects=False)

    assert install_response.status_code == 403
    assert install_response.text == "Permission denied."
    assert repair_response.status_code == 403
    assert repair_response.text == "Permission denied."


def test_install_repair_jobs_require_csrf(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    install_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )
    repair_response = client.post(
        "/jobs/server/repair",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )

    assert install_response.status_code == 403
    assert install_response.text == "Invalid CSRF token."
    assert repair_response.status_code == 403
    assert repair_response.text == "Invalid CSRF token."


def test_post_install_and_repair_create_queued_jobs_without_running_backend(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs

    def fail_backend(*args, **kwargs):
        raise AssertionError("HTTP request must not run install/repair backend")

    monkeypatch.setattr(server_jobs.installer, "run_install", fail_backend)
    monkeypatch.setattr(server_jobs.repair, "run_repair", fail_backend)
    scheduled: list[int] = []
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda db_path, job_id: scheduled.append(job_id),
    )

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    install_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    repair_response = client.post(
        "/jobs/server/repair",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = list_recent_jobs(tmp_path / "web" / "web.db")
    audit_events = _audit_events(tmp_path)

    assert install_response.status_code == 303
    assert install_response.headers["location"] == "/jobs"
    assert repair_response.status_code == 303
    assert repair_response.headers["location"] == "/jobs"
    assert [job.kind for job in jobs[:2]] == ["server:repair", "server:install"]
    assert all(job.status == "queued" for job in jobs[:2])
    assert [event["action"] for event in audit_events[-2:]] == [
        "job.server-install.enqueue",
        "job.server-repair.enqueue",
    ]
    assert audit_events[-2]["details"]["job_kind"] == "server:install"
    assert audit_events[-2]["details"]["created"] == "true"
    assert audit_events[-1]["details"]["job_kind"] == "server:repair"
    assert audit_events[-1]["details"]["created"] == "true"
    assert scheduled == [jobs[1].id, jobs[0].id]


def test_post_install_reuses_existing_active_job_without_creating_duplicate(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs

    scheduled: list[int] = []
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda db_path, job_id: scheduled.append(job_id),
    )

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    first_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    second_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:install"
    ]
    audit_events = _audit_events(tmp_path)

    assert first_response.status_code == 303
    assert first_response.headers["location"] == "/jobs"
    assert second_response.status_code == 303
    assert second_response.headers["location"] == "/jobs"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id, jobs[0].id]
    assert [event["details"]["created"] for event in audit_events[-2:]] == ["true", "false"]


def test_install_job_audit_failure_cancels_created_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions
    from armactl.web.services.audit import AuditLogError

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    def fail_worker(*args, **kwargs):
        raise AssertionError("worker should not start when audit fails")

    monkeypatch.setattr(server_job_actions, "append_audit_event", fail_audit)
    monkeypatch.setattr(server_job_actions.server_jobs, "start_server_job_worker", fail_worker)

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = list_recent_jobs(tmp_path / "web" / "web.db")

    assert response.status_code == 500
    assert response.text == "Job queued but audit logging failed."
    assert len(jobs) == 1
    assert jobs[0].kind == "server:install"
    assert jobs[0].status == "cancelled"
    assert jobs[0].result_message == "Cancelled because audit logging failed."


def test_jobs_page_shows_queued_install_repair_jobs(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    _stub_dashboard(monkeypatch)
    monkeypatch.setattr(server_jobs, "start_server_job_worker", lambda db_path, job_id: None)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)
    client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "server:install" in response.text
    assert "queued" in response.text
    assert "Queued install" in response.text

def test_service_restart_backend_success_audit_failure_clears_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services.audit import AuditLogError, append_audit_event
    from armactl.web.services.pending_work import (
        KIND_CONFIG,
        get_pending_work,
        mark_restart_pending,
    )

    password = 'owner action password'
    setup_owner_user(tmp_path, 'owner', password)
    from armactl.web.services import service_actions
    _stub_dashboard(monkeypatch)
    _stub_service_backend(monkeypatch, running=True, success=True, message='restarted')
    db_path = tmp_path / 'web' / 'web.db'
    mark_restart_pending(db_path, kind=KIND_CONFIG, source_action='config.save', username='owner')

    def fail_outcome(*args, **kwargs):
        if (kwargs.get('details') or {}).get('phase') == 'outcome':
            raise AuditLogError('disk full')
        return append_audit_event(*args, **kwargs)

    monkeypatch.setattr(service_actions, 'append_audit_event', fail_outcome)
    client = _client(create_app(data_root=tmp_path))
    _login(client, 'owner', password)
    csrf_token = _action_csrf_token(client)
    response = client.post(
        '/service/restart',
        data={'csrf_token': csrf_token, 'confirm': 'restart'},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert 'Server restart completed.' in response.text
    assert 'Service action completed but audit logging failed.' in response.text
    assert 'All saved changes that required restart have been applied.' in response.text
    assert get_pending_work(db_path, kind=KIND_CONFIG) is None
