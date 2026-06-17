"""Tests for restart schedule web routes and actions."""

from __future__ import annotations

import json
import re
import sys
import warnings
from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning

from armactl.service_manager import ServiceResult
from armactl.state import ServerState
from armactl.web.auth.cookies import SESSION_COOKIE_NAME
from armactl.web.auth.setup import setup_owner_user


def _client(app):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(app)


def _form_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _login(client, username: str, password: str):
    form_response = client.get("/login")
    csrf_token = _form_token(form_response.text)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf_token},
        follow_redirects=False,
    )


def _schedule_page(
    *,
    timer_exists: bool = True,
    timer_active: bool = True,
    timer_enabled: bool = True,
    service_enabled: bool | None = True,
) -> dict:
    if service_enabled is True:
        boot_policy = "enabled"
    elif service_enabled is False:
        boot_policy = "disabled"
    else:
        boot_policy = "unknown"
    return {
        "instance": "default",
        "available": True,
        "error": "",
        "status": {"lifecycle": "running", "installed": True, "running": True},
        "paths": {},
        "timer": {
            "available": True,
            "timer_name": "armareforger-restart.timer",
            "exists": timer_exists,
            "active": timer_active,
            "enabled": timer_enabled,
            "active_state": "active" if timer_active else "inactive",
            "sub_state": "waiting" if timer_active else "dead",
            "unit_file_state": "enabled" if timer_enabled else "disabled",
            "description": "Restart timer",
            "schedule": "05:00, 20:30" if timer_exists else "",
            "schedule_display": "05:00, 20:30" if timer_exists else "unknown",
            "schedule_entries": (
                ["*-*-* 05:00:00", "*-*-* 20:30:00"] if timer_exists else []
            ),
            "next_run": "Mon 2026-06-15 20:30:00 UTC" if timer_exists else "",
            "last_trigger": "Mon 2026-06-15 05:00:00 UTC" if timer_exists else "",
            "error": "",
        },
        "service_policy": {
            "available": True,
            "service_name": "armareforger.service",
            "exists": True,
            "active": True,
            "enabled": service_enabled,
            "boot_policy": boot_policy,
            "active_state": "active",
            "sub_state": "running",
            "description": "Arma server",
            "warning": service_enabled is False,
            "warning_message": (
                "Game service is disabled; scheduled restarts will not guarantee "
                "boot-start after host reboot."
                if service_enabled is False
                else ""
            ),
            "error": "",
        },
    }


def _state(
    *,
    installed: bool = True,
    timer_exists: bool = True,
    config_exists: bool = True,
) -> ServerState:
    return ServerState(
        server_installed=installed,
        config_exists=config_exists,
        service_exists=True,
        timer_exists=timer_exists,
        service_name="armareforger.service",
        timer_name="armareforger-restart.timer",
    )


def _authed_client(tmp_path: Path, monkeypatch, page: dict | None = None):
    from armactl.web.app import create_app
    from armactl.web.routes import schedule as schedule_route

    password = "owner schedule password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    _patch_route_global(
        app,
        schedule_route.__name__,
        monkeypatch,
        "load_schedule_page",
        lambda instance: page or _schedule_page(),
    )
    client = _client(app)
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    return client


def _schedule_csrf_token(client) -> str:
    response = client.get("/schedule")
    assert response.status_code == 200
    return _form_token(response.text)


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def _patch_route_global(app, module_name: str, monkeypatch, name: str, value) -> None:
    patched = False
    module = sys.modules.get(module_name)
    module_globals = getattr(module, "__dict__", None)
    if module is not None and hasattr(module, name):
        monkeypatch.setattr(module, name, value)
        patched = True
    for route in getattr(app, "routes", []):
        endpoint = getattr(route, "endpoint", None)
        globals_dict = getattr(endpoint, "__globals__", None)
        if (
            isinstance(globals_dict, dict)
            and name in globals_dict
            and (
                globals_dict is module_globals
                or globals_dict.get("__name__") == module_name
            )
        ):
            monkeypatch.setitem(globals_dict, name, value)
            patched = True
    assert patched, f"route global was not patched: {module_name}.{name}"


def test_schedule_js_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/schedule.js", follow_redirects=False)

    assert response.status_code == 200
    assert "data-schedule-time-form" in response.text
    assert "data-add-schedule-time" in response.text
    assert "maxTimes" in response.text


def test_schedule_page_requires_authentication(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    get_response = client.get("/schedule", follow_redirects=False)
    post_response = client.post("/schedule/set", data={}, follow_redirects=False)

    assert get_response.status_code == 303
    assert get_response.headers["location"] == "/login"
    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/login"


def test_schedule_permission_denied_skips_backend(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import schedule as schedule_route

    setup_owner_user(tmp_path, "owner", "owner schedule password")
    app = create_app(data_root=tmp_path)
    _patch_route_global(
        app,
        schedule_route.__name__,
        monkeypatch,
        "load_schedule_page",
        lambda instance: None,
    )
    _patch_route_global(
        app,
        schedule_route.__name__,
        monkeypatch,
        "require_permission",
        lambda current, permission: False,
    )
    client = _client(app)
    _login(client, "owner", "owner schedule password")

    get_response = client.get("/schedule", follow_redirects=False)
    post_response = client.post("/schedule/enable", data={}, follow_redirects=False)

    assert get_response.status_code == 403
    assert post_response.status_code == 403
    assert get_response.text == "Permission denied."
    assert post_response.text == "Permission denied."


def test_schedule_post_requires_valid_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.services import schedule_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(schedule_actions, "run_schedule_action_and_audit", AssertionError)

    response = client.post(
        "/schedule/set",
        data={"csrf_token": "wrong-token", "schedule": "05:00"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_schedule_page_renders_timer_and_boot_policy_states(tmp_path: Path, monkeypatch):
    client = _authed_client(
        tmp_path,
        monkeypatch,
        _schedule_page(timer_exists=True, timer_active=True, timer_enabled=True),
    )

    response = client.get("/schedule", follow_redirects=False)

    assert response.status_code == 200
    assert "Restart Timer" in response.text
    assert "armareforger-restart.timer" in response.text
    assert "*-*-* 05:00:00" in response.text
    assert "Mon 2026-06-15 20:30:00 UTC" in response.text
    assert "Mon 2026-06-15 05:00:00 UTC" in response.text
    assert "Boot Policy" in response.text
    assert "Autostart" in response.text
    assert "enabled" in response.text
    assert "/static/js/schedule.js" in response.text
    assert 'type="time"' in response.text
    assert "data-add-schedule-time" in response.text
    assert "05:00" in response.text
    assert "20:30" in response.text
    assert "Enable Timer" in response.text
    assert "Disable Timer" in response.text
    assert "Game server autostart" in response.text
    assert "Enable Autostart" in response.text
    assert "Disable Autostart" in response.text
    assert "Confirm disable autostart" in response.text


def test_schedule_page_warns_when_game_service_autostart_disabled(
    tmp_path: Path,
    monkeypatch,
):
    client = _authed_client(
        tmp_path,
        monkeypatch,
        _schedule_page(service_enabled=False),
    )

    response = client.get("/schedule", follow_redirects=False)

    assert response.status_code == 200
    assert "disabled" in response.text
    assert "scheduled restarts will not guarantee boot-start" in response.text
    assert "shutdown" not in response.text.lower()


def test_schedule_page_renders_missing_timer_state(tmp_path: Path, monkeypatch):
    client = _authed_client(
        tmp_path,
        monkeypatch,
        _schedule_page(timer_exists=False, timer_active=False, timer_enabled=False),
    )

    response = client.get("/schedule", follow_redirects=False)

    assert response.status_code == 200
    assert "armareforger-restart.timer" in response.text
    assert "unknown" in response.text
    assert "*-*-* 05:00:00" not in response.text


def test_schedule_set_success_writes_safe_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import schedule_actions

    calls: list[tuple[str, list[str]]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )

    def update_schedule(instance: str, on_calendar: list[str]) -> list[ServiceResult]:
        calls.append((instance, on_calendar))
        return [ServiceResult(True, "installed token=backend-secret", 0)]

    monkeypatch.setattr(
        schedule_actions.service_manager,
        "update_restart_timer_schedule",
        update_schedule,
    )
    csrf_token = _schedule_csrf_token(client)

    response = client.post(
        "/schedule/set",
        data={
            "csrf_token": csrf_token,
            "schedule_time": ["05:00", "13:30", "22:00"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Restart schedule updated." in response.text
    assert "raw-schedule-secret" not in response.text
    assert calls == [
        (
            "default",
            ["*-*-* 05:00:00", "*-*-* 13:30:00", "*-*-* 22:00:00"],
        )
    ]
    event = _audit_events(tmp_path)[0]
    assert event["username"] == "owner"
    assert event["action"] == "schedule.set"
    assert event["instance"] == "default"
    assert event["target"] == "armareforger-restart.timer"
    assert event["success"] is True
    assert event["details"]["schedule_entries"] == [
        "*-*-* 05:00:00",
        "*-*-* 13:30:00",
        "*-*-* 22:00:00",
    ]
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "raw-schedule-secret" not in audit_text
    assert "backend-secret" not in audit_text


def test_schedule_set_rejects_non_time_web_input(tmp_path: Path, monkeypatch):
    from armactl.web.services import schedule_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    monkeypatch.setattr(
        schedule_actions.service_manager,
        "update_restart_timer_schedule",
        AssertionError,
    )
    csrf_token = _schedule_csrf_token(client)

    response = client.post(
        "/schedule/set",
        data={
            "csrf_token": csrf_token,
            "schedule": "05:00; password=raw-schedule-secret",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Use one to three restart times such as 05:00, 13:30." in response.text
    assert "raw-schedule-secret" not in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "schedule.set"
    assert event["success"] is False
    assert event["details"]["schedule_entries"] == []
    assert "raw-schedule-secret" not in (
        tmp_path / "logs" / "web" / "audit.log"
    ).read_text(encoding="utf-8")


def test_schedule_set_rejects_more_than_three_web_times(tmp_path: Path, monkeypatch):
    from armactl.web.services import schedule_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    monkeypatch.setattr(
        schedule_actions.service_manager,
        "update_restart_timer_schedule",
        AssertionError,
    )
    csrf_token = _schedule_csrf_token(client)

    response = client.post(
        "/schedule/set",
        data={
            "csrf_token": csrf_token,
            "schedule_time": ["01:00", "02:00", "03:00", "04:00"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Use one to three restart times such as 05:00, 13:30." in response.text


def test_schedule_set_failure_writes_safe_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import schedule_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    monkeypatch.setattr(
        schedule_actions.service_manager,
        "update_restart_timer_schedule",
        lambda instance, on_calendar: [
            ServiceResult(False, "timer failed token=route-token", 42)
        ],
    )
    csrf_token = _schedule_csrf_token(client)

    response = client.post(
        "/schedule/set",
        data={"csrf_token": csrf_token, "schedule": "05:00"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "timer failed token=***" in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "schedule.set"
    assert event["success"] is False
    assert event["exit_code"] == 42
    assert "route-token" not in (
        tmp_path / "logs" / "web" / "audit.log"
    ).read_text(encoding="utf-8")


def test_schedule_enable_disable_restart_and_autostart_routes(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import schedule_actions

    calls: list[tuple[str, str]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )

    def enable(service_name: str) -> ServiceResult:
        calls.append(("enable", service_name))
        return ServiceResult(True, "enabled", 0)

    def disable(service_name: str) -> ServiceResult:
        calls.append(("disable", service_name))
        return ServiceResult(True, "disabled", 0)

    def start(service_name: str) -> ServiceResult:
        calls.append(("start", service_name))
        return ServiceResult(True, "restart helper started", 0)

    monkeypatch.setattr(schedule_actions.service_manager, "enable_service", enable)
    monkeypatch.setattr(schedule_actions.service_manager, "disable_service", disable)
    monkeypatch.setattr(schedule_actions.service_manager, "start_service", start)
    csrf_token = _schedule_csrf_token(client)

    enable_response = client.post(
        "/schedule/enable",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    disable_response = client.post(
        "/schedule/disable",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    missing_confirm_response = client.post(
        "/schedule/restart-now",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    restart_response = client.post(
        "/schedule/restart-now",
        data={"csrf_token": csrf_token, "confirm": "schedule.restart-now"},
        follow_redirects=False,
    )
    autostart_enable_response = client.post(
        "/schedule/autostart/enable",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    missing_autostart_confirm_response = client.post(
        "/schedule/autostart/disable",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    autostart_disable_response = client.post(
        "/schedule/autostart/disable",
        data={"csrf_token": csrf_token, "confirm": "service.autostart-disable"},
        follow_redirects=False,
    )

    assert enable_response.status_code == 200
    assert disable_response.status_code == 200
    assert missing_confirm_response.status_code == 400
    assert "Confirmation is required to restart the server now." in missing_confirm_response.text
    assert restart_response.status_code == 200
    assert autostart_enable_response.status_code == 200
    assert missing_autostart_confirm_response.status_code == 400
    assert (
        "Confirmation is required to disable game server autostart."
        in missing_autostart_confirm_response.text
    )
    assert autostart_disable_response.status_code == 200
    assert calls == [
        ("enable", "armareforger-restart.timer"),
        ("disable", "armareforger-restart.timer"),
        ("start", "armareforger-restart.service"),
        ("enable", "armareforger.service"),
        ("disable", "armareforger.service"),
    ]
    assert [event["action"] for event in _audit_events(tmp_path)] == [
        "schedule.enable",
        "schedule.disable",
        "schedule.restart-now",
        "service.autostart-enable",
        "service.autostart-disable",
    ]


def test_schedule_autostart_actions_require_installed_service(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import schedule_actions

    client = _authed_client(tmp_path, monkeypatch)
    state = _state(installed=True, timer_exists=True)
    state.service_exists = False
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: state,
    )
    monkeypatch.setattr(schedule_actions.service_manager, "enable_service", AssertionError)
    csrf_token = _schedule_csrf_token(client)

    response = client.post(
        "/schedule/autostart/enable",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Server service is not installed." in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "service.autostart-enable"
    assert event["success"] is False


def test_dashboard_links_to_schedule_page_when_permission_allows(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    def snapshot(instance: str, *, web_config=None) -> dict:
        assert instance == "default"
        assert web_config is not None
        return {
            "instance": "default",
            "lifecycle": "running",
            "installed": True,
            "running": True,
            "overview": {"label": "running"},
            "service": {
                "active_state": "active",
                "enabled": False,
                "service_name": "armareforger.service",
            },
            "players": {"count_text": "0 / 64"},
            "fps_metrics": {"available": True, "fps": 60, "fps_text": "60"},
            "host_metrics": {
                "available": True,
                "cpu_text": "1%",
                "memory_text": "1 / 2 GiB",
                "disk_text": "10 / 20 GiB",
                "uptime_text": "1h",
            },
            "config": {
                "server_name": "Schedule Link Server",
                "scenario_id": "Scenario.conf",
                "max_players": 64,
            },
            "mods": {"count": 0, "preview_labels": []},
            "operational_status": {"message": "Ready", "age_text": "1s"},
            "errors": [],
        }

    setup_owner_user(tmp_path, "owner", "owner dashboard password")
    app = create_app(data_root=tmp_path)
    _patch_route_global(
        app,
        dashboard.__name__,
        monkeypatch,
        "load_dashboard_snapshot",
        snapshot,
    )
    client = _client(app)
    _login(client, "owner", "owner dashboard password")

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert 'href="/schedule"' in response.text
    assert "Restart timer controls" in response.text
    assert "Boot Policy" in response.text
    assert "scheduled restarts will not guarantee boot-start" in response.text
    assert client.cookies.get(SESSION_COOKIE_NAME)
