"""Tests for the minimal FastAPI web app foundation."""

from __future__ import annotations

import builtins
import importlib
import sys

from fastapi.testclient import TestClient


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
        "service": {"available": True, "active": True, "enabled": True},
        "timer": {"available": True, "enabled": True},
        "players": {
            "available": True,
            "current": 3,
            "max_players": 64,
            "count_source": "rcon",
        },
        "fps_metrics": {"available": True, "fps": 59.8},
        "host_metrics": {
            "available": True,
            "cpu_percent": 12.0,
            "memory_total_bytes": 1024,
        },
        "config": {
            "available": True,
            "server_name": "Mock Server",
            "scenario_id": "Scenario.conf",
            "max_players": 64,
        },
        "mods": {"available": True, "count": 2},
        "ports": {
            "available": True,
            "ports": {
                "game": {"port": 2001, "listening": True},
                "a2s": {"port": 17777, "listening": True},
                "rcon": {"port": 19999, "listening": False},
            },
        },
        "errors": [],
    }


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

    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_dashboard_routes_render_html(monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    calls: list[str] = []

    def fake_load_dashboard_snapshot(instance: str) -> dict:
        calls.append(instance)
        return _snapshot()

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fake_load_dashboard_snapshot)
    client = TestClient(create_app())

    root_response = client.get("/")
    dashboard_response = client.get("/dashboard")

    assert root_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert "text/html" in root_response.headers["content-type"]
    assert "Mock Server" in root_response.text
    assert "running" in root_response.text
    assert "3 / 64" in root_response.text
    assert calls == ["default", "default"]


def test_dashboard_facade_error_returns_controlled_html(monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import dashboard

    def fail_dashboard(instance: str) -> dict:
        raise RuntimeError("boom with traceback-looking details")

    monkeypatch.setattr(dashboard, "load_dashboard_snapshot", fail_dashboard)
    client = TestClient(create_app())

    response = client.get("/dashboard")

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
    assert (STATIC_DIR / "css" / "app.css").is_file()

    client = TestClient(create_app())
    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ".summary-band" in response.text
