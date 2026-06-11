# Tests for the read-only web dashboard facade.

from __future__ import annotations

import builtins
import importlib
import json
import sys
from collections.abc import Callable
from typing import Any

from armactl.metrics import HostMetrics, ServerFpsMetrics
from armactl.player_view import PlayerView
from armactl.state import PortInfo, ServerState
from armactl.status_summary import ConfigSummary, ModsSummary


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
            "main_pid": 123 if service_active else 0,
        },
    )
    monkeypatch.setattr(
        facade.service_manager,
        "get_timer_status",
        lambda timer_name: {"timer_name": timer_name, "active": True, "enabled": True},
    )
    monkeypatch.setattr(
        facade.status_summary,
        "load_status_summaries",
        lambda config_path: (
            ConfigSummary(
                True,
                server_name="Test Server",
                max_players=64,
                bind_port=2500,
                a2s_port=2501,
                rcon_port=2502,
            ),
            ModsSummary(True, count=2),
        ),
    )
    monkeypatch.setattr(
        facade.metrics,
        "query_host_metrics",
        lambda: HostMetrics(True, cpu_percent=12.5, memory_total_bytes=1024),
    )
    monkeypatch.setattr(
        facade.metrics,
        "query_server_fps_metrics",
        lambda config_dir: ServerFpsMetrics(True, fps=58.5, source=str(config_dir)),
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
    return facade


def _fail_if_called(name: str) -> Callable[..., Any]:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"{name} should not be called")

    return fail


def test_facade_import_does_not_import_tui_textual_or_click(monkeypatch):
    forbidden = ("armactl.web.facade", "armactl.tui", "textual", "click")
    _forget_modules(*forbidden)
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
    assert snapshot["service"]["available"] is True
    assert snapshot["service"]["active"] is False
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
    assert snapshot["players"]["available"] is True
    assert snapshot["players"]["current"] == 3
    assert snapshot["fps_metrics"]["fps"] == 58.5
    assert snapshot["host_metrics"]["cpu_percent"] == 12.5
    assert snapshot["config"]["server_name"] == "Test Server"
    assert snapshot["mods"]["count"] == 2


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
    monkeypatch.setattr(facade.player_view, "query_player_view", _fail_if_called("players"))
    monkeypatch.setattr(facade.ports, "check_server_ports", _fail_if_called("ports"))

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["lifecycle"] == "not_installed"
    assert snapshot["installed"] is False
    assert snapshot["service"]["available"] is False
    assert snapshot["timer"]["available"] is False
    assert snapshot["ports"]["available"] is False
    assert snapshot["players"]["available"] is False
    assert snapshot["errors"] == []


def test_dashboard_snapshot_with_missing_config_does_not_crash(monkeypatch):
    server_state = _state(installed=True, running=False, config_exists=False, config_path="")
    facade = _install_common_fakes(monkeypatch, server_state)
    monkeypatch.setattr(
        facade.status_summary, "load_status_summaries", _fail_if_called("summaries")
    )
    monkeypatch.setattr(facade.metrics, "query_server_fps_metrics", _fail_if_called("fps"))

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["lifecycle"] == "incomplete"
    assert snapshot["config"]["available"] is False
    assert snapshot["mods"]["available"] is False
    assert snapshot["fps_metrics"]["available"] is False
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
        facade.player_view,
        "query_player_view",
        lambda instance, **kwargs: (_ for _ in ()).throw(RuntimeError("players boom")),
    )

    snapshot = facade.load_dashboard_snapshot("default")

    json.dumps(snapshot)
    assert snapshot["service"]["available"] is False
    assert snapshot["host_metrics"]["available"] is False
    assert snapshot["fps_metrics"]["available"] is False
    assert snapshot["players"]["available"] is False
    assert {error["section"] for error in snapshot["errors"]} == {
        "service",
        "host_metrics",
        "fps_metrics",
        "players",
    }
