# Tests for the read-only web dashboard facade.

from __future__ import annotations

import builtins
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from armactl.metrics import (
    HostMetrics,
    ProcessMetrics,
    ServerFpsMetrics,
    ServerOperationalStatus,
)
from armactl.player_view import PlayerView
from armactl.state import PortInfo, ServerState
from armactl.status_summary import ConfigSummary, ModsSummary, ModSummaryEntry


def _import_facade():
    return importlib.import_module("armactl.web.facade")


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if any(
            module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes
        ):
            sys.modules.pop(module_name)


def _state(
    *,
    installed: bool,
    running: bool = False,
    config_exists: bool = True,
    config_path: str = "/srv/armactl-data/default/config/config.json",
) -> ServerState:
    return ServerState(
        server_installed=installed,
        binary_exists=installed,
        config_exists=config_exists,
        service_exists=installed,
        timer_exists=installed,
        server_running=running,
        instance_root="/srv/armactl-data/default" if installed else "",
        install_dir="/srv/armactl-data/default/server" if installed else "",
        config_path=config_path,
        service_name="armareforger.service",
        timer_name="armareforger-restart.timer",
        ports=PortInfo(
            game=2400 if installed else None,
            a2s=17778 if installed else None,
            rcon=20000 if installed else None,
        ),
    )


def _install_bot_fakes(monkeypatch, facade, *, enabled: bool = False) -> None:
    monkeypatch.setattr(
        facade.bot_config,
        "load_bot_config",
        lambda instance: SimpleNamespace(
            enabled=enabled,
            token="configured-token" if enabled else "",
            admin_chat_ids=["123"] if enabled else [],
            language="uk",
            env_path=Path("/srv/armactl-data/default/bot/.env"),
        ),
    )
    monkeypatch.setattr(
        facade.paths,
        "bot_service_file",
        lambda: Path("/nonexistent/armactl-bot.service"),
    )


def _install_common_fakes(
    monkeypatch,
    server_state: ServerState,
    *,
    service_active: bool = False,
    players: PlayerView | None = None,
) -> Any:
    facade = _import_facade()

    monkeypatch.setattr(facade.discovery, "discover", lambda instance, save=False: server_state)
    monkeypatch.setattr(
        facade.service_manager,
        "get_service_status",
        lambda service_name: {
            "service_name": service_name,
            "active": service_active,
            "enabled": True,
            "active_state": "active" if service_active else "inactive",
            "sub_state": "running" if service_active else "dead",
            "main_pid": 123 if service_active else 0,
        },
    )
    monkeypatch.setattr(
        facade.service_manager,
        "get_timer_status",
        lambda timer_name: {
            "timer_name": timer_name,
            "active": True,
            "enabled": True,
            "schedule": "*-*-* 06:00:00",
            "next_run": "Fri 2026-06-12 06:00:00 UTC",
        },
    )
    monkeypatch.setattr(
        facade.status_summary,
        "load_status_summaries",
        lambda config_path: (
            ConfigSummary(
                True,
                server_name="Test Server",
                scenario_id="Scenario.conf",
                max_players=64,
                bind_port=2500,
                a2s_port=2501,
                rcon_port=2502,
                visible=True,
                battleye=True,
            ),
            ModsSummary(
                True,
                count=2,
                preview=[ModSummaryEntry("mod-a", "Mod A")],
                remaining_count=1,
            ),
        ),
    )
    monkeypatch.setattr(
        facade.metrics,
        "query_host_metrics",
        lambda: HostMetrics(
            True,
            cpu_percent=12.5,
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
        facade.metrics,
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
        facade.metrics,
        "query_server_fps_metrics",
        lambda config_dir: ServerFpsMetrics(
            True,
            fps=58.5,
            frame_avg_ms=17.1,
            frame_max_ms=25.0,
            engine_memory_kb=512,
            age_seconds=15,
            source=str(config_dir),
        ),
    )
    monkeypatch.setattr(
        facade.metrics,
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
        facade.player_view,
        "query_player_view",
        lambda instance, **kwargs: players or PlayerView(True, current=3, max_players=64),
    )
    monkeypatch.setattr(
        facade.ports,
        "check_server_ports",
        lambda game_port, a2s_port, rcon_port: {
            "game": {"port": game_port, "listening": service_active},
            "a2s": {"port": a2s_port, "listening": service_active},
            "rcon": {"port": rcon_port, "listening": False},
        },
    )
    monkeypatch.setattr(
        facade.sat_admin_guard,
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
    _install_bot_fakes(monkeypatch, facade)
    return facade


def _fail_if_called(name: str) -> Callable[..., Any]:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"{name} should not be called")

    return fail


def test_facade_import_does_not_import_tui_textual_or_click(monkeypatch):
    forbidden = ("armactl.web.facade", "armactl.tui", "textual", "click")
    _forget_modules("armactl.web.facade", "armactl.tui", "textual")
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden[1:]):
            blocked_imports.append(name)
            raise AssertionError(f"facade imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.facade")

    assert module.__name__ == "armactl.web.facade"
    assert blocked_imports == []
    assert "armactl.tui" not in sys.modules
    assert "textual" not in sys.modules


def test_dashboard_snapshot_for_stopped_server(monkeypatch):
    facade = _install_common_fakes(monkeypatch, _state(installed=True, running=False))

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["lifecycle"] == "stopped"
    assert snapshot["installed"] is True
    assert snapshot["running"] is False
    assert snapshot["overview"]["empty_state"] is False
    assert snapshot["paths"]["install_dir"] == "/srv/armactl-data/default/server"
    assert snapshot["service"]["available"] is True
    assert snapshot["service"]["active"] is False
    assert snapshot["service_runtime"]["available"] is False
    assert snapshot["players"]["available"] is False
    assert snapshot["ports"]["available"] is True
    assert snapshot["ports"]["ports"]["game"]["port"] == 2400
    assert snapshot["errors"] == []


def test_dashboard_snapshot_for_running_server(monkeypatch):
    facade = _install_common_fakes(
        monkeypatch, _state(installed=True, running=True), service_active=True
    )

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["lifecycle"] == "running"
    assert snapshot["service"]["active"] is True
    assert snapshot["service_runtime"]["cpu_text"] == "2.5%"
    assert snapshot["operational_status"]["message"] == "Ready"
    assert snapshot["players"]["available"] is True
    assert snapshot["players"]["count_text"] == "3 / 64"
    assert snapshot["fps_metrics"]["fps_text"] == "58.5"
    assert snapshot["host_metrics"]["cpu_text"] == "12.5%"
    assert snapshot["host_metrics"]["memory_text"] == "512 B / 1.0 KiB"
    assert snapshot["config"]["server_name"] == "Test Server"
    assert snapshot["config"]["visible_text"] == "yes"
    assert snapshot["mods"]["count"] == 2
    assert snapshot["mods"]["preview_labels"] == ["Mod A (mod-a)"]
    assert snapshot["bot"]["token_configured"] is False
    assert snapshot["sat"]["available"] is True
    assert snapshot["sat"]["warning"] == ""


def test_dashboard_snapshot_uses_fast_player_probe(monkeypatch):
    state = _state(installed=True, running=True)
    facade = _install_common_fakes(monkeypatch, state, service_active=True)
    calls: list[dict[str, Any]] = []

    def fake_query_player_view(instance: str, **kwargs: Any) -> PlayerView:
        calls.append({"instance": instance, **kwargs})
        return PlayerView(True, current=2, max_players=24)

    monkeypatch.setattr(facade.player_view, "query_player_view", fake_query_player_view)

    snapshot = facade.load_dashboard_snapshot("default")

    assert snapshot["players"]["count_text"] == "2 / 24"
    assert len(calls) == 1
    assert calls[0]["instance"] == "default"
    assert calls[0]["state"] is state
    assert calls[0]["timeout"] == facade.DASHBOARD_PLAYER_TIMEOUT_SECONDS
    assert calls[0]["roster_timeout"] == facade.DASHBOARD_ROSTER_TIMEOUT_SECONDS
    assert calls[0]["include_roster"] is False


def test_dashboard_snapshot_includes_safe_web_runtime(monkeypatch, tmp_path: Path):
    facade = _install_common_fakes(monkeypatch, _state(installed=True, running=True))
    web_config = SimpleNamespace(
        data_root=tmp_path,
        runtime_dir=tmp_path / "web",
        env_path=tmp_path / "web" / "web.env",
        db_path=tmp_path / "web" / "web.db",
        audit_log_path=tmp_path / "web" / "audit.log",
        bind_host="127.0.0.1",
        bind_port=8765,
        https_required=False,
        session_secret="do-not-render-this-secret",
    )

    snapshot = facade.load_dashboard_snapshot("default", web_config=web_config)
    payload = json.dumps(snapshot)

    assert snapshot["web"]["available"] is True
    assert snapshot["web"]["bind_port"] == 8765
    assert snapshot["web"]["exposure_warning"] is None
    assert "do-not-render-this-secret" not in payload
    assert "session_secret" not in payload


def test_dashboard_snapshot_includes_external_bind_warning(monkeypatch, tmp_path: Path):
    facade = _install_common_fakes(monkeypatch, _state(installed=True, running=True))
    web_config = SimpleNamespace(
        data_root=tmp_path,
        runtime_dir=tmp_path / "web",
        env_path=tmp_path / "web" / "web.env",
        db_path=tmp_path / "web" / "web.db",
        audit_log_path=tmp_path / "web" / "audit.log",
        bind_host="0.0.0.0",
        bind_port=8765,
        https_required=False,
        session_secret="do-not-render-this-secret",
    )

    snapshot = facade.load_dashboard_snapshot("default", web_config=web_config)
    payload = json.dumps(snapshot)

    assert snapshot["web"]["exposure_warning"]["severity"] == "danger"
    assert "External bind without HTTPS-required cookies" in payload
    assert "do-not-render-this-secret" not in payload
    assert "session_secret" not in payload


def test_dashboard_snapshot_for_no_server_skips_server_sections(monkeypatch):
    server_state = _state(installed=False, config_exists=False, config_path="")
    facade = _import_facade()
    monkeypatch.setattr(facade.discovery, "discover", lambda instance, save=False: server_state)
    monkeypatch.setattr(facade.service_manager, "get_service_status", _fail_if_called("service"))
    monkeypatch.setattr(facade.service_manager, "get_timer_status", _fail_if_called("timer"))
    monkeypatch.setattr(
        facade.status_summary, "load_status_summaries", _fail_if_called("summaries")
    )
    monkeypatch.setattr(facade.metrics, "query_host_metrics", lambda: HostMetrics(True))
    monkeypatch.setattr(facade.metrics, "query_server_fps_metrics", _fail_if_called("fps"))
    monkeypatch.setattr(
        facade.metrics,
        "query_server_operational_status",
        _fail_if_called("operational"),
    )
    monkeypatch.setattr(
        facade.metrics,
        "query_service_runtime_metrics",
        _fail_if_called("service_runtime"),
    )
    monkeypatch.setattr(facade.player_view, "query_player_view", _fail_if_called("players"))
    monkeypatch.setattr(facade.ports, "check_server_ports", _fail_if_called("ports"))
    monkeypatch.setattr(
        facade.sat_admin_guard,
        "inspect_sat_admin_config",
        _fail_if_called("sat"),
    )
    _install_bot_fakes(monkeypatch, facade)

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["lifecycle"] == "not_installed"
    assert snapshot["overview"]["empty_state"] is True
    assert snapshot["overview"]["empty_title"] == "No server found"
    assert snapshot["installed"] is False
    assert snapshot["service"]["available"] is False
    assert snapshot["timer"]["available"] is False
    assert snapshot["ports"]["available"] is False
    assert snapshot["players"]["available"] is False
    assert snapshot["sat"]["available"] is False
    assert snapshot["errors"] == []


def test_dashboard_snapshot_with_missing_config_does_not_crash(monkeypatch):
    server_state = _state(installed=True, running=False, config_exists=False, config_path="")
    facade = _install_common_fakes(monkeypatch, server_state)
    monkeypatch.setattr(
        facade.status_summary, "load_status_summaries", _fail_if_called("summaries")
    )
    monkeypatch.setattr(facade.metrics, "query_server_fps_metrics", _fail_if_called("fps"))
    monkeypatch.setattr(
        facade.metrics,
        "query_server_operational_status",
        _fail_if_called("operational"),
    )

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["lifecycle"] == "incomplete"
    assert snapshot["overview"]["empty_title"] == "Installation incomplete"
    assert snapshot["config"]["available"] is False
    assert snapshot["mods"]["available"] is False
    assert snapshot["fps_metrics"]["available"] is False
    assert snapshot["operational_status"]["available"] is False
    assert snapshot["errors"] == []


def test_dashboard_snapshot_degrades_when_sections_raise(monkeypatch):
    server_state = _state(installed=True, running=True)
    facade = _install_common_fakes(monkeypatch, server_state, service_active=True)
    monkeypatch.setattr(
        facade.service_manager,
        "get_service_status",
        lambda service_name: (_ for _ in ()).throw(RuntimeError("service boom")),
    )
    monkeypatch.setattr(
        facade.metrics,
        "query_host_metrics",
        lambda: (_ for _ in ()).throw(RuntimeError("host boom")),
    )
    monkeypatch.setattr(
        facade.metrics,
        "query_server_fps_metrics",
        lambda config_dir: (_ for _ in ()).throw(RuntimeError("fps boom")),
    )
    monkeypatch.setattr(
        facade.metrics,
        "query_server_operational_status",
        lambda config_dir: (_ for _ in ()).throw(RuntimeError("ops boom")),
    )
    monkeypatch.setattr(
        facade.player_view,
        "query_player_view",
        lambda instance, **kwargs: (_ for _ in ()).throw(RuntimeError("players boom")),
    )

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["service"]["available"] is False
    assert snapshot["host_metrics"]["available"] is False
    assert snapshot["fps_metrics"]["available"] is False
    assert snapshot["operational_status"]["available"] is False
    assert snapshot["players"]["available"] is False
    assert {error["section"] for error in snapshot["errors"]} == {
        "service",
        "host_metrics",
        "fps_metrics",
        "operational_status",
        "players",
    }



def test_management_page_snapshots_are_safe_read_only(monkeypatch):
    facade = _import_facade()
    server_state = _state(installed=True, running=False)
    config = {
        "bindPort": 2400,
        "game": {
            "name": "Read Only Server",
            "scenarioId": "Scenario.conf",
            "maxPlayers": 42,
            "visible": True,
            "passwordAdmin": "admin-password-secret",
            "admins": ["ABC123"],
        },
        "a2s": {"port": 17778},
        "rcon": {"port": 20000, "password": "rcon-password-secret"},
    }

    monkeypatch.setattr(facade.discovery, "discover", lambda instance, save=False: server_state)
    monkeypatch.setattr(facade.config_manager, "load_config", lambda config_path: config)
    monkeypatch.setattr(
        facade.mods_manager,
        "get_mods",
        lambda config_path: [
            {"modId": "mod-a", "name": "Mod A", "version": "1.0"},
            {"modId": "mod-b", "name": "Mod B"},
        ],
    )
    monkeypatch.setattr(
        facade.admins_manager,
        "admins_state_path_for_config",
        lambda config_path: Path("/srv/armactl-data/default/config/admins-state.json"),
    )
    monkeypatch.setattr(
        facade.admins_manager,
        "load_admins",
        lambda config_path: [
            {"identityId": "ABC123", "name": "Local Captain", "source": "local"}
        ],
    )
    monkeypatch.setattr(facade.admins_manager, "get_admins", _fail_if_called("get_admins"))
    _install_bot_fakes(monkeypatch, facade, enabled=True)

    config_page = facade.load_config_page("default")
    mods_page = facade.load_mods_page("default")
    admins_page = facade.load_admins_page("default")
    bot_page = facade.load_bot_page("default")
    payload = json.dumps([config_page, mods_page, admins_page, bot_page])

    assert config_page["config"]["server_name"] == "Read Only Server"
    assert config_page["config"]["rcon_port"] == 20000
    assert mods_page["count"] == 2
    assert mods_page["mods"][0] == {
        "mod_id": "mod-a",
        "name": "Mod A",
        "version": "1.0",
    }
    assert admins_page["official_admins"] == [
        {"identity_id": "ABC123", "name": "Local Captain", "source": "local"}
    ]
    assert bot_page["bot"]["token_configured"] is True
    assert "admin-password-secret" not in payload
    assert "rcon-password-secret" not in payload
    assert "configured-token" not in payload
    assert "password_hash" not in payload


def test_management_pages_handle_missing_config_without_loading_backends(monkeypatch):
    facade = _import_facade()
    server_state = _state(installed=True, running=False, config_exists=False, config_path="")
    monkeypatch.setattr(facade.discovery, "discover", lambda instance, save=False: server_state)
    monkeypatch.setattr(facade.config_manager, "load_config", _fail_if_called("load_config"))
    monkeypatch.setattr(facade.mods_manager, "get_mods", _fail_if_called("get_mods"))
    monkeypatch.setattr(facade.admins_manager, "load_admins", _fail_if_called("load_admins"))

    config_page = facade.load_config_page("default")
    mods_page = facade.load_mods_page("default")
    admins_page = facade.load_admins_page("default")

    assert config_page["available"] is False
    assert mods_page["available"] is False
    assert admins_page["available"] is False
    assert config_page["error"] == "config path is not available"
    assert mods_page["error"] == "config path is not available"
    assert admins_page["error"] == "config path is not available"


def _view_snapshot(lifecycle: str) -> dict[str, Any]:
    return {
        "instance": "default",
        "lifecycle": lifecycle,
        "overview": {
            "label": "no server" if lifecycle == "not_installed" else lifecycle,
            "empty_state": lifecycle in {"not_installed", "incomplete"},
            "empty_title": (
                "No server found"
                if lifecycle == "not_installed"
                else "Installation incomplete"
            ),
            "empty_message": "Discovery did not find an installed server.",
        },
        "service": {"active_state": "active" if lifecycle == "running" else "inactive"},
        "operational_status": {"message": "Ready", "age_text": "12s"},
        "players": {"count_text": "3 / 64"},
        "fps_metrics": {"fps_text": "60.0", "age_text": "10s"},
        "host_metrics": {
            "cpu_text": "12%",
            "memory_text": "512 MiB / 1 GiB",
            "disk_text": "2 GiB / 4 GiB",
            "uptime_text": "1h",
        },
        "web": {
            "bind_host": "127.0.0.1",
            "bind_port": 8765,
            "https_required_text": "no",
            "runtime_dir": "/tmp/web",
            "db_path": "/tmp/web/web.db",
        },
        "config": {
            "server_name": "Lifecycle Test Server",
            "scenario_id": "Scenario.conf",
            "max_players": 64,
        },
        "mods": {"count": 2, "preview_labels": ["Core Mod"]},
        "sat": {"available": True, "warning": ""},
        "paths": {
            "instance_root": "/srv/default",
            "install_dir": "/srv/default/server",
            "config_path": "/srv/default/config/config.json",
        },
        "errors": [],
    }


def test_dashboard_view_model_actions_follow_lifecycle():
    from armactl.web.views.dashboard import build_dashboard_view

    common_permissions = {
        "can_run_actions": True,
        "can_view_config": True,
        "can_view_mods": True,
        "can_view_admins": True,
        "can_view_bot": True,
        "can_view_jobs": True,
        "can_view_files": True,
        "can_view_logs": True,
    }

    not_installed = build_dashboard_view(_view_snapshot("not_installed"), **common_permissions)
    incomplete = build_dashboard_view(_view_snapshot("incomplete"), **common_permissions)
    stopped = build_dashboard_view(_view_snapshot("stopped"), **common_permissions)
    running = build_dashboard_view(_view_snapshot("running"), **common_permissions)

    assert not_installed["heading"] == "Dashboard"
    assert [action["name"] for action in not_installed["actions"]] == ["install"]
    assert not_installed["actions"][0]["action_path"] == "/jobs/server/install"
    assert not_installed["server_cards"] == []
    assert not_installed["management_links"] == []
    assert not_installed["quick_action_note"] == ""
    assert [action["name"] for action in incomplete["actions"]] == ["repair"]
    assert incomplete["actions"][0]["action_path"] == "/jobs/server/repair"
    assert incomplete["quick_action_note"] == ""
    assert [action["name"] for action in stopped["actions"]] == ["start"]
    assert [action["name"] for action in running["actions"]] == ["stop", "restart"]
    assert running["heading"] == "Lifecycle Test Server"
