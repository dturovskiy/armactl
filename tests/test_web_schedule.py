"""Tests for restart schedule web routes and actions."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning
from web_route_helpers import _session_cookie_name

from armactl.platform.service_adapter import ServiceResult
from armactl.state import ServerState
from armactl.web.auth.setup import setup_owner_user


def _client(app, *, raise_server_exceptions: bool = True):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(app, raise_server_exceptions=raise_server_exceptions)


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


class _FakeScheduleAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.update_restart_timer_schedule_handler = None
        self.start_service_result = ServiceResult(True, "restart helper started", 0)
        self.enable_service_result = ServiceResult(True, "enabled", 0)
        self.disable_service_result = ServiceResult(True, "disabled", 0)

    def service_unit_name(self, instance: str = "default") -> str:
        if instance == "default":
            return "armareforger.service"
        return f"armareforger@{instance}.service"

    def restart_service_unit_name(self, instance: str = "default") -> str:
        if instance == "default":
            return "armareforger-restart.service"
        return f"armareforger-restart@{instance}.service"

    def timer_unit_name(self, instance: str = "default") -> str:
        if instance == "default":
            return "armareforger-restart.timer"
        return f"armareforger-restart@{instance}.timer"

    def format_schedule_for_input(self, schedule_entries: list[str]) -> str:
        display_times: list[str] = []
        for entry in schedule_entries:
            if entry.startswith("*-*-* ") and entry.endswith(":00"):
                display_times.append(entry.removeprefix("*-*-* ")[:-3])
            else:
                return "; ".join(schedule_entries)
        return ", ".join(display_times)

    def update_restart_timer_schedule(
        self,
        instance: str,
        on_calendar: list[str],
    ) -> list[ServiceResult]:
        if self.update_restart_timer_schedule_handler is not None:
            return self.update_restart_timer_schedule_handler(instance, on_calendar)
        self.calls.append(("update_restart_timer_schedule", (instance, list(on_calendar))))
        return [ServiceResult(True, "installed", 0)]

    def enable_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("enable", service_name))
        return self.enable_service_result

    def disable_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("disable", service_name))
        return self.disable_service_result

    def start_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("start", service_name))
        return self.start_service_result


def _patch_schedule_adapter(monkeypatch, schedule_actions, adapter: _FakeScheduleAdapter) -> None:
    monkeypatch.setattr(schedule_actions, "get_service_adapter", lambda: adapter)


def _authed_client(tmp_path: Path, monkeypatch, page: dict | None = None):
    from armactl.web.app import create_app
    from armactl.web.page_models import schedule as schedule_page_model

    password = "owner schedule password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(
        schedule_page_model,
        "load_schedule_page",
        lambda instance: page or _schedule_page(),
    )
    app = create_app(data_root=tmp_path)
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
    events = [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines()]
    return [event for event in events if (event.get('details') or {}).get('phase') != 'intent']


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


def test_schedule_permission_denied_skips_backend(
    tmp_path: Path, monkeypatch, set_web_owner_permissions
):
    from armactl.web.app import create_app
    from armactl.web.page_models import schedule as schedule_page_model

    setup_owner_user(tmp_path, "owner", "owner schedule password")
    set_web_owner_permissions(set())
    monkeypatch.setattr(schedule_page_model, "load_schedule_page", AssertionError)
    app = create_app(data_root=tmp_path)
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
    adapter = _FakeScheduleAdapter()
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )

    def update_schedule(instance: str, on_calendar: list[str]) -> list[ServiceResult]:
        calls.append((instance, on_calendar))
        return [ServiceResult(True, "installed token=backend-secret", 0)]

    adapter.update_restart_timer_schedule_handler = update_schedule
    _patch_schedule_adapter(monkeypatch, schedule_actions, adapter)
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

    adapter = _FakeScheduleAdapter()
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    _patch_schedule_adapter(monkeypatch, schedule_actions, adapter)
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
    assert adapter.calls == []
    assert "raw-schedule-secret" not in (
        tmp_path / "logs" / "web" / "audit.log"
    ).read_text(encoding="utf-8")


def test_schedule_set_rejects_more_than_three_web_times(tmp_path: Path, monkeypatch):
    from armactl.web.services import schedule_actions

    adapter = _FakeScheduleAdapter()
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    _patch_schedule_adapter(monkeypatch, schedule_actions, adapter)
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
    assert adapter.calls == []


def test_schedule_set_failure_writes_safe_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import schedule_actions

    adapter = _FakeScheduleAdapter()
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    adapter.update_restart_timer_schedule_handler = lambda instance, on_calendar: [
        ServiceResult(False, "timer failed token=route-token", 42)
    ]
    _patch_schedule_adapter(monkeypatch, schedule_actions, adapter)
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
    from armactl.web.services import pending_work, schedule_actions

    adapter = _FakeScheduleAdapter()
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    _patch_schedule_adapter(monkeypatch, schedule_actions, adapter)
    csrf_token = _schedule_csrf_token(client)
    db_path = tmp_path / "web" / "web.db"
    pending_work.mark_restart_pending(
        db_path,
        instance="default",
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )
    assert pending_work.list_pending_work(db_path, instance="default")

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
    assert pending_work.list_pending_work(db_path, instance="default") == []
    assert adapter.calls == [
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


def test_schedule_restart_now_clears_pending_work_through_service(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import pending_work, schedule_actions

    adapter = _FakeScheduleAdapter()
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    db_path = tmp_path / "web" / "web.db"
    pending_work.mark_restart_pending(
        db_path,
        instance="default",
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )

    result = schedule_actions.run_schedule_action_and_audit(
        schedule_actions.ACTION_RESTART_NOW,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=db_path,
        adapter=adapter,
    )

    assert result.success is True
    assert result.pending_restart_work_warning == ""
    assert pending_work.list_pending_work(db_path, instance="default") == []
    assert adapter.calls == [("start", "armareforger-restart.service")]


def test_schedule_unexpected_restart_exception_does_not_clear_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import schedule as schedule_page_model
    from armactl.web.services import pending_work, schedule_actions

    setup_owner_user(tmp_path, "owner", "owner schedule password")
    monkeypatch.setattr(
        schedule_page_model,
        "load_schedule_page",
        lambda instance: _schedule_page(),
    )
    app = create_app(data_root=tmp_path)

    def fail_action(*args, **kwargs):
        raise RuntimeError("unexpected schedule bug token=raw-schedule-secret")

    monkeypatch.setattr(schedule_actions, "run_schedule_action_and_audit", fail_action)
    db_path = tmp_path / "web" / "web.db"
    pending_work.mark_restart_pending(
        db_path,
        instance="default",
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )
    client = _client(app, raise_server_exceptions=False)
    _login(client, "owner", "owner schedule password")
    csrf_token = _schedule_csrf_token(client)

    response = client.post(
        "/schedule/restart-now",
        data={"csrf_token": csrf_token, "confirm": "schedule.restart-now"},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Internal Server Error" in response.text
    assert "Schedule action is unavailable." not in response.text
    assert "raw-schedule-secret" not in response.text
    assert "Traceback" not in response.text
    assert pending_work.get_pending_work(db_path, kind=pending_work.KIND_CONFIG) is not None
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_schedule_autostart_actions_require_installed_service(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import schedule_actions

    adapter = _FakeScheduleAdapter()
    client = _authed_client(tmp_path, monkeypatch)
    state = _state(installed=True, timer_exists=True)
    state.service_exists = False
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: state,
    )
    _patch_schedule_adapter(monkeypatch, schedule_actions, adapter)
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
    assert adapter.calls == []


def test_dashboard_links_to_schedule_page_when_permission_allows(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_page_model

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
    monkeypatch.setattr(dashboard_page_model, "load_dashboard_snapshot", snapshot)
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", "owner dashboard password")

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert 'href="/schedule"' in response.text
    assert "Restart timer controls" in response.text
    assert "Boot Policy" in response.text
    assert "scheduled restarts will not guarantee boot-start" in response.text
    assert client.cookies.get(_session_cookie_name(client))


def test_schedule_restart_now_backend_success_audit_failure_clears_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import pending_work, schedule_actions
    from armactl.web.services.audit import AuditLogError, append_audit_event

    adapter = _FakeScheduleAdapter()
    adapter.start_service_result = ServiceResult(True, "restart queued", 0)
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        schedule_actions.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    _patch_schedule_adapter(monkeypatch, schedule_actions, adapter)
    db_path = tmp_path / "web" / "web.db"
    pending_work.mark_restart_pending(
        db_path,
        instance="default",
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )

    def fail_outcome(*args, **kwargs):
        if (kwargs.get("details") or {}).get("phase") == "outcome":
            raise AuditLogError("disk full")
        return append_audit_event(*args, **kwargs)

    monkeypatch.setattr(schedule_actions, "append_audit_event", fail_outcome)
    csrf_token = _schedule_csrf_token(client)
    response = client.post(
        "/schedule/restart-now",
        data={"csrf_token": csrf_token, "confirm": "schedule.restart-now"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Schedule action completed but audit logging failed." in response.text
    assert pending_work.get_pending_work(db_path, kind=pending_work.KIND_CONFIG) is None
