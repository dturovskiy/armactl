"""Tests for safe web management of Workshop mods."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning

from armactl.addon_cleanup import CleanupResult
from armactl.config_manager import ConfigError
from armactl.mods_manager import ModAddResult, ModUpdateResult
from armactl.state import ServerState
from armactl.web.auth.cookies import SESSION_COOKIE_NAME
from armactl.web.auth.permissions import MODS_VIEW
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username


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


def _mods_page() -> dict:
    return {
        "instance": "default",
        "available": True,
        "error": "",
        "status": {"lifecycle": "running", "installed": True, "running": True},
        "paths": {"config_path": "/srv/armactl-data/default/config/config.json"},
        "count": 1,
        "mods": [
            {
                "mod_id": "AAAAAAAAAAAAAAAA",
                "name": "Active Alpha",
                "version": "1.0",
            }
        ],
        "disabled_count": 1,
        "disabled_mods": [
            {
                "mod_id": "BBBBBBBBBBBBBBBB",
                "name": "Disabled Bravo",
                "version": "",
            }
        ],
        "disabled_mods_path": "/srv/armactl-data/default/mods-state.json",
        "disabled_mods_error": "",
    }


def _state(config_path: Path) -> ServerState:
    return ServerState(
        server_installed=True,
        config_exists=True,
        service_exists=True,
        config_path=str(config_path),
    )


def _write_mod_config(tmp_path: Path, mods: list[dict[str, str]]) -> Path:
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
                    "admins": [],
                    "mods": mods,
                },
            },
            indent=2,
        )
    )
    return config_path


def _patch_management_global(monkeypatch, name: str, value) -> None:
    from armactl.web.routes import management

    monkeypatch.setattr(management, name, value)


def _authed_client(tmp_path: Path, monkeypatch, page: dict | None = None):
    from armactl.web.routes import management

    def load_page(instance: str) -> dict:
        return page or _mods_page()

    password = "owner mods password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(management, "load_mods_page", load_page)
    from armactl.web.app import create_app

    app = create_app(data_root=tmp_path)
    client = _client(app)
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    return client


def _mods_csrf_token(client) -> str:
    response = client.get("/mods", follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def test_mod_action_helper_calls_mods_manager_add_and_reports_update(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str, str, str]] = []
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mod(path: Path, mod_id: str, name: str = "", version: str = "") -> ModAddResult:
        calls.append((path, mod_id, name, version))
        return ModAddResult(mod_id, "updated")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)

    result = mod_actions.run_mod_action(
        mod_actions.ACTION_ADD,
        mod_id="aaaaaaaaaaaaaaaa",
        name="Updated Alpha",
        version="2.0",
    )

    assert result.success is True
    assert result.changed is True
    assert result.action == "mod.update"
    assert result.message == "Mod updated."
    assert calls == [(config_path, "AAAAAAAAAAAAAAAA", "Updated Alpha", "2.0")]


def test_mod_action_helper_returns_controlled_backend_error(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mod(path: Path, mod_id: str, name: str = "", version: str = "") -> ModAddResult:
        raise RuntimeError("backend exploded token=raw-mod-secret")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)

    result = mod_actions.run_mod_action(
        mod_actions.ACTION_ADD,
        mod_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )

    assert result.success is False
    assert result.changed is False
    assert result.message == "Mod action is unavailable."
    assert "raw-mod-secret" not in result.message


def test_mods_routes_require_authentication(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    get_response = client.get("/mods", follow_redirects=False)
    add_response = client.post("/mods/add", data={}, follow_redirects=False)
    disable_response = client.post("/mods/disable", data={}, follow_redirects=False)

    assert get_response.status_code == 303
    assert get_response.headers["location"] == "/login"
    assert add_response.status_code == 303
    assert add_response.headers["location"] == "/login"
    assert disable_response.status_code == 303
    assert disable_response.headers["location"] == "/login"


def test_mods_post_requires_manage_permission(
    tmp_path: Path, monkeypatch, set_web_owner_permissions
):
    from armactl.web.app import create_app
    from armactl.web.services import mod_actions

    setup_owner_user(tmp_path, "owner", "owner mods password")
    set_web_owner_permissions({MODS_VIEW})
    app = create_app(data_root=tmp_path)
    _patch_management_global(monkeypatch, "load_mods_page", lambda instance: _mods_page())
    monkeypatch.setattr(mod_actions, "run_mod_action_and_audit", AssertionError)
    client = _client(app)
    _login(client, "owner", "owner mods password")
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "CCCCCCCCCCCCCCCC",
            "name": "Charlie",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_mods_post_requires_valid_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(mod_actions, "run_mod_action_and_audit", AssertionError)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": "wrong-token",
            "mod_id": "CCCCCCCCCCCCCCCC",
            "name": "Charlie",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_mods_get_does_not_show_restart_notice(tmp_path: Path, monkeypatch):
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/mods", follow_redirects=False)

    assert response.status_code == 200
    assert "Active Alpha" in response.text
    assert "Disabled Bravo" in response.text
    assert "Restart the server to apply mod changes." not in response.text


def test_mods_add_success_writes_safe_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str, str, str]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mod(path: Path, mod_id: str, name: str = "", version: str = "") -> ModAddResult:
        calls.append((path, mod_id, name, version))
        return ModAddResult(mod_id, "added")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "cccccccccccccccc",
            "name": "Charlie",
            "version": "1.2.3",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Mod added." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert calls == [(config_path, "CCCCCCCCCCCCCCCC", "Charlie", "1.2.3")]
    event = _audit_events(tmp_path)[0]
    assert event["username"] == "owner"
    assert event["action"] == "mod.add"
    assert event["instance"] == "default"
    assert event["target"] == "CCCCCCCCCCCCCCCC"
    assert event["success"] is True
    assert event["details"]["changed"] == "yes"
    from armactl.web.services.pending_work import KIND_MODS, get_pending_work

    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_MODS)
    assert item is not None
    assert item.kind == "mods"
    assert item.source_path == "/mods"
    assert item.source_action == "mod.add"
    assert item.title == "Mod changes"
    assert item.details == "CCCCCCCCCCCCCCCC"


def test_mods_add_unchanged_writes_audit_without_restart_notice(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    monkeypatch.setattr(
        mod_actions.mods_manager,
        "add_mod_detailed",
        lambda path, mod_id, name="", version="": ModAddResult(mod_id, "unchanged"),
    )
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "AAAAAAAAAAAAAAAA",
            "name": "",
            "version": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Mod was unchanged." in response.text
    assert "Restart the server to apply mod changes." not in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.add"
    assert event["success"] is True
    assert event["details"]["changed"] == "no"
    from armactl.web.services.pending_work import list_pending_work

    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_mods_pending_db_failure_writes_fallback_and_warns(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions, pending_work

    config_path = _write_mod_config(tmp_path, [])
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def fail_pending_db(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_pending_db)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "cccccccccccccccc",
            "name": "Charlie token=raw-mod-secret",
            "version": "1.2.3",
        },
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Mod added." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert "Restart tracking warning" in response.text
    assert pending_work.PENDING_WORK_FALLBACK_WARNING in response.text
    assert "raw-pending-secret" not in response.text
    assert "raw-mod-secret" not in response.text
    item = pending_work.get_fallback_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_MODS,
    )
    assert item is not None
    assert item.is_fallback is True
    assert item.source_action == "mod.add"
    assert item.details == "CCCCCCCCCCCCCCCC"
    assert "raw-mod-secret" not in pending_work.fallback_pending_work_path(
        tmp_path / "web" / "web.db"
    ).read_text(encoding="utf-8")


def test_mods_audit_failure_after_change_still_marks_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import KIND_MODS, get_pending_work

    config_path = _write_mod_config(tmp_path, [])
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full token=raw-audit-secret")

    monkeypatch.setattr(mod_actions, "append_audit_event", fail_audit)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "cccccccccccccccc",
            "name": "Charlie token=raw-mod-secret",
            "version": "1.2.3",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Mod action completed but audit logging failed." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert "raw-mod-secret" not in response.text
    updated = json.loads(config_path.read_text())
    assert updated["game"]["mods"] == [
        {
            "modId": "CCCCCCCCCCCCCCCC",
            "name": "Charlie token=raw-mod-secret",
            "version": "1.2.3",
        }
    ]
    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_MODS)
    assert item is not None
    assert item.source_action == "mod.add"
    assert item.details == "CCCCCCCCCCCCCCCC"
    audit_path = tmp_path / "logs" / "web" / "audit.log"
    audit_text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    assert "raw-mod-secret" not in audit_text
    assert "raw-audit-secret" not in audit_text


def test_mods_audit_failure_without_change_does_not_request_restart(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_mod_config(
        tmp_path,
        [{"modId": "AAAAAAAAAAAAAAAA", "name": "Active Alpha", "version": "1.0"}],
    )
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    monkeypatch.setattr(mod_actions, "append_audit_event", fail_audit)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "AAAAAAAAAAAAAAAA",
            "name": "Active Alpha",
            "version": "1.0",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Mod action completed but audit logging failed." in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert json.loads(config_path.read_text())["game"]["mods"] == [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "Active Alpha", "version": "1.0"}
    ]
    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_mods_disable_success_writes_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def disable_mod(path: Path, mod_id: str) -> bool:
        calls.append((path, mod_id))
        return True

    monkeypatch.setattr(mod_actions.mods_manager, "disable_mod", disable_mod)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/disable",
        data={"csrf_token": csrf_token, "mod_id": "AAAAAAAAAAAAAAAA"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Mod disabled." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert calls == [(config_path, "AAAAAAAAAAAAAAAA")]
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.disable"
    assert event["details"]["changed"] == "yes"


def test_mods_enable_success_writes_audit(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def enable_mod(path: Path, mod_id: str) -> bool:
        calls.append((path, mod_id))
        return True

    monkeypatch.setattr(mod_actions.mods_manager, "enable_mod", enable_mod)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/enable",
        data={"csrf_token": csrf_token, "mod_id": "BBBBBBBBBBBBBBBB"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Mod enabled." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert calls == [(config_path, "BBBBBBBBBBBBBBBB")]
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.enable"
    assert event["details"]["changed"] == "yes"


def test_mods_remove_requires_confirmation(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(mod_actions.mods_manager, "remove_mod_detailed", AssertionError)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/remove",
        data={
            "csrf_token": csrf_token,
            "mod_id": "AAAAAAAAAAAAAAAA",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Confirmation is required to remove this mod." in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_mods_remove_success_writes_cleanup_summary_audit(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def remove_mod(path: Path, mod_id: str) -> ModUpdateResult:
        calls.append((path, mod_id))
        cleanup = CleanupResult(bytes_deleted=2048)
        return ModUpdateResult(
            config_changed=True,
            cleanup_result=cleanup,
            removed_ids={mod_id},
        )

    monkeypatch.setattr(mod_actions.mods_manager, "remove_mod_detailed", remove_mod)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/remove",
        data={
            "csrf_token": csrf_token,
            "mod_id": "AAAAAAAAAAAAAAAA",
            "confirm": "remove",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Mod removed." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert calls == [(config_path, "AAAAAAAAAAAAAAAA")]
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.remove"
    assert event["target"] == "AAAAAAAAAAAAAAAA"
    assert event["success"] is True
    assert event["details"]["changed"] == "yes"
    assert event["details"]["removed_ids"] == ["AAAAAAAAAAAAAAAA"]
    assert event["details"]["removed_id_count"] == "1"
    assert event["details"]["cleanup_freed"] == "2.00 KB"


def test_mods_backend_error_is_controlled_and_audited(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mod(path: Path, mod_id: str, name: str = "", version: str = "") -> ModAddResult:
        raise ConfigError("mod save failed token=raw-route-secret")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "CCCCCCCCCCCCCCCC",
            "name": "Charlie",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "mod save failed token=***" in response.text
    assert "raw-route-secret" not in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert "Traceback" not in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.add"
    assert event["success"] is False
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "raw-route-secret" not in audit_text
    assert "token=***" in audit_text


def test_mods_html_and_audit_do_not_expose_auth_secrets(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    password = "owner secret mods password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    from armactl.web.app import create_app
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mod(path: Path, mod_id: str, name: str = "", version: str = "") -> ModAddResult:
        raise ConfigError("mod save failed token=render-secret")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)
    app = create_app(data_root=tmp_path)
    _patch_management_global(monkeypatch, "load_mods_page", lambda instance: _mods_page())
    client = _client(app)
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    session_token = login_response.cookies.get(SESSION_COOKIE_NAME)
    csrf_token = _mods_csrf_token(client)

    page_response = client.get("/mods", follow_redirects=False)
    action_response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "CCCCCCCCCCCCCCCC",
            "name": "Charlie",
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
