"""Tests for safe web management of Workshop mods."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import pytest
from starlette.exceptions import StarletteDeprecationWarning
from web_route_helpers import _session_cookie_name

from armactl.addon_cleanup import CleanupResult
from armactl.config_manager import ConfigError
from armactl.mods_manager import BulkModAddResult, ModAddResult, ModUpdateResult
from armactl.state import ServerState
from armactl.web.auth.permissions import MODS_VIEW
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


def _write_instance_mod_config(tmp_path: Path, mods: list[dict[str, str]]) -> Path:
    config_dir = tmp_path / "instance" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return _write_mod_config(config_dir, mods)


def _create_addon_dir(addons_dir: Path, name: str, *, size: int = 512) -> Path:
    addon_dir = addons_dir / name
    addon_dir.mkdir(parents=True, exist_ok=True)
    (addon_dir / "data.bin").write_bytes(b"x" * size)
    return addon_dir


def _patch_mods_page(monkeypatch, page: dict | None = None) -> None:
    from armactl.web.page_models import mods as mods_page_model

    monkeypatch.setattr(
        mods_page_model,
        "load_mods_page",
        lambda instance: page or _mods_page(),
    )


def _authed_client(tmp_path: Path, monkeypatch, page: dict | None = None):
    password = "owner mods password"
    setup_owner_user(tmp_path, "owner", password)
    _patch_mods_page(monkeypatch, page)
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
    events = [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines()]
    return [event for event in events if (event.get('details') or {}).get('phase') != 'intent']


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
        raise ConfigError("backend failed token=raw-mod-secret")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)

    result = mod_actions.run_mod_action(
        mod_actions.ACTION_ADD,
        mod_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )

    assert result.success is False
    assert result.changed is False
    assert result.message == "backend failed token=***"
    assert "raw-mod-secret" not in result.message


def test_mods_unexpected_backend_exception_is_not_rendered_as_action_result(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import mod_actions
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_mod_config(tmp_path, [])
    setup_owner_user(tmp_path, "owner", "owner mods password")
    _patch_mods_page(monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mod(path: Path, mod_id: str, name: str = "", version: str = "") -> ModAddResult:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["game"]["mods"] = [
            {
                "modId": mod_id,
                "name": name,
                "version": version,
            }
        ]
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        raise RuntimeError("unexpected mutation bug token=raw-mod-secret")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)
    client = _client(
        create_app(data_root=tmp_path),
        raise_server_exceptions=False,
    )
    _login(client, "owner", "owner mods password")
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/add",
        data={
            "csrf_token": csrf_token,
            "mod_id": "CCCCCCCCCCCCCCCC",
            "name": "Charlie token=raw-mod-secret",
            "version": "1.2.3",
        },
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Internal Server Error" in response.text
    assert "Mod added." not in response.text
    assert "Mod action is unavailable." not in response.text
    assert "raw-mod-secret" not in response.text
    assert "Traceback" not in response.text
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == [
        {
            "modId": "CCCCCCCCCCCCCCCC",
            "name": "Charlie token=raw-mod-secret",
            "version": "1.2.3",
        }
    ]
    assert list_pending_work(tmp_path / "web" / "web.db") == []
    audit_text = (tmp_path / 'logs' / 'web' / 'audit.log').read_text(encoding='utf-8')
    assert 'raw-mod-secret' not in audit_text


def test_mods_disable_then_enable_back_clears_restart_pending(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions, pending_work

    config_path = _write_mod_config(
        tmp_path,
        [{"modId": "AAAAAAAAAAAAAAAA", "name": "Active Alpha", "version": "1.0"}],
    )
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    db_path = tmp_path / "web" / "web.db"
    audit_log_path = tmp_path / "logs" / "web" / "audit.log"

    disable_result = mod_actions.run_mod_action_and_audit(
        mod_actions.ACTION_DISABLE,
        mod_id="AAAAAAAAAAAAAAAA",
        audit_log_path=audit_log_path,
        username="owner",
        db_path=db_path,
    )

    assert disable_result.success is True
    assert pending_work.get_pending_work(db_path, kind=pending_work.KIND_MODS) is not None

    enable_result = mod_actions.run_mod_action_and_audit(
        mod_actions.ACTION_ENABLE,
        mod_id="AAAAAAAAAAAAAAAA",
        audit_log_path=audit_log_path,
        username="owner",
        db_path=db_path,
    )

    assert enable_result.success is True
    assert pending_work.get_pending_work(db_path, kind=pending_work.KIND_MODS) is None


def test_mods_partial_return_to_baseline_keeps_pending_until_restart(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions, pending_work

    config_path = _write_mod_config(
        tmp_path,
        [
            {"modId": "AAAAAAAAAAAAAAAA", "name": "Active Alpha", "version": "1.0"},
            {"modId": "BBBBBBBBBBBBBBBB", "name": "Active Bravo", "version": "1.0"},
        ],
    )
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    db_path = tmp_path / "web" / "web.db"
    audit_log_path = tmp_path / "logs" / "web" / "audit.log"

    mod_actions.run_mod_action_and_audit(
        mod_actions.ACTION_DISABLE,
        mod_id="AAAAAAAAAAAAAAAA",
        audit_log_path=audit_log_path,
        username="owner",
        db_path=db_path,
    )
    mod_actions.run_mod_action_and_audit(
        mod_actions.ACTION_DISABLE,
        mod_id="BBBBBBBBBBBBBBBB",
        audit_log_path=audit_log_path,
        username="owner",
        db_path=db_path,
    )
    mod_actions.run_mod_action_and_audit(
        mod_actions.ACTION_ENABLE,
        mod_id="AAAAAAAAAAAAAAAA",
        audit_log_path=audit_log_path,
        username="owner",
        db_path=db_path,
    )

    item = pending_work.get_pending_work(db_path, kind=pending_work.KIND_MODS)
    assert item is not None
    assert item.source_action == "mod.enable"
    assert pending_work.clear_restart_pending_work(db_path) == 1
    assert pending_work.list_pending_work(db_path) == []


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
    _patch_mods_page(monkeypatch)
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
    assert event["details"]["mod_label"] == "Charlie (CCCCCCCCCCCCCCCC)"
    from armactl.web.services.pending_work import KIND_MODS, get_pending_work

    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_MODS)
    assert item is not None
    assert item.kind == "mods"
    assert item.source_path == "/mods"
    assert item.source_action == "mod.add"
    assert item.title == "Mod changes"
    assert item.details == "Charlie (CCCCCCCCCCCCCCCC)"


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


def test_mod_service_pending_db_failure_writes_fallback_and_warns(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions, pending_work

    config_path = _write_mod_config(tmp_path, [])
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mod(path: Path, mod_id: str, name: str = "", version: str = "") -> ModAddResult:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["game"]["mods"] = [{"modId": mod_id, "name": name, "version": version}]
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        return ModAddResult(mod_id, "added")

    monkeypatch.setattr(mod_actions.mods_manager, "add_mod_detailed", add_mod)

    def fail_pending_db(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_pending_db)

    result = mod_actions.run_mod_action_and_audit(
        mod_actions.ACTION_ADD,
        instance="default",
        mod_id="cccccccccccccccc",
        name="Charlie token=raw-mod-secret",
        version="1.2.3",
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    assert result.success is True
    assert result.pending_work_warning == pending_work.PENDING_WORK_FALLBACK_WARNING
    assert result.pending_work_error == ""
    item = pending_work.get_fallback_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_MODS,
    )
    assert item is not None
    assert item.is_fallback is True
    assert item.source_action == "mod.add"
    assert item.details == "Charlie token=*** (CCCCCCCCCCCCCCCC)"
    assert "raw-mod-secret" not in pending_work.fallback_pending_work_path(
        tmp_path / "web" / "web.db"
    ).read_text(encoding="utf-8")


def test_mods_remove_pending_work_identifies_config_mod_by_name(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions, pending_work

    config_path = _write_mod_config(
        tmp_path,
        [
            {
                "modId": "AAAAAAAAAAAAAAAA",
                "name": "Where Am I",
                "version": "1.0",
            }
        ],
    )
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    result = mod_actions.run_mod_action_and_audit(
        mod_actions.ACTION_REMOVE,
        instance="default",
        mod_id="AAAAAAAAAAAAAAAA",
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    assert result.success is True
    item = pending_work.get_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_MODS,
    )
    assert item is not None
    assert item.source_action == "mod.remove"
    assert item.details == "Where Am I (AAAAAAAAAAAAAAAA)"


def test_mods_pending_warning_from_service_is_rendered(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions, pending_work

    client = _authed_client(tmp_path, monkeypatch)

    def run_with_pending_warning(*args, **kwargs):
        return mod_actions.ModActionResult(
            action=mod_actions.ACTION_ADD,
            instance="default",
            target="CCCCCCCCCCCCCCCC",
            success=True,
            changed=True,
            message="Mod added.",
            exit_code=0,
            pending_work_warning=pending_work.PENDING_WORK_FALLBACK_WARNING,
        )

    monkeypatch.setattr(mod_actions, "run_mod_action_and_audit", run_with_pending_warning)
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
    assert "raw-mod-secret" not in response.text


def test_mods_audit_failure_after_change_still_marks_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

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
    assert 'Mod action was not run because audit logging failed.' in response.text
    assert 'Restart the server to apply mod changes.' not in response.text
    assert 'raw-mod-secret' not in response.text
    assert json.loads(config_path.read_text())['game']['mods'] == []
    assert list_pending_work(tmp_path / 'web' / 'web.db') == []
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
    assert 'Mod action was not run because audit logging failed.' in response.text
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
    _patch_mods_page(monkeypatch)
    app = create_app(data_root=tmp_path)
    client = _client(app)
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    session_token = login_response.cookies.get(_session_cookie_name(client))
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


def test_mod_action_support_helper_includes_mod_pack_workflows():
    from armactl.web.services import mod_actions

    assert mod_actions.is_supported_action(mod_actions.ACTION_ADD) is True
    assert mod_actions.is_supported_action(mod_actions.ACTION_BULK_ADD) is True
    assert mod_actions.is_supported_action(mod_actions.ACTION_IMPORT) is True
    assert mod_actions.is_supported_action(mod_actions.ACTION_EXPORT) is True
    assert mod_actions.is_supported_action(mod_actions.ACTION_DEDUPE) is True
    assert mod_actions.is_supported_action(mod_actions.ACTION_CLEANUP_CHECK) is True
    assert mod_actions.is_supported_action(mod_actions.ACTION_CLEANUP) is True


def test_bulk_add_helper_parses_multiple_ids_and_calls_add_mods_detailed(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, list[str], str, str]] = []
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_mods(path, mod_ids, *, name="", version="") -> BulkModAddResult:
        ids = list(mod_ids)
        calls.append((path, ids, name, version))
        return BulkModAddResult(
            [
                ModAddResult(ids[0], "added"),
                ModAddResult(ids[1], "unchanged"),
            ],
            active_count=2,
        )

    monkeypatch.setattr(mod_actions.mods_manager, "add_mods_detailed", add_mods)

    result = mod_actions.bulk_add_mods(
        text=(
            "https://reforger.armaplatform.com/workshop/aaaaaaaaaaaaaaaa "
            "and BBBBBBBBBBBBBBBB"
        )
    )

    assert result.success is True
    assert result.changed is True
    assert result.action == "mod.bulk-add"
    assert result.message == (
        "Bulk add complete: added 1, updated 0, reactivated 0, "
        "unchanged 1, duplicate input 0."
    )
    assert calls == [
        (
            config_path,
            ["AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB"],
            "",
            "",
        )
    ]


def test_bulk_add_duplicate_input_reports_summary_without_crash(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = _write_mod_config(tmp_path, [])
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    result = mod_actions.bulk_add_mods(
        text="AAAAAAAAAAAAAAAA\nhttps://example.invalid/AAAAAAAAAAAAAAAA",
    )

    assert result.success is True
    assert result.changed is True
    assert "duplicate input 1" in result.message
    assert result.details["added"] == "1"
    assert result.details["duplicate_input"] == "1"
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "", "version": ""}
    ]


def test_mods_import_append_uses_mods_manager_import_detailed(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = _write_mod_config(tmp_path, [])
    calls: list[tuple[Path, bool, list[dict[str, str]], bool]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def import_mods(path, import_file, append=False):
        import_path = Path(import_file)
        calls.append(
            (
                Path(path),
                append,
                json.loads(import_path.read_text(encoding="utf-8")),
                import_path.is_file(),
            )
        )
        return 2, 1, ModUpdateResult(config_changed=True)

    monkeypatch.setattr(mod_actions.mods_manager, "import_mods_detailed", import_mods)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/import",
        data={"csrf_token": csrf_token, "mode": "append"},
        files={
            "upload": (
                "pack.json",
                json.dumps([
                    {"modId": "AAAAAAAAAAAAAAAA", "name": "Alpha", "version": "1"}
                ]),
                "application/json",
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Import append complete: added 2, skipped 1." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert calls == [
        (
            config_path,
            True,
            [{"modId": "AAAAAAAAAAAAAAAA", "name": "Alpha", "version": "1"}],
            True,
        )
    ]
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.import"
    assert event["details"]["mode"] == "append"
    assert event["details"]["changed"] == "yes"


def test_mods_import_replace_requires_confirmation(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(mod_actions, "run_import_mod_pack_and_audit", AssertionError)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/import",
        data={"csrf_token": csrf_token, "mode": "replace"},
        files={"upload": ("pack.json", "[]", "application/json")},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Confirmation is required to replace the current mod list." in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_mods_export_returns_active_mods_json(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    config_path = _write_mod_config(
        tmp_path,
        [{"modId": "AAAAAAAAAAAAAAAA", "name": "Alpha", "version": "1"}],
    )
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/export",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"].lower()
    assert json.loads(response.content) == [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "Alpha", "version": "1"}
    ]
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.export"
    assert event["details"]["exported_count"] == "1"


def test_mods_dedupe_noop_reports_controlled_noop(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_mod_config(
        tmp_path,
        [{"modId": "AAAAAAAAAAAAAAAA", "name": "Alpha", "version": "1"}],
    )
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/dedupe",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "No duplicate mods found." in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert list_pending_work(tmp_path / "web" / "web.db") == []
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.dedupe"
    assert event["details"]["changed"] == "no"
    assert event["details"]["duplicate_removed_count"] == "0"


def test_mods_dedupe_changed_creates_pending_restart(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions
    from armactl.web.services.pending_work import KIND_MODS, get_pending_work

    config_path = _write_mod_config(
        tmp_path,
        [
            {"modId": "AAAAAAAAAAAAAAAA", "name": "Alpha", "version": "1"},
            {"modId": "aaaaaaaaaaaaaaaa", "name": "Alpha duplicate", "version": "2"},
        ],
    )
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/dedupe",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Removed 1 duplicate mod(s)." in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "Alpha", "version": "1"}
    ]
    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_MODS)
    assert item is not None
    assert item.source_action == "mod.dedupe"
    assert item.details == "1 duplicate mod(s) removed"


def test_mods_page_shows_cleanup_visibility(tmp_path: Path, monkeypatch):
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/mods", follow_redirects=False)

    assert response.status_code == 200
    assert "Maintenance / Cleanup" in response.text
    assert "Check unused addons" in response.text
    assert "Cleanup unused addons" in response.text
    assert "config.json" in response.text
    assert "disabled-mods sidecar" in response.text
    assert "general file deletion" in response.text


def test_mods_cleanup_check_dry_run_does_not_delete(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_instance_mod_config(
        tmp_path,
        [{"modId": "AAAAAAAAAAAAAAAA", "name": "Active Alpha", "version": "1"}],
    )
    addons = config_path.parent / "addons"
    active = _create_addon_dir(addons, "Active_AAAAAAAAAAAAAAAA", size=1024)
    stale = _create_addon_dir(addons, "Stale_BBBBBBBBBBBBBBBB", size=2048)
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/cleanup-check",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Found 1 unused addon entry" in response.text
    assert "cleanup would free 2.00 KB" in response.text
    assert active.exists()
    assert stale.exists()
    assert "Restart the server to apply mod changes." not in response.text
    assert list_pending_work(tmp_path / "web" / "web.db") == []
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_mods_cleanup_confirmed_calls_cleanup_helper_and_audits(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = _write_instance_mod_config(tmp_path, [])
    calls: list[tuple[Path, bool]] = []
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def cleanup(path: Path, *, dry_run: bool = False) -> CleanupResult:
        calls.append((path, dry_run))
        return CleanupResult(
            deleted=[Path("/home/deus/private/Stale_token=raw-route-secret_AAAAAAAAAAAAAAAA")],
            skipped=[Path("/home/deus/private/UnknownFormat")],
            bytes_deleted=2048,
        )

    monkeypatch.setattr(mod_actions, "cleanup_unconfigured_addons", cleanup)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/cleanup",
        data={"csrf_token": csrf_token, "confirm": "cleanup"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Deleted 1 unused addon entry" in response.text
    assert "cleanup deleted count" in response.text
    assert "2.00 KB" in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert "/home/deus" not in response.text
    assert "raw-route-secret" not in response.text
    assert calls == [(config_path, False)]
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "mod.cleanup"
    assert event["target"] == "unused addons"
    assert event["success"] is True
    assert event["details"]["changed"] == "yes"
    assert event["details"]["cleanup_deleted_count"] == "1"
    assert event["details"]["cleanup_skipped_count"] == "1"
    assert event["details"]["cleanup_freed"] == "2.00 KB"
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "/home/deus" not in audit_text
    assert "raw-route-secret" not in audit_text


def test_mods_cleanup_requires_confirmation(tmp_path: Path, monkeypatch):
    from armactl.web.services import mod_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(mod_actions, "run_cleanup_unused_addons_and_audit", AssertionError)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/cleanup",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Confirmation is required to clean up unused addons." in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()


def test_mods_cleanup_intent_audit_failure_aborts_mutation(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

    client = _authed_client(tmp_path, monkeypatch)

    def fail_cleanup(*args, **kwargs):
        raise AssertionError("cleanup should not run")

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full token=raw-audit-secret")

    monkeypatch.setattr(mod_actions, "cleanup_unconfigured_addons", fail_cleanup)
    monkeypatch.setattr(mod_actions, "append_audit_event", fail_audit)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/cleanup",
        data={"csrf_token": csrf_token, "confirm": "cleanup"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Mod action was not run because audit logging failed." in response.text
    assert "raw-audit-secret" not in response.text
    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_mods_cleanup_outcome_audit_failure_reports_controlled_problem(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_instance_mod_config(tmp_path, [])
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def cleanup(path: Path, *, dry_run: bool = False) -> CleanupResult:
        return CleanupResult(
            deleted=[Path("/private/Stale_AAAAAAAAAAAAAAAA")],
            bytes_deleted=1024,
        )

    def fail_outcome(audit_log_path, *, details=None, **kwargs):
        if (details or {}).get("phase") == "outcome":
            raise AuditLogError("disk full token=raw-audit-secret")
        return None

    monkeypatch.setattr(mod_actions, "cleanup_unconfigured_addons", cleanup)
    monkeypatch.setattr(mod_actions, "append_audit_event", fail_outcome)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/cleanup",
        data={"csrf_token": csrf_token, "confirm": "cleanup"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Mod action completed but audit logging failed." in response.text
    assert "Deleted 1 unused addon entry" in response.text
    assert "Restart the server to apply mod changes." not in response.text
    assert "raw-audit-secret" not in response.text
    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_mods_cleanup_success_does_not_request_restart_or_pending_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.pending_work import list_pending_work

    config_path = _write_instance_mod_config(tmp_path, [])
    stale = _create_addon_dir(
        config_path.parent / "addons",
        "Stale_AAAAAAAAAAAAAAAA",
        size=1024,
    )
    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/cleanup",
        data={"csrf_token": csrf_token, "confirm": "cleanup"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Deleted 1 unused addon entry" in response.text
    assert not stale.exists()
    assert "Restart the server to apply mod changes." not in response.text
    assert list_pending_work(tmp_path / "web" / "web.db") == []


def test_mods_cleanup_result_details_are_redacted_and_bounded(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions

    config_path = _write_instance_mod_config(tmp_path, [])
    entries = [
        Path(f"/home/deus/private/Stale{i}_token=raw-secret_AAAAAAAAAAAAAAAA")
        for i in range(12)
    ]
    skipped = [Path("/home/deus/private/Skipped_token=raw-skip-secret")]
    cleanup = CleanupResult(
        deleted=entries,
        skipped=skipped,
        errors=["Failed /home/deus/private token=raw-error-secret"],
        bytes_deleted=4096,
    )
    monkeypatch.setattr(
        mod_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )
    monkeypatch.setattr(
        mod_actions,
        "cleanup_unconfigured_addons",
        lambda path, *, dry_run=False: cleanup,
    )

    result = mod_actions.check_unused_addons()

    assert result.success is False
    assert result.changed is False
    assert result.restart_required is False
    assert len(result.details["cleanup_candidate_summary"]) == 8
    assert result.details["cleanup_candidate_summary_omitted_count"] == "4"
    rendered = json.dumps(result.details)
    assert "/home/deus" not in rendered
    assert "raw-secret" not in rendered
    assert "raw-skip-secret" not in rendered
    assert "raw-error-secret" not in rendered
    assert "cleanup_error_summary" in result.details


def test_mods_bulk_intent_audit_failure_aborts_mutation(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import mod_actions
    from armactl.web.services.audit import AuditLogError
    from armactl.web.services.pending_work import list_pending_work

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
        "/mods/bulk-add",
        data={"csrf_token": csrf_token, "bulk_mods": "AAAAAAAAAAAAAAAA"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Mod action was not run because audit logging failed." in response.text
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == []
    assert list_pending_work(tmp_path / "web" / "web.db") == []
    assert "raw-audit-secret" not in response.text


def test_mods_bulk_outcome_audit_failure_reports_controlled_problem(
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

    def fail_outcome(audit_log_path, *, details=None, **kwargs):
        if (details or {}).get("phase") == "outcome":
            raise AuditLogError("disk full token=raw-audit-secret")
        return None

    monkeypatch.setattr(mod_actions, "append_audit_event", fail_outcome)
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/bulk-add",
        data={"csrf_token": csrf_token, "bulk_mods": "AAAAAAAAAAAAAAAA"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Mod action completed but audit logging failed." in response.text
    assert "Bulk add complete: added 1" in response.text
    assert "Restart the server to apply mod changes." in response.text
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "", "version": ""}
    ]
    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_MODS)
    assert item is not None
    assert item.source_action == "mod.bulk-add"
    assert "raw-audit-secret" not in response.text


@pytest.mark.parametrize(
    ("url", "data", "files"),
    [
        ("/mods/bulk-add", {"bulk_mods": "AAAAAAAAAAAAAAAA"}, None),
        ("/mods/import", {"mode": "append"}, {"upload": ("pack.json", "[]", "application/json")}),
        ("/mods/export", {}, None),
        ("/mods/dedupe", {}, None),
        ("/mods/cleanup-check", {}, None),
        ("/mods/cleanup", {"confirm": "cleanup"}, None),
    ],
)
def test_mod_pack_routes_require_valid_csrf(
    tmp_path: Path,
    monkeypatch,
    url: str,
    data: dict[str, str],
    files,
):
    from armactl.web.services import mod_actions

    client = _authed_client(tmp_path, monkeypatch)
    monkeypatch.setattr(mod_actions, "run_bulk_add_and_audit", AssertionError)
    monkeypatch.setattr(mod_actions, "run_import_mod_pack_and_audit", AssertionError)
    monkeypatch.setattr(mod_actions, "export_mod_pack_and_audit", AssertionError)
    monkeypatch.setattr(mod_actions, "run_dedupe_and_audit", AssertionError)
    monkeypatch.setattr(mod_actions, "check_unused_addons", AssertionError)
    monkeypatch.setattr(mod_actions, "run_cleanup_unused_addons_and_audit", AssertionError)

    response = client.post(
        url,
        data={"csrf_token": "wrong-token", **data},
        files=files,
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_mod_pack_routes_require_manage_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.services import mod_actions

    setup_owner_user(tmp_path, "owner", "owner mods password")
    set_web_owner_permissions({MODS_VIEW})
    app = create_app(data_root=tmp_path)
    _patch_mods_page(monkeypatch)
    monkeypatch.setattr(mod_actions, "run_bulk_add_and_audit", AssertionError)
    monkeypatch.setattr(mod_actions, "check_unused_addons", AssertionError)
    monkeypatch.setattr(mod_actions, "run_cleanup_unused_addons_and_audit", AssertionError)
    client = _client(app)
    _login(client, "owner", "owner mods password")
    csrf_token = _mods_csrf_token(client)

    response = client.post(
        "/mods/bulk-add",
        data={"csrf_token": csrf_token, "bulk_mods": "AAAAAAAAAAAAAAAA"},
        follow_redirects=False,
    )
    check_response = client.post(
        "/mods/cleanup-check",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    cleanup_response = client.post(
        "/mods/cleanup",
        data={"csrf_token": csrf_token, "confirm": "cleanup"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert check_response.status_code == 403
    assert check_response.text == "Permission denied."
    assert cleanup_response.status_code == 403
    assert cleanup_response.text == "Permission denied."
