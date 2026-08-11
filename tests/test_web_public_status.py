from __future__ import annotations

from pathlib import Path

from test_web_dashboard import _install_dashboard_model_fakes
from web_route_helpers import _client


def test_public_server_status_json_is_public_and_safe(
    tmp_path: Path, monkeypatch
):
    from armactl.web.app import create_app

    calls = _install_dashboard_model_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/public/server-status.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["ok"] is True
    assert payload["installed"] is True
    assert payload["running"] is True
    assert payload["server"] == {
        "name": "Mock Server",
        "scenario": "Scenario.conf",
    }
    assert payload["players"]["current"] == 3
    assert payload["players"]["max"] == 64
    assert payload["performance"] == {
        "fps_available": True,
        "fps": 59.8,
        "fps_text": "59.8",
        "telemetry_age": "10s",
    }
    assert payload["status"]["state"] == "ready"
    body = response.text.lower()
    assert "/srv/" not in body
    assert "csrf" not in body
    assert "session" not in body
    assert calls == ["default"]


def test_public_server_status_json_reports_stopping_lifecycle(
    tmp_path: Path, monkeypatch
):
    from armactl.web.app import create_app

    _install_dashboard_model_fakes(monkeypatch, lifecycle="stopping")
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/public/server-status.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["running"] is False
    assert payload["lifecycle"] == "stopping"
    assert payload["players"]["available"] is False
    assert payload["players"]["text"] == "unavailable"
    assert payload["performance"]["fps"] is None


def test_public_server_status_json_fails_closed(
    tmp_path: Path, monkeypatch
):
    from armactl.web.app import create_app
    from armactl.web.page_models import dashboard as dashboard_model

    def fail_load(*args, **kwargs):
        raise RuntimeError("secret token leaked if exposed")

    monkeypatch.setattr(dashboard_model, "load_dashboard_snapshot", fail_load)
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/public/server-status.json")

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "error": "Server status is unavailable.",
    }
    assert "secret" not in response.text.lower()
