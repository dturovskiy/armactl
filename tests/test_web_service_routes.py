"""Route tests for web service action endpoints."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from web_route_helpers import (
    _client,
    _csrf_cookie_name,
    _login,
    _login_action_csrf_token,
    _session_cookie_name,
)

from armactl.metrics import (
    HostMetrics,
    ProcessMetrics,
    ServerFpsMetrics,
    ServerOperationalStatus,
)
from armactl.player_view import PlayerView
from armactl.state import PortInfo, ServerState
from armactl.status_summary import ConfigSummary, ModsSummary, ModSummaryEntry
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username


def _install_dashboard_model_fakes(monkeypatch) -> None:
    from armactl.web.page_models import bot as bot_model
    from armactl.web.page_models import dashboard as dashboard_model

    state = ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        service_exists=True,
        timer_exists=True,
        server_running=True,
        instance_root="/srv/armactl-data/default",
        install_dir="/srv/armactl-data/default/server",
        config_path="/srv/armactl-data/default/config/config.json",
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )
    monkeypatch.setattr(
        dashboard_model.discovery,
        "discover",
        lambda instance, save=False: state,
    )
    class FakeServiceAdapter:
        def get_service_status(self, service_name):
            return {
                "service_name": service_name,
                "active": True,
                "enabled": True,
                "active_state": "active",
                "sub_state": "running",
                "main_pid": 123,
            }

        def get_timer_status(self, timer_name):
            return {
                "timer_name": timer_name,
                "active": True,
                "enabled": True,
                "schedule": "*-*-* 06:00:00",
                "next_run": "Fri 2026-06-12 06:00:00 UTC",
            }

    monkeypatch.setattr(dashboard_model, "get_service_adapter", lambda: FakeServiceAdapter())
    monkeypatch.setattr(
        dashboard_model.status_summary,
        "load_status_summaries",
        lambda config_path: (
            ConfigSummary(
                True,
                server_name="Mock Server",
                scenario_id="Scenario.conf",
                max_players=64,
                bind_port=2001,
                a2s_port=17777,
                rcon_port=19999,
                visible=True,
                battleye=True,
            ),
            ModsSummary(
                True,
                count=2,
                preview=[ModSummaryEntry("mod-a", "Core Mod")],
                remaining_count=1,
            ),
        ),
    )
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_host_metrics",
        lambda: HostMetrics(
            True,
            cpu_percent=12.0,
            memory_used_bytes=512,
            memory_total_bytes=1024,
            disk_used_bytes=2048,
            disk_total_bytes=4096,
            load_average_1m=0.1,
            load_average_5m=0.2,
            load_average_15m=0.3,
            uptime_seconds=3661,
        ),
    )
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_service_runtime_metrics",
        lambda service: ProcessMetrics(
            True,
            pid=123,
            cpu_percent=2.5,
            memory_rss_bytes=2048,
        ),
    )
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_server_fps_metrics",
        lambda config_dir: ServerFpsMetrics(
            True,
            fps=59.8,
            frame_avg_ms=16.7,
            frame_max_ms=24.0,
            engine_memory_kb=512,
            age_seconds=10,
            source=str(config_dir / "logs" / "latest" / "console.log"),
        ),
    )
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_server_operational_status",
        lambda config_dir: ServerOperationalStatus(
            True,
            state="ready",
            severity="success",
            message="Ready",
            age_seconds=12,
            source=str(config_dir),
        ),
    )
    monkeypatch.setattr(
        dashboard_model.player_view,
        "query_player_view",
        lambda instance, **kwargs: PlayerView(True, current=3, max_players=64),
    )
    monkeypatch.setattr(
        dashboard_model.ports,
        "check_server_ports",
        lambda game_port, a2s_port, rcon_port: {
            "game": {"port": game_port, "listening": True},
            "a2s": {"port": a2s_port, "listening": True},
            "rcon": {"port": rcon_port, "listening": False},
        },
    )
    monkeypatch.setattr(
        dashboard_model.sat_admin_guard,
        "inspect_sat_admin_config",
        lambda config_path: SimpleNamespace(
            to_dict=lambda: {
                "available": True,
                "valid_json": True,
                "desired_admins": [],
                "missing_mappings": [],
                "default_only_admins": False,
                "default_only_game_masters": False,
                "missing_admins": [],
                "missing_game_masters": [],
                "warning": "",
            }
        ),
    )
    monkeypatch.setattr(
        bot_model.bot_config,
        "load_bot_config",
        lambda instance: SimpleNamespace(
            enabled=False,
            token="",
            admin_chat_ids=[],
            language="uk",
            env_path=Path("/srv/armactl-data/default/bot/.env"),
        ),
    )
    monkeypatch.setattr(
        bot_model.paths,
        "bot_service_file",
        lambda: Path("/nonexistent/armactl-bot.service"),
    )


def _audit_log_text(data_root: Path) -> str:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return audit_path.read_text(encoding="utf-8")


def _audit_events(data_root: Path) -> list[dict]:
    events = [json.loads(line) for line in _audit_log_text(data_root).splitlines()]
    return [event for event in events if (event.get('details') or {}).get('phase') != 'intent']


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
    from armactl.platform.service_adapter import ServiceResult
    from armactl.web.services import service_actions

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service_actions.discovery,
        "discover",
        lambda instance, save=False: _service_action_state(running=running),
    )

    class FakeServiceAdapter:
        def service_unit_name(self, instance: str = "default") -> str:
            if instance == "default":
                return "armareforger.service"
            return f"armareforger@{instance}.service"

        def start_service(self, service_name: str) -> ServiceResult:
            calls.append(("start", service_name))
            return ServiceResult(success, message, exit_code)

        def stop_service(self, service_name: str) -> ServiceResult:
            calls.append(("stop", service_name))
            return ServiceResult(success, message, exit_code)

        def restart_service(self, service_name: str) -> ServiceResult:
            calls.append(("restart", service_name))
            return ServiceResult(success, message, exit_code)

    monkeypatch.setattr(service_actions, "get_service_adapter", FakeServiceAdapter)
    return calls


def test_unauthenticated_service_action_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.post("/service/start", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_service_action_permission_denied_returns_controlled_403(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.services import service_actions

    password = "owner action password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())

    def fail_action(*args, **kwargs):
        raise AssertionError("backend action should not be called")

    monkeypatch.setattr(service_actions, "run_service_action_and_audit", fail_action)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    csrf_token = login_response.cookies.get(_csrf_cookie_name(client))

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
    calls = _stub_service_backend(
        monkeypatch,
        running=False,
        success=True,
        message="started token=route-secret",
    )
    client = _client(create_app(data_root=tmp_path))
    csrf_token = _login_action_csrf_token(client, "owner", password)

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


def test_service_stop_and_restart_require_confirmation(
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
    csrf_token = _login_action_csrf_token(client, "owner", password)

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
    _install_dashboard_model_fakes(monkeypatch)
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
    csrf_token = _login_action_csrf_token(client, "owner", password)

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
    csrf_token = _login_action_csrf_token(client, "owner", password)

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
    csrf_token = _login_action_csrf_token(client, "owner", password)

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
    _install_dashboard_model_fakes(monkeypatch)
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
    csrf_token = _login_action_csrf_token(client, "owner", password)

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
    calls = _stub_service_backend(
        monkeypatch,
        running=False,
        success=False,
        message="failed password=backend-secret token=route-token",
        exit_code=7,
    )
    client = _client(create_app(data_root=tmp_path))
    csrf_token = _login_action_csrf_token(client, "owner", password)

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
    _stub_service_backend(
        monkeypatch,
        running=False,
        success=False,
        message="failed password=backend-secret token=backend-token",
        exit_code=9,
    )
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(_session_cookie_name(client))
    csrf_token = login_response.cookies.get(_csrf_cookie_name(client))
    assert csrf_token

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
    _stub_service_backend(monkeypatch, running=True, success=True, message='restarted')
    db_path = tmp_path / 'web' / 'web.db'
    mark_restart_pending(db_path, kind=KIND_CONFIG, source_action='config.save', username='owner')

    def fail_outcome(*args, **kwargs):
        if (kwargs.get('details') or {}).get('phase') == 'outcome':
            raise AuditLogError('disk full')
        return append_audit_event(*args, **kwargs)

    monkeypatch.setattr(service_actions, 'append_audit_event', fail_outcome)
    client = _client(create_app(data_root=tmp_path))
    csrf_token = _login_action_csrf_token(client, 'owner', password)
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
