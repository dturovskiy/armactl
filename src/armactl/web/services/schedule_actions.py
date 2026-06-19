"""Controlled web actions for the Arma restart timer."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from armactl import discovery, paths, service_manager
from armactl.redaction import redact_sensitive_text
from armactl.service_manager import ServiceResult
from armactl.state import ServerState
from armactl.web.services import pending_work
from armactl.web.services.audit import AuditLogError, append_audit_event

ACTION_SET_SCHEDULE = "schedule.set"
ACTION_ENABLE_TIMER = "schedule.enable"
ACTION_DISABLE_TIMER = "schedule.disable"
ACTION_RESTART_NOW = "schedule.restart-now"
ACTION_ENABLE_GAME_AUTOSTART = "service.autostart-enable"
ACTION_DISABLE_GAME_AUTOSTART = "service.autostart-disable"
MAX_WEB_RESTART_TIMES = 3
WEB_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
SUPPORTED_ACTIONS = frozenset(
    {
        ACTION_SET_SCHEDULE,
        ACTION_ENABLE_TIMER,
        ACTION_DISABLE_TIMER,
        ACTION_RESTART_NOW,
        ACTION_ENABLE_GAME_AUTOSTART,
        ACTION_DISABLE_GAME_AUTOSTART,
    }
)
CONFIRMATION_REQUIRED_ACTIONS = frozenset(
    {
        ACTION_RESTART_NOW,
        ACTION_DISABLE_GAME_AUTOSTART,
    }
)


class ScheduleActionError(ValueError):
    """Raised when a schedule action request is invalid."""


@dataclass(frozen=True)
class ScheduleActionResult:
    """Safe result for rendering and audit logging."""

    action: str
    instance: str
    target: str
    success: bool
    message: str
    exit_code: int
    performed: bool
    schedule: str
    schedule_entries: list[str]
    audit_written: bool = True
    intent_audited: bool = True
    backend_success: bool | None = None
    backend_message: str = ""
    pending_restart_work_warning: str = ""

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def normalize_schedule_action(action: str) -> str:
    """Normalize a user-facing schedule action."""
    return action.strip().lower()


def is_supported_action(action: str) -> bool:
    """Return whether the action is a known schedule action."""
    return normalize_schedule_action(action) in SUPPORTED_ACTIONS


def requires_confirmation(action: str) -> bool:
    """Return whether the action needs explicit operator confirmation."""
    return normalize_schedule_action(action) in CONFIRMATION_REQUIRED_ACTIONS


def confirmation_message(action: str) -> str:
    """Return a safe validation message for missing confirmations."""
    normalized = normalize_schedule_action(action)
    if normalized == ACTION_RESTART_NOW:
        return "Confirmation is required to restart the server now."
    if normalized == ACTION_DISABLE_GAME_AUTOSTART:
        return "Confirmation is required to disable game server autostart."
    return "Confirmation is required for this schedule action."


def confirmation_failure(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ScheduleActionResult:
    """Return a controlled failure for missing schedule confirmations."""
    normalized = normalize_schedule_action(action)
    return ScheduleActionResult(
        action=normalized,
        instance=instance,
        target="",
        success=False,
        message=confirmation_message(normalized),
        exit_code=1,
        performed=False,
        schedule="",
        schedule_entries=[],
        audit_written=False,
    )


def _safe_message(value: object) -> str:
    message = redact_sensitive_text(value).strip()
    return message or "No details available."


def _safe_schedule_string(value: object) -> str:
    return redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()


def _normalize_web_schedule_entries(value: str) -> list[str]:
    """Normalize simple web schedule input into daily OnCalendar entries."""
    raw_entries = [entry.strip() for entry in re.split(r"[,;\s]+", value) if entry.strip()]
    if len(raw_entries) > MAX_WEB_RESTART_TIMES:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for entry in raw_entries:
        if not WEB_TIME_RE.fullmatch(entry):
            return []
        parts = entry.split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        if hour > 23 or minute > 59:
            return []
        normalized_entry = f"*-*-* {hour:02d}:{minute:02d}:00"
        if normalized_entry in seen:
            continue
        seen.add(normalized_entry)
        normalized.append(normalized_entry)
    return normalized


def _timer_name(instance: str, state: ServerState | None) -> str:
    if state is not None and state.timer_name:
        return state.timer_name
    return service_manager.timer_unit_name(instance)


def _restart_service_name(instance: str) -> str:
    return service_manager.restart_service_unit_name(instance)


def _service_name(instance: str, state: ServerState | None) -> str:
    if state is not None and state.service_name:
        return state.service_name
    return service_manager.service_unit_name(instance)


def _result(
    *,
    action: str,
    instance: str,
    target: str,
    success: bool,
    message: str,
    exit_code: int = 0,
    performed: bool,
    schedule: str = "",
    schedule_entries: list[str] | None = None,
) -> ScheduleActionResult:
    return ScheduleActionResult(
        action=action,
        instance=instance,
        target=target,
        success=success,
        message=_safe_message(message),
        exit_code=exit_code,
        performed=performed,
        schedule=_safe_schedule_string(schedule),
        schedule_entries=[_safe_schedule_string(entry) for entry in schedule_entries or []],
    )


def _discover(instance: str) -> ServerState | None:
    try:
        return discovery.discover(instance=instance, save=False)
    except Exception:  # noqa: BLE001 - discovery preflight fails closed.
        return None


def _failure_from_missing_state(
    action: str,
    instance: str,
    state: ServerState | None,
) -> ScheduleActionResult | None:
    if state is None:
        return _result(
            action=action,
            instance=instance,
            target="",
            success=False,
            message="Schedule action is unavailable.",
            exit_code=1,
            performed=False,
        )
    if action == ACTION_SET_SCHEDULE and not (state.server_installed or state.timer_exists):
        return _result(
            action=action,
            instance=instance,
            target=_timer_name(instance, state),
            success=False,
            message="Restart timer is not installed.",
            exit_code=1,
            performed=False,
        )
    if action in {ACTION_ENABLE_TIMER, ACTION_DISABLE_TIMER} and not state.timer_exists:
        return _result(
            action=action,
            instance=instance,
            target=_timer_name(instance, state),
            success=False,
            message="Restart timer is not installed.",
            exit_code=1,
            performed=False,
        )
    if action in {ACTION_ENABLE_GAME_AUTOSTART, ACTION_DISABLE_GAME_AUTOSTART}:
        if not state.service_exists:
            return _result(
                action=action,
                instance=instance,
                target=_service_name(instance, state),
                success=False,
                message="Server service is not installed.",
                exit_code=1,
                performed=False,
            )
    if action == ACTION_RESTART_NOW:
        if not state.server_installed:
            return _result(
                action=action,
                instance=instance,
                target=_restart_service_name(instance),
                success=False,
                message="No server found.",
                exit_code=1,
                performed=False,
            )
        if not state.config_exists:
            return _result(
                action=action,
                instance=instance,
                target=_restart_service_name(instance),
                success=False,
                message="Config missing. Run './armactl repair' first.",
                exit_code=1,
                performed=False,
            )
    return None


def _result_from_service_result(
    action: str,
    instance: str,
    target: str,
    result: ServiceResult,
    *,
    performed: bool = True,
    schedule: str = "",
    schedule_entries: list[str] | None = None,
) -> ScheduleActionResult:
    return _result(
        action=action,
        instance=instance,
        target=target,
        success=result.success,
        message=result.message,
        exit_code=result.exit_code,
        performed=performed,
        schedule=schedule,
        schedule_entries=schedule_entries,
    )


def _result_from_many(
    action: str,
    instance: str,
    target: str,
    results: list[ServiceResult],
    *,
    success_message: str,
    schedule: str,
    schedule_entries: list[str],
) -> ScheduleActionResult:
    if not results:
        return _result(
            action=action,
            instance=instance,
            target=target,
            success=False,
            message="Schedule action is unavailable.",
            exit_code=1,
            performed=False,
            schedule=schedule,
            schedule_entries=schedule_entries,
        )

    failure = next((item for item in results if not item.success), None)
    if failure is not None:
        return _result_from_service_result(
            action,
            instance,
            target,
            failure,
            schedule=schedule,
            schedule_entries=schedule_entries,
        )

    return _result(
        action=action,
        instance=instance,
        target=target,
        success=True,
        message=success_message,
        exit_code=0,
        performed=True,
        schedule=schedule,
        schedule_entries=schedule_entries,
    )


def run_schedule_action(
    action: str,
    *,
    schedule_value: str = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ScheduleActionResult:
    """Run a controlled restart timer action without auditing."""
    normalized = normalize_schedule_action(action)
    if normalized not in SUPPORTED_ACTIONS:
        raise ScheduleActionError("Unknown schedule action.")

    state = _discover(instance)
    missing = _failure_from_missing_state(normalized, instance, state)
    if missing is not None:
        return missing
    assert state is not None

    timer_name = _timer_name(instance, state)
    if normalized == ACTION_SET_SCHEDULE:
        schedule_entries = _normalize_web_schedule_entries(schedule_value)
        schedule = service_manager.format_schedule_for_input(schedule_entries)
        if not schedule_entries:
            return _result(
                action=normalized,
                instance=instance,
                target=timer_name,
                success=False,
                message="Use one to three restart times such as 05:00, 13:30.",
                exit_code=1,
                performed=False,
                schedule=schedule_value,
            )
        results = service_manager.update_restart_timer_schedule(
            instance=instance,
            on_calendar=schedule_entries,
        )
        return _result_from_many(
            normalized,
            instance,
            timer_name,
            results,
            success_message="Restart schedule updated.",
            schedule=schedule,
            schedule_entries=schedule_entries,
        )

    if normalized == ACTION_ENABLE_TIMER:
        return _result_from_service_result(
            normalized,
            instance,
            timer_name,
            service_manager.enable_service(timer_name),
        )
    if normalized == ACTION_DISABLE_TIMER:
        return _result_from_service_result(
            normalized,
            instance,
            timer_name,
            service_manager.disable_service(timer_name),
        )
    if normalized == ACTION_RESTART_NOW:
        restart_service_name = _restart_service_name(instance)
        return _result_from_service_result(
            normalized,
            instance,
            restart_service_name,
            service_manager.start_service(restart_service_name),
        )
    if normalized == ACTION_ENABLE_GAME_AUTOSTART:
        service_name = _service_name(instance, state)
        return _result_from_service_result(
            normalized,
            instance,
            service_name,
            service_manager.enable_service(service_name),
        )
    if normalized == ACTION_DISABLE_GAME_AUTOSTART:
        service_name = _service_name(instance, state)
        return _result_from_service_result(
            normalized,
            instance,
            service_name,
            service_manager.disable_service(service_name),
        )
    raise ScheduleActionError("Unknown schedule action.")


def _append_schedule_action_intent(action, *, audit_log_path, username, instance, target):
    append_audit_event(
        audit_log_path,
        username=username,
        action=action,
        instance=instance,
        target=target,
        success=True,
        message="Schedule action requested.",
        exit_code=0,
        details={"phase": "intent"},
    )


def _schedule_intent_audit_failure(action, instance, target, schedule_value):
    return ScheduleActionResult(
        action=action,
        instance=instance,
        target=target,
        success=False,
        message="Schedule action was not run because audit logging failed.",
        exit_code=1,
        performed=False,
        schedule=_safe_schedule_string(schedule_value),
        schedule_entries=[],
        audit_written=False,
        intent_audited=False,
        backend_success=False,
    )


def audit_schedule_action_result(
    result: ScheduleActionResult,
    *,
    audit_log_path: Path,
    username: str,
) -> ScheduleActionResult:
    """Append an audit event for a restart timer action result."""
    try:
        append_audit_event(
            audit_log_path,
            username=username,
            action=result.action,
            instance=result.instance,
            target=result.target,
            success=result.success,
            message=result.message,
            exit_code=result.exit_code,
            details={
                "phase": "outcome",
                "schedule": result.schedule,
                "schedule_entries": result.schedule_entries,
                "performed": "yes" if result.performed else "no",
            },
        )
    except AuditLogError:
        return replace(
            result,
            success=False,
            message="Schedule action completed but audit logging failed.",
            exit_code=1,
            backend_success=result.success,
            backend_message=result.message,
            audit_written=False,
        )
    return result


def _backend_success(result: ScheduleActionResult) -> bool:
    return result.success if result.backend_success is None else result.backend_success


def _clear_restart_pending_work_for_result(
    result: ScheduleActionResult,
    *,
    db_path: Path | None,
) -> ScheduleActionResult:
    if (
        db_path is None
        or result.action != ACTION_RESTART_NOW
        or not _backend_success(result)
        or not result.performed
    ):
        return result
    clear_result = pending_work.clear_restart_pending_work_safely(
        db_path,
        instance=result.instance,
    )
    return replace(result, pending_restart_work_warning=clear_result.warning)


def run_schedule_action_and_audit(
    action: str,
    *,
    schedule_value: str = "",
    audit_log_path: Path,
    username: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    db_path: Path | None = None,
) -> ScheduleActionResult:
    """Run a restart timer action and append safe audit events."""
    normalized = normalize_schedule_action(action)
    if normalized not in SUPPORTED_ACTIONS:
        raise ScheduleActionError("Unknown schedule action.")
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    intent_target = service_manager.timer_unit_name(normalized_instance)
    if normalized == ACTION_RESTART_NOW:
        intent_target = _restart_service_name(normalized_instance)
    elif normalized == ACTION_ENABLE_GAME_AUTOSTART:
        intent_target = service_manager.service_unit_name(normalized_instance)
    elif normalized == ACTION_DISABLE_GAME_AUTOSTART:
        intent_target = service_manager.service_unit_name(normalized_instance)
    try:
        _append_schedule_action_intent(
            normalized,
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=intent_target,
        )
    except AuditLogError:
        return _schedule_intent_audit_failure(
            normalized,
            normalized_instance,
            intent_target,
            schedule_value,
        )
    result = run_schedule_action(
        normalized,
        schedule_value=schedule_value,
        instance=normalized_instance,
    )
    result = audit_schedule_action_result(
        result,
        audit_log_path=audit_log_path,
        username=username,
    )
    return _clear_restart_pending_work_for_result(result, db_path=db_path)
