"""Route and template tests for the web dashboard."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from web_route_helpers import (
    _action_csrf_token,
    _client,
    _login,
    _session_cookie_name,
    _set_cookie,
)

from armactl.metrics import (
    HostMetrics,
    ProcessMetrics,
    ServerFpsMetrics,
    ServerIncident,
    ServerOperationalStatus,
)
from armactl.player_view import PlayerView
from armactl.state import PortInfo, ServerState
from armactl.status_summary import ConfigSummary, ModsSummary, ModSummaryEntry
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username
from armactl.web.i18n import LANGUAGE_COOKIE_NAME, THEME_COOKIE_NAME
from armactl.web.runtime import ensure_web_runtime, save_web_runtime_config


def _dashboard_state(lifecycle: str) -> ServerState:
    if lifecycle == "not_installed":
        return ServerState()

    instance_root = "/srv/armactl-data/default"
    install_dir = f"{instance_root}/server"
    config_path = f"{instance_root}/config/config.json"
    if lifecycle == "incomplete":
        return ServerState(
            server_installed=True,
            binary_exists=True,
            config_exists=False,
            service_exists=False,
            timer_exists=False,
            server_running=False,
            instance_root=instance_root,
            install_dir=install_dir,
            config_path=config_path,
            ports=PortInfo(game=2001, a2s=17777, rcon=19999),
        )

    return ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        service_exists=True,
        timer_exists=True,
        server_running=lifecycle == "running",
        instance_root=instance_root,
        install_dir=install_dir,
        config_path=config_path,
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )


def _service_status(lifecycle: str) -> dict[str, object]:
    if lifecycle == "running":
        return {
            "service_name": "armareforger.service",
            "active": True,
            "enabled": True,
            "active_state": "active",
            "sub_state": "running",
            "main_pid": 123,
        }
    if lifecycle == "starting":
        return {
            "service_name": "armareforger.service",
            "active": True,
            "enabled": True,
            "active_state": "activating",
            "sub_state": "start",
            "main_pid": 123,
        }
    if lifecycle == "stopping":
        return {
            "service_name": "armareforger.service",
            "active": False,
            "enabled": True,
            "active_state": "deactivating",
            "sub_state": "stop-sigterm",
            "main_pid": 123,
        }
    return {
        "service_name": "armareforger.service",
        "active": False,
        "enabled": True,
        "active_state": "inactive",
        "sub_state": "dead",
        "main_pid": 0,
    }


def _install_dashboard_model_fakes(
    monkeypatch,
    *,
    lifecycle: str = "running",
    host_metrics_error: bool = False,
    fps_available: bool = True,
    players_available: bool = True,
) -> list[str]:
    from armactl.web.page_models import bot as bot_model
    from armactl.web.page_models import dashboard as dashboard_model

    calls: list[str] = []
    state = _dashboard_state(lifecycle)

    def discover(instance: str, save: bool = False) -> ServerState:
        assert save is False
        calls.append(instance)
        return state

    def host_metrics() -> HostMetrics:
        if host_metrics_error:
            raise RuntimeError("host boom")
        return HostMetrics(
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
        )

    def fps_metrics(config_dir: Path) -> ServerFpsMetrics:
        if lifecycle != "running":
            return ServerFpsMetrics(False, error="server is not running")
        if not fps_available:
            return ServerFpsMetrics(False, error="waiting for telemetry")
        return ServerFpsMetrics(
            True,
            fps=59.8,
            frame_avg_ms=16.7,
            frame_max_ms=24.0,
            engine_memory_kb=512,
            age_seconds=10,
            source=str(config_dir / "logs" / "latest" / "console.log"),
        )

    def operational_status(config_dir: Path) -> ServerOperationalStatus:
        if lifecycle == "starting":
            return ServerOperationalStatus(
                True,
                state="starting",
                severity="info",
                message="Waiting for server telemetry",
                age_seconds=0,
                source=str(config_dir),
            )
        return ServerOperationalStatus(
            True,
            state="ready",
            severity="success",
            message="Ready",
            age_seconds=12,
            source=str(config_dir),
        )

    monkeypatch.setattr(dashboard_model.discovery, "discover", discover)

    class FakeServiceAdapter:
        def get_service_status(self, service_name):
            return _service_status(lifecycle)

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
    monkeypatch.setattr(dashboard_model.metrics, "query_host_metrics", host_metrics)
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_service_runtime_metrics",
        lambda service: ProcessMetrics(
            bool(service.get("active")),
            pid=int(service.get("main_pid") or 0),
            cpu_percent=2.5 if service.get("active") else None,
            memory_rss_bytes=2048 if service.get("active") else None,
            error="" if service.get("active") else "service is not active",
        ),
    )
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_server_fps_metrics",
        fps_metrics,
    )
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_server_operational_status",
        operational_status,
    )
    monkeypatch.setattr(
        dashboard_model.player_view,
        "query_player_view",
        lambda instance, **kwargs: PlayerView(
            players_available,
            current=3 if players_available else None,
            max_players=64 if players_available else None,
        ),
    )

    def current_roster_snapshot(instance: str, **kwargs):
        cache = dashboard_model.player_current_cache
        snapshot = cache.CurrentRosterSnapshot(
            instance=instance,
            players=(),
            source="rcon.roster" if players_available else "unavailable",
            status="available" if players_available else "unavailable",
            error="" if players_available else "player view is not available",
            collected_at="2026-06-16T12:00:00+00:00",
            observed_count=3 if players_available else 0,
            count_source="rcon" if players_available else "unavailable",
            roster_available=players_available,
            roster_configured=players_available,
        )
        return cache.CurrentRosterSnapshotResult(
            snapshot=snapshot,
            age_seconds=0,
            is_stale=False,
            cache_status="test",
        )

    monkeypatch.setattr(
        dashboard_model.player_current_cache,
        "load_current_roster_snapshot",
        current_roster_snapshot,
    )

    monkeypatch.setattr(
        dashboard_model.ports,
        "check_server_ports",
        lambda game_port, a2s_port, rcon_port: {
            "game": {"port": game_port, "listening": lifecycle == "running"},
            "a2s": {"port": a2s_port, "listening": lifecycle == "running"},
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
                "desired_admins": ["21761a7f-c9b4-4bff-8375-b4b43abb95ec"],
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
    return calls


def _fail_if_dashboard_model_loads(monkeypatch) -> None:
    from armactl.web.page_models import dashboard as dashboard_model

    def fail_discovery(*args, **kwargs):
        raise AssertionError("dashboard model should not load")

    monkeypatch.setattr(dashboard_model.discovery, "discover", fail_discovery)


def _install_dashboard_model_failure(monkeypatch) -> None:
    from armactl.web.page_models import dashboard as dashboard_model

    def fail_discovery(*args, **kwargs):
        raise RuntimeError("boom with traceback-looking details")

    monkeypatch.setattr(dashboard_model.discovery, "discover", fail_discovery)


def test_session_cookie_authenticates_dashboard(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _install_dashboard_model_fakes(monkeypatch)
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


def test_dashboard_live_refresh_status_labels_are_localized(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "data-dashboard-live-status" in response.text
    assert 'data-fresh-label="Оновлення наживо активне"' in response.text
    assert (
        'data-paused-label="Оновлення наживо призупинене або перепідключається."'
        in response.text
    )
    assert 'data-stale-label="Дані dashboard можуть бути застарілими."' in response.text



def test_authenticated_owner_can_fetch_dashboard_status_json(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _install_dashboard_model_fakes(monkeypatch)
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
    assert payload["status"] == {
        "state": "ready",
        "severity": "success",
        "message": "Ready",
    }
    assert payload["fields"]["heading"] == "Mock Server"
    assert payload["fields"]["overview.players"] == "3 / 64"
    assert payload["field_states"]["overview.players"] == {"loading": False}
    assert payload["fields"]["live.fps"] == "59.8"
    assert payload["host"]["cpu"] == "12.0%"
    assert payload["mods"]["count"] == 2
    assert payload["metrics"]["fps"] == {
        "available": True,
        "loading": False,
        "value": 59.8,
        "percent": 99.67,
        "text": "59.8",
    }
    assert payload["metrics"]["cpu"]["percent"] == 12.0
    assert payload["metrics"]["memory"]["percent"] == 50.0
    assert payload["metrics"]["disk"]["percent"] == 50.0
    assert [action["name"] for action in payload["actions"]] == [
        "stop",
        "restart",
        "update-check",
    ]
    assert calls == ["default"]


def test_dashboard_players_use_current_roster_cache_before_direct_probe(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_model
    from armactl.web.services import player_current_cache

    real_load_current_roster_snapshot = player_current_cache.load_current_roster_snapshot
    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
    player_current_cache.clear_current_roster_cache()
    player_current_cache.store_persistent_current_roster_snapshot(
        player_current_cache.CurrentRosterSnapshot(
            instance="default",
            players=(
                player_current_cache.CurrentRosterPlayerSnapshot(
                    display_name="Alpha",
                    reliable_id="11111111-1111-4111-8111-111111111111",
                    source="rcon.guid",
                ),
            ),
            source="rcon.roster",
            status="available",
            error="",
            collected_at=datetime.now(timezone.utc).isoformat(),
            observed_count=5,
            count_source="rcon",
            roster_available=True,
            roster_configured=True,
        ),
        data_root=tmp_path,
    )
    monkeypatch.setattr(
        dashboard_model.player_current_cache,
        "load_current_roster_snapshot",
        real_load_current_roster_snapshot,
    )
    monkeypatch.setattr(
        dashboard_model.player_view,
        "query_player_view",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("direct player probe should not be called")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard/status.json", follow_redirects=False)

    assert response.status_code == 200
    payload = response.json()
    assert payload["fields"]["overview.players"] == "5 / 64"
    assert payload["players"]["text"] == "5 / 64"


def test_unauthenticated_dashboard_status_json_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/dashboard/status.json", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_status_json_permission_denied_returns_controlled_403(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    _fail_if_dashboard_model_loads(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard/status.json", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_dashboard_status_json_does_not_include_secrets_or_paths(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner status secret password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    _install_dashboard_model_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(_session_cookie_name(client))

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
    set_web_owner_permissions,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    _fail_if_dashboard_model_loads(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_password_hash_and_session_token_do_not_appear_in_dashboard_html(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner secret password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    _install_dashboard_model_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(_session_cookie_name(client))

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
    calls = _install_dashboard_model_fakes(monkeypatch)
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
    assert 'action="/service/restart-at-fps"' in root_response.text
    assert 'href="/config"' in root_response.text
    assert 'href="/mods"' in root_response.text
    assert 'href="/admins"' in root_response.text
    assert 'href="/bot"' in root_response.text
    assert 'href="/jobs"' in root_response.text
    assert 'href="/files"' in root_response.text
    assert 'href="/logs"' in root_response.text
    assert 'href="/updates"' in root_response.text
    assert "Pending operator work" in root_response.text
    assert "No pending operator work." in root_response.text
    assert "All saved changes are applied. No action is required." in root_response.text
    assert "Background jobs" not in root_response.text
    assert "No background jobs." not in root_response.text
    assert "/static/js/dashboard.js" in root_response.text
    assert "/static/js/service_actions.js" in root_response.text
    assert 'data-service-action-form' in root_response.text
    assert 'data-service-action="stop"' in root_response.text
    assert 'data-service-action="restart-at-fps"' in root_response.text
    assert 'data-service-action-label="Stopping server..."' in root_response.text
    assert 'data-service-action-label="Restarting server..."' in root_response.text
    assert 'data-service-action-submit' in root_response.text
    assert re.search(
        r'<input type="checkbox" name="confirm" value="stop" required>',
        root_response.text,
    )
    assert re.search(
        r'<input type="checkbox" name="confirm" value="restart-at-fps" required>',
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


def test_dashboard_keeps_recent_crash_and_suspect_visible_after_recovery(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_model

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
    monkeypatch.setattr(
        dashboard_model.metrics,
        "query_recent_server_incidents",
        lambda config_dir, **kwargs: (
            ServerIncident(
                occurred_at="2026-09-06T16:29:42+00:00",
                kind="runtime_crash",
                severity="error",
                summary="Native game crash (crash dump)",
                suspect="ATGM / CLBR weapon stack",
                confidence="high",
                reason="Kornet prefab and CLBR weapon code appeared immediately before "
                "the native crash.",
                evidence=(
                    "SpawnEntityPrefab Prefabs/Weapons/Tripods/Tripod_KORNET.et",
                    "Application crashed! Generated memory dump",
                ),
            ),
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)
    incidents_response = client.get("/incidents", follow_redirects=False)
    payload = client.get("/dashboard/status.json", follow_redirects=False).json()

    assert response.status_code == 200
    assert 'href="/incidents"' in response.text
    assert "Recent server incidents" not in response.text
    assert "ATGM / CLBR weapon stack" in response.text
    assert "Tripod_KORNET.et" not in response.text
    assert 'data-dashboard-field="incidents.count"' in response.text
    assert incidents_response.status_code == 200
    assert "Server incidents" in incidents_response.text
    assert "ATGM / CLBR weapon stack" in incidents_response.text
    assert "Tripod_KORNET.et" in incidents_response.text
    assert payload["incidents"] == {
        "count": 1,
        "latest_suspect": "ATGM / CLBR weapon stack",
    }
    assert payload["fields"]["incidents.count"] == "1"
    assert payload["fields"]["incidents.latest_suspect"] == "ATGM / CLBR weapon stack"


def test_dashboard_js_static_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/dashboard.js", follow_redirects=False)

    assert response.status_code == 200
    assert "dashboard/status.json" in response.text
    assert "data-dashboard-root" in response.text
    assert "data-dashboard-meter" in response.text
    assert "dashboardLoading" in response.text
    assert "field_states" in response.text
    assert "data-dashboard-sparkline" not in response.text
    assert "metricHistory" not in response.text
    assert "ARMACTL_WEB_SESSION_SECRET" not in response.text
    assert "csrf_token" not in response.text
    assert "password" not in response.text.lower()


def test_dashboard_stopped_server_shows_start_fps_actions(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopped")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert response.text.count('class="fps-selector"') == 1
    assert response.text.count('name="max_fps"') == 2
    assert 'value="60" form="service-start-form" checked' in response.text
    assert 'value="120" form="service-start-form"' in response.text
    assert '<input type="hidden" name="max_fps"' not in response.text
    assert response.text.count('action="/service/start-at-fps"') == 1
    assert "Start at 60 FPS" not in response.text
    assert "Start at 120 FPS" not in response.text
    assert 'action="/service/start-at-fps"' in response.text
    assert 'action="/jobs/server/update-check"' in response.text
    assert 'action="/service/stop"' not in response.text
    assert 'action="/service/restart-at-fps"' not in response.text
    assert 'data-service-action="start-at-fps"' in response.text
    assert 'data-service-action-label="Starting server..."' in response.text
    assert "/static/js/service_actions.js" in response.text
    assert "Server snapshot" in response.text
    assert 'href="/config"' in response.text


def test_dashboard_shows_compact_profile_selector_and_links_to_full_controls(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import updates as updates_page_model

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopped")
    config_path = tmp_path / "default" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        updates_page_model,
        "load_updates_page",
        lambda *args, **kwargs: {
            "instance": "default",
            "server_installed": True,
            "server_running": False,
            "version": {
                "check_state": "up_to_date",
                "installed": "200",
                "latest": "200",
            },
            "compatibility": {
                "available": True,
                "active_mode": "vanilla",
                "parked_modded_available": True,
                "parked_profile_name": "serhiivka-modded",
                "parked_profile_compatibility": {
                    "status": "compatible",
                    "label": "Ready for current build",
                    "css_class": "success",
                },
            },
            "profiles": [
                {
                    "name": "vanilla",
                    "active": True,
                    "mode": "vanilla",
                    "scenario_id": "Everon.conf",
                    "mod_count": 0,
                },
                {
                    "name": "serhiivka-modded",
                    "active": False,
                    "mode": "modded",
                    "scenario_id": "Serhiivka.conf",
                    "mod_count": 35,
                },
            ],
            "policy": {"automatic_vanilla_fallback": True},
        },
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert 'id="compatibility-profiles"' in response.text
    assert response.text.count('class="profile-quick-control"') == 1
    assert 'action="/updates/profile/select"' in response.text
    assert 'name="profile_selection"' in response.text
    assert '<option value="" selected disabled>vanilla · Active</option>' in response.text
    assert (
        '<option value="retry-modded">serhiivka-modded · Ready for current build</option>'
        in response.text
    )
    assert "Parked modded profile" not in response.text
    assert 'name="return_to" value="dashboard"' in response.text
    assert 'href="/updates#compatibility-profiles"' in response.text
    assert "Manual profile control" not in response.text
    assert "game.scenarioId and game.mods" not in response.text
    assert 'action="/updates/auto-fallback"' not in response.text
    assert "Disable automatic fallback" not in response.text
    assert "<table>" not in response.text.split('id="compatibility-profiles"', 1)[1].split(
        "</section>", 1
    )[0]


def test_dashboard_starting_server_hides_service_actions(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="starting")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "starting" in response.text
    assert (
        "Server is starting; restart and stop are unavailable until systemd leaves startup."
        in response.text
    )
    assert "action=\"/service/start\"" not in response.text
    assert "action=\"/service/stop\"" not in response.text
    assert "action=\"/service/restart\"" not in response.text
    assert "data-service-action-form" not in response.text
    assert "/static/js/service_actions.js" in response.text
    assert "Live server" not in response.text


def test_dashboard_stopping_server_hides_service_actions(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopping")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)
    payload = client.get("/dashboard/status.json", follow_redirects=False).json()

    assert response.status_code == 200
    assert "stopping" in response.text
    assert "Server is stopping; actions are unavailable until shutdown finishes." in response.text
    assert "action=\"/service/start\"" not in response.text
    assert "action=\"/service/stop\"" not in response.text
    assert "action=\"/service/restart\"" not in response.text
    assert "data-service-action-form" not in response.text
    assert "Live server" not in response.text
    assert payload["lifecycle"] == "stopping"
    assert payload["running"] is False
    assert payload["fields"]["overview.lifecycle"] == "stopping"
    assert (
        payload["fields"]["quick_actions.note"]
        == "Server is stopping; actions are unavailable until shutdown finishes."
    )
    assert payload["actions"] == []


def test_dashboard_running_server_marks_live_telemetry_loading(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(
        monkeypatch,
        lifecycle="running",
        fps_available=False,
        players_available=False,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)
    payload = client.get("/dashboard/status.json", follow_redirects=False).json()

    assert response.status_code == 200
    assert "Waiting for telemetry..." in response.text
    assert 'data-dashboard-field="overview.players" data-dashboard-loading="true"' in response.text
    assert 'data-dashboard-field="overview.fps" data-dashboard-loading="true"' in response.text
    assert 'data-metric-loading="true"' in response.text
    assert payload["fields"]["overview.players"] == "Waiting for telemetry..."
    assert payload["fields"]["overview.fps"] == "Waiting for telemetry..."
    assert payload["field_states"]["overview.players"] == {"loading": True}
    assert payload["field_states"]["overview.fps"] == {"loading": True}
    assert payload["metrics"]["fps"]["loading"] is True
    assert payload["metrics"]["fps"]["text"] == "Waiting for telemetry..."


def test_dashboard_running_server_shows_stop_restart_fps_actions(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert response.text.count('class="fps-selector"') == 1
    assert response.text.count('name="max_fps"') == 2
    assert 'value="60" form="service-restart-form" checked' in response.text
    assert 'value="120" form="service-restart-form"' in response.text
    assert 'id="service-stop-form"' in response.text
    assert 'name="max_fps" value="60" form="service-stop-form"' not in response.text
    stop_button = (
        '<button class="danger" data-service-action-submit type="submit">'
        'Stop</button>'
    )
    assert stop_button in response.text
    restart_warning_button = (
        '<button class="warning" data-service-action-submit type="submit">'
        'Restart</button>'
    )
    assert restart_warning_button in response.text
    restart_danger_button = (
        '<button class="danger" data-service-action-submit type="submit">'
        'Restart</button>'
    )
    assert restart_danger_button not in response.text
    assert '<input type="hidden" name="max_fps"' not in response.text
    assert response.text.count('action="/service/restart-at-fps"') == 1
    assert "Restart at 60 FPS" not in response.text
    assert "Restart at 120 FPS" not in response.text
    assert 'action="/service/start"' not in response.text
    assert 'action="/service/stop"' in response.text
    assert 'action="/service/restart-at-fps"' in response.text
    assert 'action="/jobs/server/update-check"' in response.text
    assert "3 / 64" in response.text
    assert "59.8" in response.text


def test_dashboard_renders_ukrainian_and_dark_theme_preference(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
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


def test_dashboard_async_preferences_do_not_reload_heavy_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    calls = _install_dashboard_model_fakes(monkeypatch)
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

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="incomplete")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/jobs/server/repair"' in response.text
    assert 'action="/service/start"' not in response.text
    assert 'action="/service/stop"' not in response.text
    assert 'action="/service/restart-at-fps"' not in response.text
    assert "Repair" in response.text
    assert "Installation incomplete" in response.text


def test_dashboard_active_update_job_overrides_incomplete_repair_state(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import SERVER_UPDATE_JOB_KIND, create_job, mark_job_running

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="incomplete")
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind=SERVER_UPDATE_JOB_KIND, requested_by_username="owner")
    mark_job_running(db_path, job.id, current_step="Running")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)
    payload = client.get("/dashboard/status.json", follow_redirects=False).json()

    update_running_note = (
        "Server update is running; server actions are unavailable until it finishes."
    )

    assert response.status_code == 200
    assert "Update job running" in response.text
    assert update_running_note in response.text
    assert "Installation incomplete" not in response.text
    assert "Repair diagnostics" not in response.text
    assert 'action="/jobs/server/repair"' not in response.text
    assert 'href="/updates"' in response.text
    assert payload["lifecycle"] == "updating"
    assert payload["fields"]["overview.lifecycle"] == "updating"
    assert payload["fields"]["quick_actions.note"] == update_running_note
    assert payload["actions"] == []


def test_dashboard_no_server_empty_state_renders_controlled_html(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="not_installed")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "No server found" in response.text
    assert "Discovery did not find an installed Arma Reforger server" in response.text
    assert 'action="/jobs/server/install"' in response.text
    assert "Install" in response.text
    assert "Host" in response.text
    assert "Web Runtime" not in response.text
    assert "Mock Server" not in response.text
    assert 'action="/service/start"' not in response.text
    assert 'action="/service/stop"' not in response.text
    assert 'action="/service/restart-at-fps"' not in response.text
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

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, host_metrics_error=True)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Partial data" in response.text
    assert "host_metrics" in response.text
    assert "host boom" in response.text
    assert "Traceback" not in response.text


def test_dashboard_shows_compact_pending_work_summary(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_CONFIG, mark_restart_pending

    password = "owner pending password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
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
    assert "Start or restart game server" in response.text
    assert "Updated / created" in response.text
    assert "View all work" in response.text
    assert response.text.count('href="/jobs"') == 1
    assert "View all jobs" not in response.text
    assert "Background jobs" not in response.text
    assert "No background jobs." not in response.text
    assert "pending-work-dashboard-table" in response.text
    assert response.text.count("View all work") == 1
    assert "pending-detail-field" not in response.text
    assert "raw-secret" not in response.text


def test_dashboard_shows_only_active_background_jobs(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running, mark_job_succeeded

    password = "owner dashboard jobs password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
    db_path = tmp_path / "web" / "web.db"
    completed = create_job(db_path, kind="safe:done", requested_by_username="owner")
    mark_job_running(db_path, completed.id)
    mark_job_succeeded(db_path, completed.id, result_message="Done")
    create_job(db_path, kind="safe:queued", requested_by_username="owner")
    running = create_job(db_path, kind="safe:running", requested_by_username="owner")
    mark_job_running(db_path, running.id)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Background jobs" in response.text
    assert "safe:running" in response.text
    assert "safe:queued" in response.text
    assert "safe:done" not in response.text
    assert "succeeded" not in response.text
    assert "Done" not in response.text


def test_dashboard_hides_completed_background_jobs(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running, mark_job_succeeded

    password = "owner dashboard jobs password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
    db_path = tmp_path / "web" / "web.db"
    completed = create_job(db_path, kind="safe:done", requested_by_username="owner")
    mark_job_running(db_path, completed.id)
    mark_job_succeeded(db_path, completed.id, result_message="Done")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Background jobs" not in response.text
    assert "safe:done" not in response.text

def test_dashboard_facade_error_returns_controlled_html(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_failure(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 500
    assert "text/html" in response.headers["content-type"]
    assert "Dashboard data is unavailable." in response.text
    assert "RuntimeError" in response.text
    assert "boom" not in response.text
    assert "Traceback" not in response.text


def test_dashboard_renders_external_bind_warning(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.security.exposure import EXTERNAL_BIND_WITHOUT_HTTPS_WARNING

    password = "owner dashboard password"
    config = ensure_web_runtime(tmp_path)
    save_web_runtime_config(replace(config, bind_host="0.0.0.0", https_required=False))
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Exposure warning" not in response.text
    assert EXTERNAL_BIND_WITHOUT_HTTPS_WARNING not in response.text
    assert password not in response.text
    assert "ARMACTL_WEB_SESSION_SECRET" not in response.text


def test_dashboard_and_jobs_show_fallback_pending_work_without_leaking_secrets(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_CONFIG, mark_restart_pending_fallback

    password = "owner fallback password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch)
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
        assert "Start or restart game server" in response.text
        assert "raw-user-secret" not in response.text
        assert "raw-detail-secret" not in response.text
        assert "raw-token" not in response.text
    assert "Updated / created" in dashboard_response.text
    assert "max_players" in jobs_response.text
    assert "No pending operator work." not in dashboard_response.text
    assert "No pending operator work." not in jobs_response.text


def test_dashboard_renders_unknown_update_signal_without_breaking(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopped")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Updates" in response.text
    assert "Latest build unknown" in response.text
    assert "Check for updates" in response.text
    assert "Last checked" in response.text
    assert "Installed build" in response.text
    assert "Latest build" in response.text
    assert "Check state" in response.text
    assert 'action="/jobs/server/update-check"' in response.text
    assert 'action="/jobs/server/update"' not in response.text
    assert "Traceback" not in response.text


def test_dashboard_version_read_model_does_not_run_steamcmd(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_model

    def fail_latest_check(*args, **kwargs):
        raise AssertionError("dashboard GET must not run SteamCMD latest check")

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopped")
    monkeypatch.setattr(
        dashboard_model.server_versions.installer,
        "fetch_steam_app_info",
        fail_latest_check,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Latest build unknown" in response.text


def test_dashboard_renders_update_available_notice_without_action_when_running(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_model
    from armactl.web.services import server_versions

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="running")
    monkeypatch.setattr(
        dashboard_model.server_versions,
        "load_server_version_state",
        lambda **kwargs: server_versions.ServerVersionState(
            installed="100",
            latest="101",
            branch="public",
            check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
            status="update available",
            message="Update available",
            can_update=True,
            server_running=True,
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)
    payload = client.get("/dashboard/status.json", follow_redirects=False).json()

    assert response.status_code == 200
    assert "Updates" in response.text
    assert "Update available" in response.text
    assert "Stop the game server before updating." in response.text
    assert "100" in response.text
    assert "101" in response.text
    assert 'action="/jobs/server/update-check"' in response.text
    assert "action=\"/jobs/server/update\"" not in response.text
    assert "name=\"confirm\" value=\"running-update\" required" not in response.text
    assert payload["fields"]["server_version.status"] == "Update available"
    assert payload["fields"]["server_version.installed"] == "100"
    assert payload["fields"]["server_version.latest"] == "101"
    assert [action["name"] for action in payload["actions"]] == [
        "stop",
        "restart",
        "update-check",
    ]


def test_dashboard_renders_update_available_action_when_stopped(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_model
    from armactl.web.services import server_versions

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopped")
    monkeypatch.setattr(
        dashboard_model.server_versions,
        "load_server_version_state",
        lambda **kwargs: server_versions.ServerVersionState(
            installed="100",
            latest="101",
            branch="public",
            check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
            status="update available",
            message="Update available",
            can_update=True,
            server_running=False,
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)
    payload = client.get("/dashboard/status.json", follow_redirects=False).json()

    assert response.status_code == 200
    assert "Updates" in response.text
    assert "Update available" in response.text
    assert "Stop the game server before updating." not in response.text
    assert "Check for updates" in response.text
    assert "Last checked" in response.text
    assert 'action="/jobs/server/update-check"' in response.text
    assert "action=\"/jobs/server/update\"" in response.text
    assert "name=\"confirm\" value=\"running-update\" required" not in response.text
    assert [action["name"] for action in payload["actions"]] == [
        "start",
        "update-check",
        "update",
    ]


def test_dashboard_version_check_failure_degrades_to_controlled_signal(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_model

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopped")
    monkeypatch.setattr(
        dashboard_model.server_versions,
        "load_server_version_state",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("version boom")),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "Build check failed" in response.text
    assert "Partial data" in response.text
    assert "version boom" in response.text
    assert "Traceback" not in response.text


_DASHBOARD_JS = (
    Path(__file__).parents[1]
    / "src"
    / "armactl"
    / "web"
    / "static"
    / "js"
    / "dashboard.js"
)
_NETWORK_RUNBOOK = Path(__file__).parents[1] / "docs" / "network-hardening-runbook.md"
_WEB_DEPLOYMENT_DOC = Path(__file__).parents[1] / "docs" / "web-deployment.md"
_HARDENING_CHECKLIST = (
    Path(__file__).parents[1] / "docs" / "hardening-audit-cleanup-checklist.md"
)


def _dashboard_js() -> str:
    return _DASHBOARD_JS.read_text(encoding="utf-8")


def test_dashboard_refresh_js_uses_bounded_state_machine() -> None:
    content = _dashboard_js()

    assert "lastSuccessAt" in content
    assert "failureCount" in content
    assert "staleAfterMs" in content
    assert "repeatedFailureThreshold = 3" in content
    assert "Math.min(Math.max(intervalMs * 4, 30000), 60000)" in content
    assert "refreshState.failureCount += 1" in content
    assert "applyFailureStatus();" in content
    assert "refreshState.lastSuccessAt = Date.now();" in content
    assert "refreshState.failureCount = 0;" in content


def test_dashboard_refresh_js_pauses_for_background_or_offline() -> None:
    content = _dashboard_js()

    assert "document.hidden" in content
    assert "navigator.onLine === false" in content
    assert 'setLiveStatus("paused")' in content
    assert 'document.addEventListener("visibilitychange"' in content
    assert 'window.addEventListener("focus", triggerImmediateRefresh)' in content
    assert 'window.addEventListener("online"' in content
    assert 'window.addEventListener("offline"' in content


def test_dashboard_single_failed_poll_does_not_immediately_show_stale() -> None:
    content = _dashboard_js()

    assert re.search(r"catch \(_error\) \{\s+refreshState\.failureCount \+= 1;", content)
    assert 'catch (_error) {\n      setLiveStatus("stale");' not in content
    assert 'catch (_error) {\n      setLiveStatus(true);' not in content
    assert 'setLiveStatus(staleByAge || staleByFailures ? "stale" : "fresh");' in content


def test_network_hardening_login_limit_returns_429_and_avoids_strict_get_login() -> None:
    content = _NETWORK_RUNBOOK.read_text(encoding="utf-8")

    assert "limit_req_status 429;" in content
    assert re.search(r"^\s*POST \$binary_remote_addr;", content, flags=re.MULTILINE)
    assert "GET `/login` must not be hard-limited" in content
    assert "Prefer putting the stricter limiter on\n`POST /login`" in content
    assert "not an armactl web app outage" in content


def test_network_hardening_keeps_dashboard_status_bounded_but_browser_safe() -> None:
    content = _NETWORK_RUNBOOK.read_text(encoding="utf-8")

    assert "zone=armactl_dashboard_status:10m rate=120r/m" in content
    assert "limit_req zone=armactl_dashboard_status burst=60 nodelay;" in content
    assert "normal 7-second\npolling across a small set of concurrently open tabs" in content


def test_web_deployment_troubleshooting_explains_gateway_throttle_not_outage() -> None:
    content = _WEB_DEPLOYMENT_DOC.read_text(encoding="utf-8")

    assert "should return HTTP 429 rather than nginx's default-looking 503" in content
    assert "gateway throttling, not proof that `armactl-web` crashed" in content
    assert "Verify local `/healthz` before restarting services" in content


def test_hardening_checklist_records_slice6_scope_note() -> None:
    content = _HARDENING_CHECKLIST.read_text(encoding="utf-8")

    assert "Slice 6 UX/Ops Hardening Note" in content
    assert "Production nginx config, deploy, SSH, restart" in content
    assert "polling frequency, WebSocket/SSE, and player/session truth stay out of scope" in content
