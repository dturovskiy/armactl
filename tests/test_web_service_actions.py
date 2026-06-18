"""Tests for controlled web service action orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from armactl.service_manager import ServiceResult
from armactl.state import ServerState
from armactl.web.services import service_actions


def _state(
    *,
    installed: bool = True,
    running: bool = False,
    config_exists: bool = True,
) -> ServerState:
    return ServerState(
        server_installed=installed,
        config_exists=config_exists,
        server_running=running,
        service_name="armareforger.service",
    )


def _patch_state(monkeypatch, state: ServerState) -> None:
    monkeypatch.setattr(
        service_actions.discovery,
        "discover",
        lambda instance, save=False: state,
    )


def _patch_managers(monkeypatch, calls: list[tuple[str, str]], result: ServiceResult) -> None:
    def start(service_name: str) -> ServiceResult:
        calls.append(("start", service_name))
        return result

    def stop(service_name: str) -> ServiceResult:
        calls.append(("stop", service_name))
        return result

    def restart(service_name: str) -> ServiceResult:
        calls.append(("restart", service_name))
        return result

    monkeypatch.setattr(service_actions.service_manager, "start_service", start)
    monkeypatch.setattr(service_actions.service_manager, "stop_service", stop)
    monkeypatch.setattr(service_actions.service_manager, "restart_service", restart)


def test_start_service_action_calls_backend(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    _patch_managers(monkeypatch, calls, ServiceResult(True, "started", 0))

    result = service_actions.run_service_action("start")

    assert result.success is True
    assert result.performed is True
    assert result.message == "started"
    assert calls == [("start", "armareforger.service")]


def test_start_already_running_skips_backend(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=True))
    _patch_managers(monkeypatch, calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("start")

    assert result.success is True
    assert result.performed is False
    assert result.message == "Server is already running."
    assert calls == []


def test_stop_already_stopped_skips_backend(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    _patch_managers(monkeypatch, calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("stop")

    assert result.success is True
    assert result.performed is False
    assert result.message == "Server is already stopped."
    assert calls == []


def test_restart_rejects_missing_config(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=True, config_exists=False))
    _patch_managers(monkeypatch, calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("restart")

    assert result.success is False
    assert result.performed is False
    assert "Config missing" in result.message
    assert calls == []


def test_missing_server_is_controlled(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(installed=False, running=False, config_exists=False))
    _patch_managers(monkeypatch, calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("start")

    assert result.success is False
    assert result.performed is False
    assert result.message == "No server found."
    assert calls == []


def test_unknown_action_is_rejected():
    with pytest.raises(service_actions.ServiceActionError):
        service_actions.run_service_action("delete")


def test_service_action_redacts_backend_message(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    _patch_managers(
        monkeypatch,
        calls,
        ServiceResult(False, "failed password=backend-secret token=raw-token", 23),
    )

    result = service_actions.run_service_action("start")

    assert result.success is False
    assert result.exit_code == 23
    assert "backend-secret" not in result.message
    assert "raw-token" not in result.message
    assert "password=***" in result.message
    assert "token=***" in result.message


def test_service_manager_exception_is_not_converted_to_controlled_result(monkeypatch):
    _patch_state(monkeypatch, _state(running=False))

    def start(service_name: str) -> ServiceResult:
        raise RuntimeError("unexpected service bug token=raw-service-secret")

    monkeypatch.setattr(service_actions.service_manager, "start_service", start)

    with pytest.raises(RuntimeError, match="unexpected service bug"):
        service_actions.run_service_action("start")


def test_service_action_audit_writes_safe_jsonl(monkeypatch, tmp_path: Path):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    _patch_managers(monkeypatch, calls, ServiceResult(True, "started token=raw-secret", 0))
    audit_path = tmp_path / "logs" / "web" / "audit.log"

    result = service_actions.run_service_action_and_audit(
        "start",
        audit_log_path=audit_path,
        username="owner",
    )

    events = [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines()]
    event = [item for item in events if (item.get('details') or {}).get('phase') == 'outcome'][0]
    assert result.success is True
    assert event["username"] == "owner"
    assert event["action"] == "start"
    assert event["instance"] == "default"
    assert event["target"] == "armareforger.service"
    assert event["service"] == "armareforger.service"
    assert event["success"] is True
    assert event["exit_code"] == 0
    assert audit_path.stat().st_mode & 0o777 == 0o600
    assert "raw-secret" not in audit_path.read_text(encoding="utf-8")
    assert "token=***" in event["message"]
