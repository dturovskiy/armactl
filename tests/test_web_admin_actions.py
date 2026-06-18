"""Tests for safe web management of game admins."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import pytest
from starlette.exceptions import StarletteDeprecationWarning

from armactl.config_manager import ConfigError
from armactl.state import ServerState
from armactl.web.auth.cookies import SESSION_COOKIE_NAME
from armactl.web.auth.permissions import ADMINS_VIEW
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username


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


def _admins_page(*, admin_id: str = "76561198000000001") -> dict:
    return {
        "instance": "default",
        "available": True,
        "error": "",
        "status": {"lifecycle": "running", "installed": True, "running": True},
        "paths": {"config_path": "/srv/armactl-data/default/config/config.json"},
        "official_admins": [
            {
                "identity_id": admin_id,
                "name": "Local Captain",
                "source": "local",
            }
        ],
        "official_count": 1,
        "local_labels": [
            {
                "identity_id": admin_id,
                "name": "Local Captain",
                "source": "local",
            }
        ],
        "local_label_count": 1,
        "local_labels_path": "/srv/armactl-data/default/config/admins-state.json",
        "local_labels_error": "",
    }


def _state(config_path: Path) -> ServerState:
    return ServerState(
        server_installed=True,
        config_exists=True,
        service_exists=True,
        config_path=str(config_path),
    )


def _write_admin_config(tmp_path: Path, admins: list[str]) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "bindAddress": "0.0.0.0",
                "bindPort": 2001,
                "publicPort": 2001,
                "game": {
                    "name": "Test Server",
                    "scenarioId": "Scenario.conf",
                    "maxPlayers": 32,
                    "admins": admins,
                    "mods": [],
                },
            },
            indent=2,
        )
    )
    return config_path


def _authed_client(tmp_path: Path, monkeypatch, page: dict | None = None):
    password = "owner admins password"
    setup_owner_user(tmp_path, "owner", password)
    _patch_admins_page(monkeypatch, page)
    from armactl.web.app import create_app

    app = create_app(data_root=tmp_path)
    client = _client(app)
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    return client


def _admins_csrf_token(client) -> str:
    response = client.get("/admins", follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    events = [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines()]
    return [event for event in events if (event.get('details') or {}).get('phase') != 'intent']


def _patch_admins_page(monkeypatch, page: dict | None = None) -> None:
    from armactl.web import facade

    monkeypatch.setattr(facade, "load_admins_page", lambda instance: page or _admins_page())


def test_admin_action_helper_calls_admins_manager_add_and_reports_update(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str, str]] = []
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        calls.append((path, admin_reference, name))
        return False

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)

    result = admin_actions.run_admin_action(
        admin_actions.ACTION_ADD,
        admin_reference="76561198000000002",
        label="Updated Captain",
    )

    assert result.success is True
    assert result.changed is True
    assert result.action == "admin.update"
    assert result.message == "Admin updated."
    assert calls == [(config_path, "76561198000000002", "Updated Captain")]


def test_admin_action_helper_returns_controlled_backend_error(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        raise ConfigError("backend failed token=raw-admin-secret")

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)

    result = admin_actions.run_admin_action(
        admin_actions.ACTION_ADD,
        admin_reference="76561198000000002",
        label="Captain",
    )

    assert result.success is False
    assert result.changed is False
    assert result.message == "backend failed token=***"
    assert "raw-admin-secret" not in result.message


def test_admins_unexpected_backend_exception_is_not_rendered_as_action_result(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import admin_actions
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_admin_config(tmp_path, [])
    setup_owner_user(tmp_path, "owner", "owner admins password")
    _patch_admins_page(monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["game"]["admins"] = [admin_reference]
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        raise RuntimeError("unexpected mutation bug token=raw-admin-secret")

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)
    client = _client(
        create_app(data_root=tmp_path),
        raise_server_exceptions=False,
    )
    _login(client, "owner", "owner admins password")
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000002",
            "label": "Captain token=raw-admin-secret",
        },
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Internal Server Error" in response.text
    assert "Admin added." not in response.text
    assert "Admin action is unavailable." not in response.text
    assert "raw-admin-secret" not in response.text
    assert "Traceback" not in response.text
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["admins"] == [
        "76561198000000002"
    ]
    assert list_pending_work(tmp_path / "web" / "web.db") == []
    audit_text = (tmp_path / 'logs' / 'web' / 'audit.log').read_text(encoding='utf-8')
    assert 'raw-admin-secret' not in audit_text


def test_admins_routes_require_authentication(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    get_response = client.get("/admins", follow_redirects=False)
    add_response = client.post("/admins/add", data={}, follow_redirects=False)
    remove_response = client.post("/admins/remove", data={}, follow_redirects=False)

    assert get_response.status_code == 303
    assert get_response.headers["location"] == "/login"
    assert add_response.status_code == 303
    assert add_response.headers["location"] == "/login"
    assert remove_response.status_code == 303
    assert remove_response.headers["location"] == "/login"


def test_admins_post_requires_manage_permission(
    tmp_path: Path, monkeypatch, set_web_owner_permissions
):
    from armactl.web.app import create_app
    from armactl.web.services import admin_actions

    setup_owner_user(tmp_path, "owner", "owner admins password")
    set_web_owner_permissions({ADMINS_VIEW})
    app = create_app(data_root=tmp_path)
    _patch_admins_page(monkeypatch)
    monkeypatch.setattr(admin_actions, "run_admin_action_and_audit", AssertionError)
    client = _client(app)
    _login(client, "owner", "owner admins password")
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000002",
            "label": "Captain",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_admins_post_requires_valid_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(admin_actions, "run_admin_action_and_audit", AssertionError)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": "wrong-token",
            "admin_reference": "76561198000000002",
            "label": "Captain",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_admins_plain_get_does_not_show_restart_required_notice(
    tmp_path: Path,
    monkeypatch,
):
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/admins", follow_redirects=False)

    assert response.status_code == 200
    assert "Restart the server to apply admin changes." not in response.text


def test_admins_add_success_writes_safe_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str, str]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        calls.append((path, admin_reference, name))
        return True

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000002",
            "label": "Captain",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Admin added." in response.text
    assert "Restart required" in response.text
    assert calls == [(config_path, "76561198000000002", "Captain")]
    event = _audit_events(tmp_path)[0]
    assert event["username"] == "owner"
    assert event["action"] == "admin.add"
    assert event["instance"] == "default"
    assert event["target"] == "76561198000000002"
    assert event["success"] is True
    assert event['details']['changed'] == 'yes'
    from armactl.web.services.pending_work import KIND_ADMINS, get_pending_work

    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_ADMINS)
    assert item is not None
    assert item.kind == "admins"
    assert item.source_path == "/admins"
    assert item.source_action == "admin.add"
    assert item.title == "Admin changes"
    assert item.details == "76561198000000002"


def test_admins_update_success_writes_update_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    monkeypatch.setattr(
        admin_actions.admins_manager,
        "add_admin",
        lambda path, admin_reference, name="": False,
    )
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000002",
            "label": "Updated Captain",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Admin updated." in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "admin.update"
    assert event["success"] is True
    assert event["target"] == "76561198000000002"


def test_admins_pending_db_failure_writes_fallback_and_warns(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import admin_actions, pending_work

    config_path = _write_admin_config(tmp_path, [])
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def fail_pending_db(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_pending_db)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "ABCDEF1234567890",
            "label": "Captain token=raw-admin-secret",
        },
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Admin added." in response.text
    assert "Restart the server to apply admin changes." in response.text
    assert "Restart tracking warning" in response.text
    assert pending_work.PENDING_WORK_FALLBACK_WARNING in response.text
    assert "raw-pending-secret" not in response.text
    assert "raw-admin-secret" not in response.text
    item = pending_work.get_fallback_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_ADMINS,
    )
    assert item is not None
    assert item.is_fallback is True
    assert item.source_action == "admin.add"
    assert item.details == "ABCDEF1234567890"
    assert "raw-admin-secret" not in pending_work.fallback_pending_work_path(
        tmp_path / "web" / "web.db"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("initial_admins", "expected_action"),
    [([], "admin.add"), (["ABCDEF1234567890"], "admin.update")],
)
def test_admins_add_or_update_audit_failure_after_change_still_marks_pending_work(
    tmp_path: Path,
    monkeypatch,
    initial_admins: list[str],
    expected_action: str,
):
    from armactl.web.services import admin_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_admin_config(tmp_path, initial_admins)
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full token=raw-audit-secret")

    monkeypatch.setattr(admin_actions, "append_audit_event", fail_audit)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "ABCDEF1234567890",
            "label": "Captain token=raw-admin-secret",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert 'Admin action was not run because audit logging failed.' in response.text
    assert 'Restart the server to apply admin changes.' not in response.text
    assert 'raw-admin-secret' not in response.text
    updated = json.loads(config_path.read_text())
    assert updated['game']['admins'] == initial_admins
    assert list_pending_work(tmp_path / 'web' / 'web.db') == []
    audit_path = tmp_path / "logs" / "web" / "audit.log"
    audit_text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    assert "raw-admin-secret" not in audit_text
    assert "raw-audit-secret" not in audit_text


def test_admins_remove_audit_failure_after_change_still_marks_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import admin_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_admin_config(tmp_path, ["ABCDEF1234567890"])
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    monkeypatch.setattr(admin_actions, "append_audit_event", fail_audit)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/remove",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "ABCDEF1234567890",
            "confirm": "remove",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert 'Admin action was not run because audit logging failed.' in response.text
    assert 'Restart the server to apply admin changes.' not in response.text
    updated = json.loads(config_path.read_text())
    assert updated['game']['admins'] == ['ABCDEF1234567890']
    assert list_pending_work(tmp_path / 'web' / 'web.db') == []


def test_admins_audit_failure_without_change_does_not_request_restart(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import admin_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_admin_config(tmp_path, [])
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    monkeypatch.setattr(admin_actions, "append_audit_event", fail_audit)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/remove",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "ABCDEF1234567890",
            "confirm": "remove",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert 'Admin action was not run because audit logging failed.' in response.text
    assert "Restart the server to apply admin changes." not in response.text
    assert json.loads(config_path.read_text())["game"]["admins"] == []
    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_admins_remove_requires_confirmation(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(admin_actions.admins_manager, "remove_admin", AssertionError)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/remove",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000001",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Confirmation is required to remove this admin." in response.text
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_admins_remove_success_writes_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def remove_admin(path: Path, admin_reference: str) -> bool:
        calls.append((path, admin_reference))
        return True

    monkeypatch.setattr(admin_actions.admins_manager, "remove_admin", remove_admin)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/remove",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000001",
            "confirm": "remove",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Admin removed." in response.text
    assert calls == [(config_path, "76561198000000001")]
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "admin.remove"
    assert event["target"] == "76561198000000001"
    assert event["success"] is True
    assert event['details']['changed'] == 'yes'


def test_admins_unchanged_remove_does_not_show_restart_required_notice(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    monkeypatch.setattr(
        admin_actions.admins_manager,
        "remove_admin",
        lambda path, admin_reference: False,
    )
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/remove",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000001",
            "confirm": "remove",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Admin was unchanged." in response.text
    assert "Restart the server to apply admin changes." not in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "admin.remove"
    assert event['details']['changed'] == 'no'
    from armactl.web.services.pending_work import list_pending_work

    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_admins_backend_error_is_controlled_and_audited(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        raise ConfigError("admin save failed token=raw-route-secret")

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000002",
            "label": "Captain",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "admin save failed token=***" in response.text
    assert "raw-route-secret" not in response.text
    assert "Traceback" not in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "admin.add"
    assert event["success"] is False
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "raw-route-secret" not in audit_text
    assert "token=***" in audit_text


def test_admins_ambiguous_steam_nickname_error_is_localized(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.i18n import LANGUAGE_COOKIE_NAME
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    client = _authed_client(tmp_path, monkeypatch)
    client.cookies.set(LANGUAGE_COOKIE_NAME, "uk", path="/")
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        raise ConfigError(
            "Steam nickname lookup is ambiguous. Paste the profile URL or SteamID64."
        )

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "1",
            "label": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Нікнейм Steam неоднозначний. Вставте URL профілю або SteamID64." in response.text
    assert "Steam nickname lookup is ambiguous" not in response.text


def test_admins_html_and_audit_do_not_expose_auth_secrets(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    password = "owner secret admins password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    from armactl.web.app import create_app

    _patch_admins_page(monkeypatch)
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        raise ConfigError("admin save failed token=render-secret")

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)
    csrf_token = _admins_csrf_token(client)

    page_response = client.get("/admins", follow_redirects=False)
    action_response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "76561198000000002",
            "label": "Captain",
        },
        follow_redirects=False,
    )
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    combined_html = page_response.text + action_response.text

    assert action_response.status_code == 400
    assert password not in combined_html
    assert user.password_hash not in combined_html
    assert session_token
    assert session_token not in combined_html
    assert "render-secret" not in combined_html
    assert "Traceback" not in combined_html
    assert password not in audit_text
    assert user.password_hash not in audit_text
    assert session_token not in audit_text
    assert "render-secret" not in audit_text
