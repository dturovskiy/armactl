"""Tests for controlled web service action orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from armactl.platform.service_adapter import ServiceResult
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


class _FakeServiceAdapter:
    def __init__(self, calls: list[tuple[str, str]], result: ServiceResult) -> None:
        self.calls = calls
        self.result = result

    def service_unit_name(self, instance: str = "default") -> str:
        if instance == "default":
            return "armareforger.service"
        return f"armareforger@{instance}.service"

    def start_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("start", service_name))
        return self.result

    def stop_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("stop", service_name))
        return self.result

    def restart_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("restart", service_name))
        return self.result


def _adapter(calls: list[tuple[str, str]], result: ServiceResult) -> _FakeServiceAdapter:
    return _FakeServiceAdapter(calls, result)


def test_start_service_action_calls_backend(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    adapter = _adapter(calls, ServiceResult(True, "started", 0))

    result = service_actions.run_service_action("start", adapter=adapter)

    assert result.success is True
    assert result.performed is True
    assert result.message == "started"
    assert calls == [("start", "armareforger.service")]


def test_start_already_running_skips_backend(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=True))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("start", adapter=adapter)

    assert result.success is True
    assert result.performed is False
    assert result.message == "Server is already running."
    assert calls == []


def test_start_already_running_does_not_clear_restart_pending_work(
    monkeypatch,
    tmp_path: Path,
):
    from armactl.web.services import pending_work

    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=True))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))
    db_path = tmp_path / "web" / "web.db"
    pending_work.mark_restart_pending(
        db_path,
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )

    result = service_actions.run_service_action_and_audit(
        "start",
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=db_path,
        adapter=adapter,
    )

    assert result.success is True
    assert result.performed is False
    assert result.pending_restart_work_cleared is False
    assert len(pending_work.list_pending_work(db_path)) == 1
    assert calls == []


def test_stop_already_stopped_skips_backend(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("stop", adapter=adapter)

    assert result.success is True
    assert result.performed is False
    assert result.message == "Server is already stopped."
    assert calls == []


def test_start_is_blocked_while_service_is_stopping(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))

    class StoppingAdapter(_FakeServiceAdapter):
        def get_service_status(self, service_name: str) -> dict[str, object]:
            return {
                "service_name": service_name,
                "active_state": "deactivating",
                "sub_state": "stop-sigterm",
            }

    adapter = StoppingAdapter(calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("start", adapter=adapter)

    assert result.success is False
    assert result.performed is False
    assert "Server is stopping" in result.message
    assert calls == []


def test_restart_rejects_missing_config(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=True, config_exists=False))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("restart", adapter=adapter)

    assert result.success is False
    assert result.performed is False
    assert "Config missing" in result.message
    assert calls == []


def test_missing_server_is_controlled(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(installed=False, running=False, config_exists=False))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))

    result = service_actions.run_service_action("start", adapter=adapter)

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
    adapter = _adapter(
        calls,
        ServiceResult(False, "failed password=backend-secret token=raw-token", 23),
    )

    result = service_actions.run_service_action("start", adapter=adapter)

    assert result.success is False
    assert result.exit_code == 23
    assert "backend-secret" not in result.message
    assert "raw-token" not in result.message
    assert "password=***" in result.message
    assert "token=***" in result.message


def test_service_adapter_exception_is_not_converted_to_controlled_result(monkeypatch):
    _patch_state(monkeypatch, _state(running=False))

    class FailingAdapter(_FakeServiceAdapter):
        def start_service(self, service_name: str) -> ServiceResult:
            raise RuntimeError("unexpected service bug token=raw-service-secret")

    with pytest.raises(RuntimeError, match="unexpected service bug"):
        service_actions.run_service_action(
            "start",
            adapter=FailingAdapter([], ServiceResult(True, "unused", 0)),
        )


def test_service_action_audit_writes_safe_jsonl(monkeypatch, tmp_path: Path):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    adapter = _adapter(calls, ServiceResult(True, "started token=raw-secret", 0))
    audit_path = tmp_path / "logs" / "web" / "audit.log"

    result = service_actions.run_service_action_and_audit(
        "start",
        audit_log_path=audit_path,
        username="owner",
        adapter=adapter,
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


def test_start_service_action_clears_restart_pending_work(monkeypatch, tmp_path: Path):
    from armactl.web.services import pending_work

    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=False))
    adapter = _adapter(calls, ServiceResult(True, "started", 0))
    db_path = tmp_path / "web" / "web.db"
    pending_work.mark_restart_pending(
        db_path,
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )

    result = service_actions.run_service_action_and_audit(
        "start",
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=db_path,
        adapter=adapter,
    )

    assert result.success is True
    assert result.pending_restart_work_cleared is True
    assert pending_work.list_pending_work(db_path) == []
    assert calls == [("start", "armareforger.service")]


def test_restart_service_action_clears_restart_pending_work(monkeypatch, tmp_path: Path):
    from armactl.web.services import pending_work

    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=True))
    adapter = _adapter(calls, ServiceResult(True, "restarted", 0))
    db_path = tmp_path / "web" / "web.db"
    pending_work.mark_restart_pending(
        db_path,
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )

    result = service_actions.run_service_action_and_audit(
        "restart",
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=db_path,
        adapter=adapter,
    )

    assert result.success is True
    assert result.pending_restart_work_cleared is True
    assert pending_work.list_pending_work(db_path) == []
    assert calls == [("restart", "armareforger.service")]


def test_start_at_fps_rejects_running_without_mutating(monkeypatch):
    calls: list[tuple[str, str]] = []
    update_calls: list[tuple[str, int]] = []
    _patch_state(monkeypatch, _state(running=True))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))

    def update_profile(instance: str, max_fps: int) -> ServiceResult:
        update_calls.append((instance, max_fps))
        return ServiceResult(True, "updated", 0)

    monkeypatch.setattr(service_actions.service_manager, "update_max_fps_profile", update_profile)

    result = service_actions.run_service_action_with_max_fps(
        "start-at-fps",
        120,
        adapter=adapter,
    )

    assert result.success is False
    assert result.performed is False
    assert result.message == "Server already running; use restart to apply FPS."
    assert result.max_fps_profile == 120
    assert update_calls == []
    assert calls == []


def test_start_at_fps_intent_audit_failure_does_not_mutate(monkeypatch, tmp_path: Path):
    calls: list[tuple[str, str]] = []
    update_calls: list[tuple[str, int]] = []
    _patch_state(monkeypatch, _state(running=False))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))

    def fail_audit(*args, **kwargs):
        raise service_actions.AuditLogError("audit unavailable")

    def update_profile(instance: str, max_fps: int) -> ServiceResult:
        update_calls.append((instance, max_fps))
        return ServiceResult(True, "updated", 0)

    monkeypatch.setattr(service_actions, "append_audit_event", fail_audit)
    monkeypatch.setattr(service_actions.service_manager, "update_max_fps_profile", update_profile)

    result = service_actions.run_service_action_with_max_fps_and_audit(
        "start-at-fps",
        120,
        audit_log_path=tmp_path / "audit.log",
        username="owner",
        adapter=adapter,
    )

    assert result.success is False
    assert result.intent_audited is False
    assert result.performed is False
    assert update_calls == []
    assert calls == []


def test_restart_at_fps_does_not_restart_if_profile_update_fails(monkeypatch):
    calls: list[tuple[str, str]] = []
    _patch_state(monkeypatch, _state(running=True))
    adapter = _adapter(calls, ServiceResult(True, "should not run", 0))

    def fail_update(instance: str, max_fps: int) -> ServiceResult:
        return ServiceResult(False, "/srv/armactl-data/default token=raw-secret", 9)

    monkeypatch.setattr(service_actions.service_manager, "update_max_fps_profile", fail_update)

    result = service_actions.run_service_action_with_max_fps(
        "restart-at-fps",
        120,
        adapter=adapter,
    )

    assert result.success is False
    assert result.performed is False
    assert result.exit_code == 9
    assert result.backend_message == "Max FPS profile update failed."
    assert "/srv/armactl-data" not in result.message
    assert "raw-secret" not in result.message
    assert calls == []


def test_restart_at_120_updates_profile_before_restart(monkeypatch):
    order: list[str] = []
    _patch_state(monkeypatch, _state(running=True))

    def update_profile(instance: str, max_fps: int) -> ServiceResult:
        order.append(f"update:{max_fps}")
        return ServiceResult(True, "updated", 0)

    class OrderedAdapter(_FakeServiceAdapter):
        def restart_service(self, service_name: str) -> ServiceResult:
            order.append(f"restart:{service_name}")
            return self.result

    monkeypatch.setattr(service_actions.service_manager, "update_max_fps_profile", update_profile)
    adapter = OrderedAdapter([], ServiceResult(True, "restarted", 0))

    result = service_actions.run_service_action_with_max_fps(
        "restart-at-fps",
        120,
        adapter=adapter,
    )

    assert result.success is True
    assert result.performed is True
    assert order == ["update:120", "restart:armareforger.service"]


def test_restart_at_fps_output_and_audit_hide_raw_paths_and_secrets(
    monkeypatch,
    tmp_path: Path,
):
    _patch_state(monkeypatch, _state(running=True))

    def update_profile(instance: str, max_fps: int) -> ServiceResult:
        return ServiceResult(True, "updated /srv/armactl-data/default", 0)

    monkeypatch.setattr(service_actions.service_manager, "update_max_fps_profile", update_profile)
    adapter = _adapter(
        [],
        ServiceResult(False, "failed /srv/armactl-data/default password=backend-secret", 7),
    )
    audit_path = tmp_path / "logs" / "web" / "audit.log"

    result = service_actions.run_service_action_with_max_fps_and_audit(
        "restart-at-fps",
        120,
        audit_log_path=audit_path,
        username="owner",
        adapter=adapter,
    )

    audit_text = audit_path.read_text(encoding="utf-8")
    assert result.success is False
    assert result.message == (
        "Max FPS profile set to 120, but server restart failed. "
        "The generated launch script is ready; retry the service action after "
        "resolving the service issue."
    )
    assert result.backend_message == "Backend service action failed."
    assert "/srv/armactl-data" not in result.message
    assert "backend-secret" not in result.message
    assert "/srv/armactl-data" not in audit_text
    assert "backend-secret" not in audit_text
    events = [json.loads(line) for line in audit_text.splitlines()]
    assert {event["details"]["phase"] for event in events} == {"intent", "outcome"}
    assert all(event["details"].get("max_fps_profile") == "120" for event in events)
