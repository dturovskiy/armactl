"""Tests for safe web editing of basic server config fields."""

from __future__ import annotations

import builtins
import importlib
import json
import re
import sys
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from starlette.exceptions import StarletteDeprecationWarning

from armactl.state import PortInfo, ServerState
from armactl.web.auth.cookies import SESSION_COOKIE_NAME
from armactl.web.auth.setup import setup_owner_user


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if _matches_prefix(module_name, prefixes):
            sys.modules.pop(module_name)


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


def _sample_config() -> dict[str, Any]:
    return {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "publicAddress": "",
        "publicPort": 2001,
        "a2s": {"address": "0.0.0.0", "port": 17777},
        "rcon": {
            "address": "0.0.0.0",
            "port": 19999,
            "password": "raw-rcon-secret",
            "permission": "admin",
        },
        "game": {
            "name": "Old Server",
            "password": "",
            "passwordAdmin": "admin-password-secret",
            "admins": ["ABC123"],
            "scenarioId": "OldScenario.conf",
            "maxPlayers": 32,
            "visible": False,
            "gameProperties": {
                "serverMaxViewDistance": 1600,
                "serverMinGrassDistance": 30,
                "networkViewDistance": 1200,
                "battlEye": False,
            },
            "mods": [{"modId": "1234567890ABCDEF", "name": "Keep Mod"}],
        },
    }


def _write_config(tmp_path: Path, config: dict[str, Any] | None = None) -> Path:
    config_path = tmp_path / "instance" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config or _sample_config(), indent=2))
    return config_path


def _state_for(config_path: Path) -> ServerState:
    return ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        service_exists=True,
        timer_exists=True,
        server_running=False,
        instance_root=str(config_path.parents[1]),
        install_dir=str(config_path.parents[1] / "server"),
        config_path=str(config_path),
        service_name="armareforger.service",
        timer_name="armareforger-restart.timer",
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )


def _patch_discovery(monkeypatch, config_path: Path) -> None:
    from armactl.web import facade
    from armactl.web.services import config_edit

    state = _state_for(config_path)
    monkeypatch.setattr(facade.discovery, "discover", lambda instance, save=False: state)
    monkeypatch.setattr(config_edit.discovery, "discover", lambda instance, save=False: state)


def _authed_client(tmp_path: Path, monkeypatch, config_path: Path):
    from armactl.web.app import create_app

    _patch_discovery(monkeypatch, config_path)
    password = "owner config edit password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    return client


def _valid_post_data(csrf_token: str) -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "name": "Updated Server",
        "scenario_id": "UpdatedScenario.conf",
        "max_players": "48",
        "visible": "true",
        "battleye": "true",
        "server_max_view_distance": "2500",
        "server_min_grass_distance": "60",
    }


def _audit_log_text(data_root: Path) -> str:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return audit_path.read_text(encoding="utf-8")


def _audit_events(data_root: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _audit_log_text(data_root).splitlines()]


def _unchanged_post_data(csrf_token: str) -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "name": "Old Server",
        "scenario_id": "OldScenario.conf",
        "max_players": "32",
        "server_max_view_distance": "1600",
        "server_min_grass_distance": "30",
    }


def test_get_config_page_shows_edit_form_for_owner(tmp_path: Path, monkeypatch):
    config_path = _write_config(tmp_path)
    client = _authed_client(tmp_path, monkeypatch, config_path)

    response = client.get("/config", follow_redirects=False)

    assert response.status_code == 200
    assert 'class="config-summary-grid"' in response.text
    assert "key-value-list config-summary-list" in response.text
    assert "config.js" in response.text
    assert "data-config-edit-form" in response.text
    assert "data-config-dirty-note hidden" in response.text
    assert "status-pill status-pill-stopped" in response.text
    assert "notice-inline notice-restart" in response.text
    for heading in ("Status", "Server", "Network", "Paths", "Basic server settings"):
        assert f">{heading}<" in response.text
    assert 'method="post" action="/config"' in response.text
    assert 'name="name"' in response.text
    assert 'value="Old Server"' in response.text
    assert 'name="scenario_id"' in response.text
    assert 'value="OldScenario.conf"' in response.text
    assert 'name="server_max_view_distance"' in response.text
    assert 'name="server_min_grass_distance"' in response.text
    assert "Restart the server to apply saved config changes." in response.text
    assert response.text.count('class="field-wide"') >= 2

    form_match = re.search(
        r'<form method="post" action="/config" class="config-edit-form"[^>]*>(.*?)</form>',
        response.text,
        re.S,
    )
    assert form_match is not None
    editable_names = set(re.findall(r'name="([^"]+)"', form_match.group(1)))
    assert editable_names == {
        "csrf_token",
        "name",
        "scenario_id",
        "max_players",
        "server_max_view_distance",
        "server_min_grass_distance",
        "visible",
        "battleye",
    }
    assert "bind_port" not in editable_names
    assert "rcon_port" not in editable_names
    assert "password" not in editable_names


def test_config_page_groups_summary_and_escapes_long_values(tmp_path: Path, monkeypatch):
    config = _sample_config()
    config["game"]["name"] = "Very long <script>alert(1)</script> server name"
    config["game"]["scenarioId"] = "Scenarios/VeryLongScenario<&>.conf"
    config_path = _write_config(tmp_path, config)
    client = _authed_client(tmp_path, monkeypatch, config_path)

    response = client.get("/config", follow_redirects=False)

    assert response.status_code == 200
    assert "config-summary-card" in response.text
    assert "key-value-list config-summary-list" in response.text
    assert "config-path-list" in response.text
    assert str(config_path) in response.text
    assert "Scenarios/VeryLongScenario&lt;&amp;&gt;.conf" in response.text
    assert "Very long &lt;script&gt;alert(1)&lt;/script&gt; server name" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert 'class="wrap-value"' in response.text


def test_config_edit_unauthenticated_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    get_response = client.get("/config", follow_redirects=False)
    post_response = client.post("/config", data={}, follow_redirects=False)

    assert get_response.status_code == 303
    assert get_response.headers["location"] == "/login"
    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/login"


def test_config_edit_without_settings_permission_gets_403(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.auth.permissions import CONFIG_VIEW, SETTINGS_MANAGE
    from armactl.web.services import config_edit

    config_path = _write_config(tmp_path)
    client = _authed_client(tmp_path, monkeypatch, config_path)
    monkeypatch.setattr(config_edit, "save_default_config", pytest.fail)

    def fake_require_permission(current, permission: str) -> bool:
        return permission == CONFIG_VIEW

    originals: dict[int, tuple[dict[str, Any], Any]] = {}
    for route in client.app.routes:
        if getattr(route, "path", "") == "/config":
            globals_dict = route.endpoint.__globals__
            key = id(globals_dict)
            if key not in originals:
                originals[key] = (
                    globals_dict,
                    globals_dict["require_permission"],
                )
            globals_dict["require_permission"] = fake_require_permission

    try:
        get_response = client.get("/config", follow_redirects=False)
        post_response = client.post("/config", data={}, follow_redirects=False)

        assert SETTINGS_MANAGE != CONFIG_VIEW
        assert get_response.status_code == 200
        assert 'method="post" action="/config"' not in get_response.text
        assert post_response.status_code == 403
        assert post_response.text == "Permission denied."
    finally:
        for globals_dict, original in originals.values():
            globals_dict["require_permission"] = original


def test_config_edit_requires_csrf(tmp_path: Path, monkeypatch):
    config_path = _write_config(tmp_path)
    original = config_path.read_text()
    client = _authed_client(tmp_path, monkeypatch, config_path)
    data = _valid_post_data("missing")
    data.pop("csrf_token")

    response = client.post("/config", data=data, follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert config_path.read_text() == original


def test_config_edit_updates_allowlisted_fields_creates_backup_and_preserves_rest(
    tmp_path: Path,
    monkeypatch,
):
    from armactl import service_manager

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    monkeypatch.setattr(service_manager, "restart_service", pytest.fail)
    csrf_token = _form_token(client.get("/config").text)

    response = client.post(
        "/config",
        data=_valid_post_data(csrf_token),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?saved=1"
    backups = sorted(config_path.parent.glob("config.json.before-web-config-save-*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == original_config

    updated = json.loads(config_path.read_text())
    assert updated["game"]["name"] == "Updated Server"
    assert updated["game"]["scenarioId"] == "UpdatedScenario.conf"
    assert updated["game"]["maxPlayers"] == 48
    assert updated["game"]["visible"] is True
    assert updated["game"]["gameProperties"]["battlEye"] is True
    assert updated["game"]["gameProperties"]["serverMaxViewDistance"] == 2500
    assert updated["game"]["gameProperties"]["serverMinGrassDistance"] == 60
    assert updated["game"]["passwordAdmin"] == "admin-password-secret"
    assert updated["rcon"]["password"] == "raw-rcon-secret"
    assert updated["game"]["admins"] == original_config["game"]["admins"]
    assert updated["game"]["mods"] == original_config["game"]["mods"]
    assert updated["game"]["gameProperties"]["networkViewDistance"] == 1200

    saved_page = client.get("/config?saved=1", follow_redirects=False)
    assert saved_page.status_code == 200
    assert "Config saved" in saved_page.text
    assert "Updated Server" in saved_page.text
    assert "Restart the server to apply these changes." in saved_page.text

    events = _audit_events(tmp_path)
    assert len(events) == 1
    event = events[0]
    assert event["username"] == "owner"
    assert event["action"] == "config.save"
    assert event["instance"] == "default"
    assert event["target"] == str(config_path)
    assert event["success"] is True
    assert event["exit_code"] == 0
    assert event["message"] == "Config saved."
    assert event["details"]["changed_fields"] == [
        "name",
        "scenario_id",
        "max_players",
        "visible",
        "battleye",
        "server_max_view_distance",
        "server_min_grass_distance",
    ]
    assert event["details"]["backup_path"] == str(backups[0])
    audit_text = _audit_log_text(tmp_path)
    for secret in ("admin-password-secret", "raw-rcon-secret"):
        assert secret not in audit_text


def test_config_edit_noop_save_does_not_backup_or_request_restart(
    tmp_path: Path,
    monkeypatch,
):
    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)

    response = client.post(
        "/config",
        data=_unchanged_post_data(csrf_token),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?unchanged=1"
    assert json.loads(config_path.read_text()) == original_config
    assert list(config_path.parent.glob("config.json.before-web-config-save-*.bak")) == []
    audit_path = tmp_path / "logs" / "web" / "audit.log"
    assert not audit_path.exists() or audit_path.read_text(encoding="utf-8") == ""

    unchanged_page = client.get("/config?unchanged=1", follow_redirects=False)
    assert unchanged_page.status_code == 200
    assert "No config changes" in unchanged_page.text
    assert "Config was unchanged; no restart is required." in unchanged_page.text
    assert "data-config-flash-message" in unchanged_page.text
    assert "notice-success" not in unchanged_page.text


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"name": " "}, "game.name is required."),
        ({"scenario_id": ""}, "game.scenarioId is required."),
        ({"max_players": "0"}, "game.maxPlayers must be a positive integer."),
        ({"max_players": "12.5"}, "game.maxPlayers must be a positive integer."),
        (
            {"server_max_view_distance": "0"},
            "game.gameProperties.serverMaxViewDistance must be a positive integer.",
        ),
        (
            {"server_min_grass_distance": "-1"},
            "game.gameProperties.serverMinGrassDistance must be a non-negative integer.",
        ),
    ],
)
def test_config_edit_rejects_invalid_values_without_backup(
    tmp_path: Path,
    monkeypatch,
    override: dict[str, str],
    message: str,
):
    config_path = _write_config(tmp_path)
    original = json.loads(config_path.read_text())
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)
    data = _valid_post_data(csrf_token)
    data.update(override)

    response = client.post("/config", data=data, follow_redirects=False)

    assert response.status_code == 400
    assert message in response.text
    assert json.loads(config_path.read_text()) == original
    assert list(config_path.parent.glob("config.json.before-web-config-save-*.bak")) == []
    events = _audit_events(tmp_path)
    assert len(events) == 1
    event = events[0]
    assert event["username"] == "owner"
    assert event["action"] == "config.save"
    assert event["instance"] == "default"
    assert event["target"] == "config.json"
    assert event["success"] is False
    assert event["exit_code"] == 1
    assert event["message"] == message
    assert event["details"]["backup_path"] == ""
    assert set(event["details"]["changed_fields"]) == {
        "name",
        "scenario_id",
        "max_players",
        "visible",
        "battleye",
        "server_max_view_distance",
        "server_min_grass_distance",
    }
    audit_text = _audit_log_text(tmp_path)
    for secret in ("admin-password-secret", "raw-rcon-secret"):
        assert secret not in audit_text


def test_config_edit_audit_failure_reports_saved_with_warning(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit
    from armactl.web.services.audit import AuditLogError

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    monkeypatch.setattr(config_edit, "append_audit_event", fail_audit)

    response = client.post(
        "/config",
        data=_valid_post_data(csrf_token),
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Config saved" in response.text
    assert "Audit warning" in response.text
    assert "Config saved but audit logging failed." in response.text
    assert "Config was not saved." not in response.text
    updated = json.loads(config_path.read_text())
    assert updated["game"]["name"] == "Updated Server"
    assert updated["game"]["maxPlayers"] == 48
    backups = sorted(config_path.parent.glob("config.json.before-web-config-save-*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == original_config


def test_config_edit_does_not_render_secrets(tmp_path: Path, monkeypatch):
    config_path = _write_config(tmp_path)
    client = _authed_client(tmp_path, monkeypatch, config_path)
    session_token = client.cookies.get(SESSION_COOKIE_NAME)

    response = client.get("/config", follow_redirects=False)

    assert response.status_code == 200
    assert "admin-password-secret" not in response.text
    assert "raw-rcon-secret" not in response.text
    assert session_token
    assert session_token not in response.text


def test_config_edit_import_does_not_import_tui_textual(monkeypatch):
    _forget_modules("armactl.web.services.config_edit", "armactl.tui", "textual")
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "textual" or name.startswith("textual.") or name.startswith("armactl.tui"):
            blocked_imports.append(name)
            raise AssertionError(f"config edit imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.services.config_edit")

    assert module.__name__ == "armactl.web.services.config_edit"
    assert blocked_imports == []
    assert "textual" not in sys.modules
