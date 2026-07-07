"""Tests for safe web editing of basic server config fields."""

from __future__ import annotations

import json
import re
import warnings
from copy import deepcopy
from html import unescape
from pathlib import Path
from typing import Any

import pytest
from starlette.exceptions import StarletteDeprecationWarning
from web_route_helpers import _session_cookie_name

from armactl.state import PortInfo, ServerState
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


def _raw_editor_value(html: str) -> str:
    match = re.search(
        r'<textarea(?=[^>]*name="raw_config")[^>]*>(.*?)</textarea>',
        html,
        re.S,
    )
    assert match is not None
    return unescape(match.group(1))


def _raw_editor_loaded_value(html: str) -> str:
    match = re.search(
        r'<textarea(?=[^>]*name="raw_config")'
        r'(?=[^>]*data-loaded-config="([^"]*)")[^>]*>',
        html,
        re.S,
    )
    assert match is not None
    return unescape(match.group(1))


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
                "disableThirdPerson": True,
                "fastValidation": True,
                "VONCanTransmitCrossFaction": False,
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
    from armactl.web.page_models import common as page_common
    from armactl.web.services import config_edit

    state = _state_for(config_path)
    monkeypatch.setattr(page_common.discovery, "discover", lambda instance, save=False: state)
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
        "disable_third_person": "true",
        "battleye": "true",
        "server_max_view_distance": "2500",
        "server_min_grass_distance": "60",
    }


def _audit_log_text(data_root: Path) -> str:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return audit_path.read_text(encoding="utf-8")


def _audit_events(data_root: Path) -> list[dict[str, Any]]:
    events = [json.loads(line) for line in _audit_log_text(data_root).splitlines()]
    return [event for event in events if (event.get("details") or {}).get("phase") != "intent"]


def _unchanged_post_data(csrf_token: str) -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "name": "Old Server",
        "scenario_id": "OldScenario.conf",
        "max_players": "32",
        "disable_third_person": "true",
        "server_max_view_distance": "1600",
        "server_min_grass_distance": "30",
    }


def test_config_edit_descriptor_registry_covers_current_safe_fields_only():
    from armactl.server_config_schema import web_config_field_descriptors
    from armactl.web.auth.permissions import SETTINGS_MANAGE
    from armactl.web.services import config_edit

    descriptors = config_edit.editable_config_field_descriptors()

    assert descriptors == web_config_field_descriptors()
    assert config_edit.CONFIG_FIELD_DESCRIPTORS == web_config_field_descriptors()
    assert tuple(descriptor.form_name for descriptor in descriptors) == (
        "name",
        "scenario_id",
        "max_players",
        "visible",
        "disable_third_person",
        "battleye",
        "server_max_view_distance",
        "server_min_grass_distance",
    )
    assert tuple(descriptor.form_name for descriptor in descriptors) == (
        config_edit.ALLOWLISTED_CONFIG_FORM_FIELDS
    )
    assert tuple(".".join(descriptor.config_path) for descriptor in descriptors) == (
        "game.name",
        "game.scenarioId",
        "game.maxPlayers",
        "game.visible",
        "game.gameProperties.disableThirdPerson",
        "game.gameProperties.battlEye",
        "game.gameProperties.serverMaxViewDistance",
        "game.gameProperties.serverMinGrassDistance",
    )
    assert {descriptor.permission for descriptor in descriptors} == {SETTINGS_MANAGE}
    assert {descriptor.risk_class for descriptor in descriptors} == {"safe"}
    assert {descriptor.secret_behavior for descriptor in descriptors} == {"not-secret"}
    assert {descriptor.restart_behavior for descriptor in descriptors} == {
        "changed-values-mark-restart-pending"
    }
    assert all(descriptor.audit_field_name == descriptor.form_name for descriptor in descriptors)
    third_person = next(
        descriptor for descriptor in descriptors if descriptor.form_name == "disable_third_person"
    )
    assert third_person.config_path == ("game", "gameProperties", "disableThirdPerson")
    assert third_person.value_type == "boolean"
    assert third_person.ui is not None
    assert third_person.ui.control == "checkbox"
    assert third_person.ui.label == "Disable third-person view"
    assert third_person.ui.helper_text == "Applies a gameplay camera rule after restart."
    assert third_person.ui.impact_label == "Gameplay"
    visible = next(descriptor for descriptor in descriptors if descriptor.form_name == "visible")
    assert visible.ui is not None
    assert visible.ui.label == "Show server in server browser"
    assert visible.ui.helper_text == (
        "Controls server-browser discovery only; it does not change bind or firewall settings."
    )
    assert visible.ui.impact_label == "Discovery"

    field_groups = config_edit.build_config_edit_field_groups(_sample_config())
    assert [
        (group["section"], tuple(field["name"] for field in group["fields"]))
        for group in field_groups
    ] == [
        ("server_identity", ("name", "scenario_id")),
        ("capacity_visibility", ("max_players", "visible")),
        ("gameplay_security", ("disable_third_person", "battleye")),
        ("view_distance", ("server_max_view_distance", "server_min_grass_distance")),
    ]
    assert all(
        field["restart_required"]
        for group in field_groups
        for field in group["fields"]
    )
    assert {field["ui"]["impact_label"] for group in field_groups for field in group["fields"]} == {
        "Discovery",
        "Gameplay",
        "Performance",
        "Security",
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
    for heading in ("Status", "Server", "Network", "Files", "Basic server settings"):
        assert f">{heading}<" in response.text
    for group_heading in (
        "Server identity",
        "Capacity and visibility",
        "Gameplay and security",
        "View distance",
    ):
        assert f">{group_heading}<" in response.text
    assert 'method="post" action="/config"' in response.text
    assert 'name="name"' in response.text
    assert 'value="Old Server"' in response.text
    assert 'name="scenario_id"' in response.text
    assert 'value="OldScenario.conf"' in response.text
    assert 'name="server_max_view_distance"' in response.text
    assert 'name="server_min_grass_distance"' in response.text
    assert "Restart the server to apply saved config changes." in response.text
    assert response.text.count("field-wide") >= 2
    assert response.text.count("config-meta-restart") == 8
    for impact_label in ("Discovery", "Gameplay", "Performance", "Security"):
        assert f">{impact_label}<" in response.text

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
        "disable_third_person",
        "battleye",
    }
    assert "bind_port" not in editable_names
    assert "rcon_port" not in editable_names
    assert "password" not in editable_names
    assert "disableThirdPerson" not in form_match.group(1)
    assert "Show server in server browser" in response.text
    assert (
        "Controls server-browser discovery only; it does not change bind or firewall settings."
        in response.text
    )
    assert "Disable third-person view" in response.text
    assert "Applies a gameplay camera rule after restart." in response.text
    assert "Disabling BattlEye lowers anti-cheat protection." in response.text
    assert "Higher values can increase server and client load." in response.text
    assert 'name="disable_third_person" value="true" checked' in response.text


def test_config_page_uses_generated_default_for_missing_disable_third_person(
    tmp_path: Path, monkeypatch
):
    config = _sample_config()
    del config["game"]["gameProperties"]["disableThirdPerson"]
    config_path = _write_config(tmp_path, config)
    client = _authed_client(tmp_path, monkeypatch, config_path)

    response = client.get("/config", follow_redirects=False)

    assert response.status_code == 200
    assert 'name="disable_third_person" value="true" checked' in response.text


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
    assert str(config_path) not in response.text
    assert str(config_path.parents[1]) not in response.text
    assert str(config_path.parents[1] / "server") not in response.text
    assert "config.json" in response.text
    assert "instance config" in response.text
    assert "server install" in response.text
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
    set_web_owner_permissions,
):
    from armactl.web.auth.permissions import CONFIG_VIEW, SETTINGS_MANAGE
    from armactl.web.services import config_edit

    config_path = _write_config(tmp_path)
    set_web_owner_permissions({CONFIG_VIEW})
    client = _authed_client(tmp_path, monkeypatch, config_path)
    monkeypatch.setattr(config_edit, "save_default_config", pytest.fail)

    get_response = client.get("/config", follow_redirects=False)
    post_response = client.post("/config", data={}, follow_redirects=False)

    assert SETTINGS_MANAGE != CONFIG_VIEW
    assert get_response.status_code == 200
    assert 'method="post" action="/config"' not in get_response.text
    assert post_response.status_code == 403
    assert post_response.text == "Permission denied."


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
    assert updated["game"]["gameProperties"]["disableThirdPerson"] is True
    assert updated["game"]["gameProperties"]["fastValidation"] is True
    assert updated["game"]["gameProperties"]["VONCanTransmitCrossFaction"] is False

    saved_page = client.get("/config?saved=1", follow_redirects=False)
    assert saved_page.status_code == 200
    assert "Config saved" in saved_page.text
    assert "Updated Server" in saved_page.text
    assert "Restart the server to apply these changes." in saved_page.text
    from armactl.web.services.pending_work import KIND_CONFIG, get_pending_work

    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG)
    assert item is not None
    assert item.kind == "config"
    assert item.source_path == "/config"
    assert item.source_action == "config.save"
    assert item.title == "Config changes"
    assert item.resolution_action == "restart game server"
    assert "max_players" in item.details

    events = _audit_events(tmp_path)
    assert len(events) == 1
    event = events[0]
    assert event["username"] == "owner"
    assert event["action"] == "config.save"
    assert event["instance"] == "default"
    assert event["target"] == "config.json"
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
    assert event["details"]["backup_created"] is True
    assert event["details"]["backup_name"] == backups[0].name
    assert "backup_path" not in event["details"]
    assert "password" not in event["details"]["changed_fields"]
    assert "disable_third_person" not in event["details"]["changed_fields"]
    assert "fast_validation" not in event["details"]["changed_fields"]
    audit_text = _audit_log_text(tmp_path)
    assert str(config_path) not in audit_text
    assert str(backups[0]) not in audit_text
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
    from armactl.web.services.pending_work import list_pending_work

    assert list_pending_work(tmp_path / "web" / "web.db") == []

    unchanged_page = client.get("/config?unchanged=1", follow_redirects=False)
    assert unchanged_page.status_code == 200
    assert "No config changes" in unchanged_page.text
    assert "Config was unchanged; no restart is required." in unchanged_page.text
    assert "data-config-flash-message" in unchanged_page.text
    assert "notice-success" not in unchanged_page.text


def test_config_edit_disable_third_person_toggle_tracks_pending_state(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services.pending_work import KIND_CONFIG, get_pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)

    disable_data = _unchanged_post_data(csrf_token)
    disable_data.pop("disable_third_person")
    response = client.post(
        "/config",
        data=disable_data,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?saved=1"
    updated = json.loads(config_path.read_text())
    assert updated["game"]["gameProperties"]["disableThirdPerson"] is False
    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG)
    assert item is not None
    assert item.details == "disable_third_person"

    csrf_token = _form_token(client.get("/config").text)
    response = client.post(
        "/config",
        data=_unchanged_post_data(csrf_token),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?saved=1"
    assert json.loads(config_path.read_text()) == original_config
    assert get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG) is None

    events = _audit_events(tmp_path)
    assert [event["details"]["changed_fields"] for event in events] == [
        ["disable_third_person"],
        ["disable_third_person"],
    ]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"name": " "}, "game.name is required."),
        ({"scenario_id": ""}, "game.scenarioId is required."),
        ({"max_players": "0"}, "game.maxPlayers must be a positive integer."),
        ({"max_players": "12.5"}, "game.maxPlayers must be a positive integer."),
        ({"visible": "maybe"}, "Boolean field value is invalid."),
        ({"disable_third_person": "maybe"}, "Boolean field value is invalid."),
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
    assert event["details"]["backup_created"] is False
    assert event["details"]["backup_name"] == ""
    assert "backup_path" not in event["details"]
    assert set(event["details"]["changed_fields"]) == {
        "name",
        "scenario_id",
        "max_players",
        "visible",
        "disable_third_person",
        "battleye",
        "server_max_view_distance",
        "server_min_grass_distance",
    }
    audit_text = _audit_log_text(tmp_path)
    for secret in ("admin-password-secret", "raw-rcon-secret"):
        assert secret not in audit_text


def test_config_service_pending_db_failure_writes_fallback_and_warns(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit, pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    _patch_discovery(monkeypatch, config_path)

    def fail_pending_db(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_pending_db)

    result = config_edit.save_default_config_and_audit(
        "default",
        _valid_post_data("unused"),
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    assert result.pending_work_warning == pending_work.PENDING_WORK_FALLBACK_WARNING
    assert result.pending_work_error == ""
    updated = json.loads(config_path.read_text())
    assert updated["game"]["name"] == "Updated Server"
    item = pending_work.get_fallback_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_CONFIG,
    )
    assert item is not None
    assert item.is_fallback is True
    assert item.source_action == "config.save"
    assert "max_players" in item.details
    sidecar_path = pending_work.fallback_pending_work_path(tmp_path / "web" / "web.db")
    assert sidecar_path.stat().st_mode & 0o777 == 0o600
    assert "raw-pending-secret" not in sidecar_path.read_text(encoding="utf-8")


def test_config_edit_pending_warning_from_service_is_rendered(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit, pending_work

    config_path = _write_config(tmp_path, deepcopy(_sample_config()))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)

    def save_with_pending_warning(*args, **kwargs):
        return config_edit.ConfigEditResult(
            config_path=config_path,
            backup_path=None,
            changed_fields=("max_players",),
            pending_work_warning=pending_work.PENDING_WORK_FALLBACK_WARNING,
        )

    monkeypatch.setattr(config_edit, "save_default_config_and_audit", save_with_pending_warning)

    response = client.post(
        "/config",
        data=_valid_post_data(csrf_token),
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Config saved" in response.text
    assert "Restart tracking warning" in response.text
    assert pending_work.PENDING_WORK_FALLBACK_WARNING in response.text


def test_config_service_pending_db_and_fallback_failure_is_controlled(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit, pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    _patch_discovery(monkeypatch, config_path)

    def fail_pending_db(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    def fail_fallback(*args, **kwargs):
        raise OSError("fallback failed token=raw-fallback-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_pending_db)
    monkeypatch.setattr(pending_work, "mark_restart_pending_fallback", fail_fallback)

    result = config_edit.save_default_config_and_audit(
        "default",
        _valid_post_data("unused"),
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    assert result.pending_work_warning == ""
    assert result.pending_work_error == pending_work.PENDING_WORK_STORAGE_FAILED_MESSAGE
    updated = json.loads(config_path.read_text())
    assert updated["game"]["name"] == "Updated Server"
    assert pending_work.list_fallback_pending_work(tmp_path / "web" / "web.db") == []


def test_config_edit_pending_error_from_service_is_rendered(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit, pending_work

    config_path = _write_config(tmp_path, deepcopy(_sample_config()))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)

    def save_with_pending_error(*args, **kwargs):
        return config_edit.ConfigEditResult(
            config_path=config_path,
            backup_path=None,
            changed_fields=("max_players",),
            pending_work_error=pending_work.PENDING_WORK_STORAGE_FAILED_MESSAGE,
        )

    monkeypatch.setattr(config_edit, "save_default_config_and_audit", save_with_pending_error)

    response = client.post(
        "/config",
        data=_valid_post_data(csrf_token),
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Config saved" in response.text
    assert "Restart tracking failed" in response.text
    assert pending_work.PENDING_WORK_STORAGE_FAILED_MESSAGE in response.text


def test_config_service_noop_with_pending_db_failure_does_not_create_fallback(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit, pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    _patch_discovery(monkeypatch, config_path)

    def fail_pending_db(*args, **kwargs):
        raise RuntimeError("pending should not be called")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_pending_db)

    result = config_edit.save_default_config_and_audit(
        "default",
        _unchanged_post_data("unused"),
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    assert result.changed_fields == ()
    assert json.loads(config_path.read_text()) == original_config
    assert pending_work.list_fallback_pending_work(tmp_path / "web" / "web.db") == []


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
        if (kwargs.get("details") or {}).get("phase") == "outcome":
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

    from armactl.web.services.pending_work import KIND_CONFIG, get_pending_work

    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG)
    assert item is not None
    assert item.source_action == "config.save"
    assert "max_players" in item.details
    audit_path = tmp_path / "logs" / "web" / "audit.log"
    audit_text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    for secret in ("admin-password-secret", "raw-rcon-secret"):
        assert secret not in response.text
        assert secret not in audit_text


def test_config_edit_does_not_render_secrets(tmp_path: Path, monkeypatch):
    config_path = _write_config(tmp_path)
    client = _authed_client(tmp_path, monkeypatch, config_path)
    session_token = client.cookies.get(_session_cookie_name(client))

    response = client.get("/config", follow_redirects=False)

    assert response.status_code == 200
    assert "admin-password-secret" not in response.text
    assert "raw-rcon-secret" not in response.text
    assert session_token
    assert session_token not in response.text


def test_config_edit_import_does_not_import_tui_textual(
    assert_import_does_not_import_modules,
):
    assert_import_does_not_import_modules(
        "armactl.web.services.config_edit",
        ("armactl.tui", "textual"),
    )


def test_config_edit_intent_audit_failure_aborts_save(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    from armactl.web.services import config_edit

    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)

    def fail_intent(*args, **kwargs):
        if (kwargs.get("details") or {}).get("phase") == "intent":
            raise AuditLogError("disk full")

    monkeypatch.setattr(config_edit, "append_audit_event", fail_intent)
    response = client.post(
        "/config",
        data=_valid_post_data(csrf_token),
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Config was not saved because audit logging failed." in response.text
    assert json.loads(config_path.read_text()) == original_config
    assert not list(config_path.parent.glob("config.json.before-web-config-save-*.bak"))
    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_config_page_shows_redacted_advanced_json_editor(tmp_path: Path, monkeypatch):
    config_path = _write_config(tmp_path)
    client = _authed_client(tmp_path, monkeypatch, config_path)

    response = client.get("/config", follow_redirects=False)

    assert response.status_code == 200
    assert 'method="post" action="/config/raw"' in response.text
    assert "Advanced config JSON" in response.text
    assert (
        "Network, RCON, and secret fields are not exposed as safe controls; "
        "secret edits are rejected by the guarded raw editor."
    ) in response.text
    assert "&lt;redacted: unchanged&gt;" in response.text
    assert "raw-rcon-secret" not in response.text
    assert "admin-password-secret" not in response.text
    assert 'name="confirm" value="raw-config-save" required' in response.text
    assert 'type="button" class="secondary" data-config-raw-reset' in response.text
    assert "Reset to loaded config" in response.text
    assert "data-config-raw-editor" in response.text
    assert "data-loaded-config=" in response.text
    assert 'href="/config/raw"' in response.text
    assert "Reload config from disk" in response.text


def test_raw_config_page_reload_route_renders_editor(tmp_path: Path, monkeypatch):
    config_path = _write_config(tmp_path)
    client = _authed_client(tmp_path, monkeypatch, config_path)

    response = client.get("/config/raw", follow_redirects=False)

    assert response.status_code == 200
    assert "Advanced config JSON" in response.text
    assert "Reload config from disk" in response.text


def test_raw_config_edit_requires_confirmation(tmp_path: Path, monkeypatch):
    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)

    response = client.post(
        "/config/raw",
        data={"csrf_token": csrf_token, "raw_config": json.dumps(original_config)},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Confirmation is required to save raw config JSON." in response.text
    assert json.loads(config_path.read_text()) == original_config
    assert list(config_path.parent.glob("config.json.before-web-config-save-*.bak")) == []


def test_raw_config_edit_rejects_invalid_json_without_backup(tmp_path: Path, monkeypatch):
    from armactl.web.services import config_edit
    from armactl.web.services.pending_work import list_pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)
    submitted_text = '{\n  "game": "still editable"\n'

    response = client.post(
        "/config/raw",
        data={
            "csrf_token": csrf_token,
            "confirm": "raw-config-save",
            "raw_config": submitted_text,
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Invalid JSON at line" in response.text
    assert "Traceback" not in response.text
    assert str(config_path) not in response.text
    assert _raw_editor_value(response.text) == submitted_text
    assert _raw_editor_loaded_value(response.text) == config_edit.build_raw_config_editor_text(
        original_config,
    )
    assert json.loads(config_path.read_text()) == original_config
    assert list(config_path.parent.glob("config.json.before-web-config-save-*.bak")) == []
    assert list_pending_work(tmp_path / "web" / "web.db") == []
    audit_text = _audit_log_text(tmp_path)
    assert "raw-rcon-secret" not in audit_text
    assert "admin-password-secret" not in audit_text


def test_raw_config_edit_rejects_secret_changes(tmp_path: Path, monkeypatch):
    from armactl.web.services import config_edit

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)
    submitted = json.loads(config_edit.build_raw_config_editor_text(original_config))
    submitted["rcon"]["password"] = "changed-secret"

    response = client.post(
        "/config/raw",
        data={
            "csrf_token": csrf_token,
            "confirm": "raw-config-save",
            "raw_config": json.dumps(submitted),
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Secret fields cannot be changed in the web config editor." in response.text
    assert "Traceback" not in response.text
    assert str(config_path) not in response.text
    assert "changed-secret" not in response.text
    assert _raw_editor_value(response.text) == config_edit.build_raw_config_editor_text(
        original_config,
    )
    assert json.loads(config_path.read_text()) == original_config
    assert list(config_path.parent.glob("config.json.before-web-config-save-*.bak")) == []
    audit_text = _audit_log_text(tmp_path)
    assert "changed-secret" not in audit_text
    assert "raw-rcon-secret" not in audit_text
    assert "admin-password-secret" not in audit_text


def test_raw_config_edit_updates_advanced_field_preserves_secrets_and_tracks_pending(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit
    from armactl.web.services.pending_work import KIND_CONFIG, get_pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)
    submitted = json.loads(config_edit.build_raw_config_editor_text(original_config))
    submitted["game"]["gameProperties"]["networkViewDistance"] = 1800
    submitted["game"]["gameProperties"]["fastValidation"] = False

    response = client.post(
        "/config/raw",
        data={
            "csrf_token": csrf_token,
            "confirm": "raw-config-save",
            "raw_config": json.dumps(submitted, indent=2),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?saved=1"
    backups = sorted(config_path.parent.glob("config.json.before-web-config-save-*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == original_config
    updated = json.loads(config_path.read_text())
    assert updated["game"]["gameProperties"]["networkViewDistance"] == 1800
    assert updated["game"]["gameProperties"]["fastValidation"] is False
    assert updated["rcon"]["password"] == "raw-rcon-secret"
    assert updated["game"]["passwordAdmin"] == "admin-password-secret"

    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG)
    assert item is not None
    assert item.source_action == "config.save"
    assert "game.gameProperties.networkViewDistance" in item.details
    assert "game.gameProperties.fastValidation" in item.details

    events = _audit_events(tmp_path)
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "config.save"
    assert event["target"] == "config.json"
    assert event["message"] == "Raw config saved."
    assert event["details"]["backup_created"] is True
    assert event["details"]["backup_name"] == backups[0].name
    assert event["details"]["changed_fields"] == [
        "game.gameProperties.fastValidation",
        "game.gameProperties.networkViewDistance",
    ]
    audit_text = _audit_log_text(tmp_path)
    assert str(config_path) not in audit_text
    assert str(backups[0]) not in audit_text
    assert "raw-rcon-secret" not in audit_text
    assert "admin-password-secret" not in audit_text


def test_raw_config_service_audit_failure_records_pending_marker(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import config_edit, pending_work
    from armactl.web.services.audit import AuditLogError

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    _patch_discovery(monkeypatch, config_path)
    submitted = json.loads(config_edit.build_raw_config_editor_text(original_config))
    submitted["game"]["gameProperties"]["networkViewDistance"] = 1800

    def fail_outcome(*args, **kwargs):
        if (kwargs.get("details") or {}).get("phase") == "outcome":
            raise AuditLogError("disk full token=raw-audit-secret")

    monkeypatch.setattr(config_edit, "append_audit_event", fail_outcome)

    with pytest.raises(config_edit.ConfigAuditError) as error:
        config_edit.save_default_raw_config_and_audit(
            "default",
            json.dumps(submitted),
            audit_log_path=tmp_path / "logs" / "web" / "audit.log",
            username="owner",
            db_path=tmp_path / "web" / "web.db",
        )

    assert str(error.value) == "Config saved but audit logging failed."
    assert error.value.result.audit_written is False
    updated = json.loads(config_path.read_text())
    assert updated["game"]["gameProperties"]["networkViewDistance"] == 1800
    item = pending_work.get_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_CONFIG,
    )
    assert item is not None
    assert item.source_action == "config.save"
    assert "game.gameProperties.networkViewDistance" in item.details
    assert "raw-audit-secret" not in str(error.value)
    assert "raw-rcon-secret" not in str(error.value)
    assert "admin-password-secret" not in str(error.value)


def test_raw_config_edit_rejects_secret_field_removal_without_backup(
    tmp_path: Path, monkeypatch
):
    from armactl.web.services import config_edit

    original_config = _sample_config()
    assert original_config["game"]["password"] == ""
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)
    submitted = json.loads(config_edit.build_raw_config_editor_text(original_config))
    del submitted["game"]["password"]

    response = client.post(
        "/config/raw",
        data={
            "csrf_token": csrf_token,
            "confirm": "raw-config-save",
            "raw_config": json.dumps(submitted),
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Secret fields cannot be removed in the web config editor." in response.text
    assert json.loads(config_path.read_text()) == original_config
    assert list(config_path.parent.glob("config.json.before-web-config-save-*.bak")) == []
    audit_text = _audit_log_text(tmp_path)
    assert "raw-rcon-secret" not in audit_text
    assert "admin-password-secret" not in audit_text


def test_raw_config_edit_return_to_baseline_clears_pending_restart(
    tmp_path: Path, monkeypatch
):
    from armactl.web.services import config_edit
    from armactl.web.services.pending_work import KIND_CONFIG, get_pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)
    changed = json.loads(config_edit.build_raw_config_editor_text(original_config))
    changed["game"]["gameProperties"]["networkViewDistance"] = 1800

    first_response = client.post(
        "/config/raw",
        data={
            "csrf_token": csrf_token,
            "confirm": "raw-config-save",
            "raw_config": json.dumps(changed),
        },
        follow_redirects=False,
    )

    assert first_response.status_code == 303
    assert get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG) is not None

    csrf_token = _form_token(client.get("/config").text)
    baseline = json.loads(config_edit.build_raw_config_editor_text(original_config))
    second_response = client.post(
        "/config/raw",
        data={
            "csrf_token": csrf_token,
            "confirm": "raw-config-save",
            "raw_config": json.dumps(baseline),
        },
        follow_redirects=False,
    )

    assert second_response.status_code == 303
    assert second_response.headers["location"] == "/config?saved=1"
    assert json.loads(config_path.read_text()) == original_config
    assert get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG) is None
    assert len(list(config_path.parent.glob("config.json.before-web-config-save-*.bak"))) == 2


def test_raw_config_edit_noop_does_not_backup_or_request_restart(tmp_path: Path, monkeypatch):
    from armactl.web.services import config_edit
    from armactl.web.services.pending_work import list_pending_work

    original_config = _sample_config()
    config_path = _write_config(tmp_path, deepcopy(original_config))
    client = _authed_client(tmp_path, monkeypatch, config_path)
    csrf_token = _form_token(client.get("/config").text)
    raw_config = config_edit.build_raw_config_editor_text(original_config)

    response = client.post(
        "/config/raw",
        data={
            "csrf_token": csrf_token,
            "confirm": "raw-config-save",
            "raw_config": raw_config,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?unchanged=1"
    assert json.loads(config_path.read_text()) == original_config
    assert list(config_path.parent.glob("config.json.before-web-config-save-*.bak")) == []
    assert list_pending_work(tmp_path / "web" / "web.db") == []
