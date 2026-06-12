"""Controlled web service actions for the default Arma server instance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from armactl import discovery, paths, service_manager
from armactl.redaction import redact_sensitive_text
from armactl.service_manager import ServiceResult
from armactl.state import ServerState
from armactl.web.services.audit import AuditLogError, append_audit_event

ACTION_START = "start"
ACTION_STOP = "stop"
ACTION_RESTART = "restart"
SUPPORTED_ACTIONS = frozenset({ACTION_START, ACTION_STOP, ACTION_RESTART})
CONFIRMATION_REQUIRED_ACTIONS = frozenset({ACTION_STOP, ACTION_RESTART})


class ServiceActionError(ValueError):
    """Raised when a service action request is invalid."""


@dataclass(frozen=True)
class ServiceActionResult:
    """Safe result for rendering and audit logging."""

    action: str
    instance: str
    service_name: str
    success: bool
    message: str
    exit_code: int
    performed: bool
    audit_written: bool = True

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def normalize_service_action(action: str) -> str:
    """Normalize a user-facing action name."""
    return action.strip().lower()


def is_supported_action(action: str) -> bool:
    """Return whether the action is a known service action."""
    return normalize_service_action(action) in SUPPORTED_ACTIONS


def requires_confirmation(action: str) -> bool:
    """Return whether the action needs explicit operator confirmation."""
    return normalize_service_action(action) in CONFIRMATION_REQUIRED_ACTIONS


def confirmation_message(action: str) -> str:
    """Return a safe validation message for missing confirmations."""
    normalized = normalize_service_action(action)
    return f"Confirmation is required to {normalized} the server."


def confirmation_failure(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ServiceActionResult:
    """Return a controlled failure for missing stop/restart confirmation."""
    normalized = normalize_service_action(action)
    return ServiceActionResult(
        action=normalized,
        instance=instance,
        service_name="",
        success=False,
        message=confirmation_message(normalized),
        exit_code=1,
        performed=False,
        audit_written=False,
    )


def _safe_message(value: object) -> str:
    message = redact_sensitive_text(value).strip()
    return message or "No details available."


def _service_name(instance: str, state: ServerState | None) -> str:
    if state is not None and state.service_name:
        return state.service_name
    return service_manager.service_unit_name(instance)


def _result(
    *,
    action: str,
    instance: str,
    service_name: str,
    success: bool,
    message: str,
    exit_code: int = 0,
    performed: bool,
) -> ServiceActionResult:
    return ServiceActionResult(
        action=action,
        instance=instance,
        service_name=service_name,
        success=success,
        message=_safe_message(message),
        exit_code=exit_code,
        performed=performed,
    )


def _manager_for_action(action: str) -> Callable[[str], ServiceResult]:
    if action == ACTION_START:
        return service_manager.start_service
    if action == ACTION_STOP:
        return service_manager.stop_service
    if action == ACTION_RESTART:
        return service_manager.restart_service
    raise ServiceActionError("Unknown service action.")


def run_service_action(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ServiceActionResult:
    """Run a controlled service action for one instance without auditing."""
    normalized = normalize_service_action(action)
    if normalized not in SUPPORTED_ACTIONS:
        raise ServiceActionError("Unknown service action.")

    try:
        state = discovery.discover(instance=instance, save=False)
    except Exception:
        return _result(
            action=normalized,
            instance=instance,
            service_name=_service_name(instance, None),
            success=False,
            message="Service action is unavailable.",
            exit_code=1,
            performed=False,
        )

    service_name = _service_name(instance, state)
    if not state.server_installed:
        return _result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message="No server found.",
            exit_code=1,
            performed=False,
        )

    if normalized in {ACTION_START, ACTION_RESTART} and not state.config_exists:
        return _result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message="Config missing. Run './armactl repair' first.",
            exit_code=1,
            performed=False,
        )

    if normalized == ACTION_START and state.server_running:
        return _result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=True,
            message="Server is already running.",
            performed=False,
        )

    if normalized == ACTION_STOP and not state.server_running:
        return _result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=True,
            message="Server is already stopped.",
            performed=False,
        )

    manager = _manager_for_action(normalized)
    try:
        backend_result = manager(service_name)
    except Exception:
        return _result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message="Service action is unavailable.",
            exit_code=1,
            performed=True,
        )

    return _result(
        action=normalized,
        instance=instance,
        service_name=service_name,
        success=backend_result.success,
        message=backend_result.message,
        exit_code=backend_result.exit_code,
        performed=True,
    )


def audit_service_action_result(
    result: ServiceActionResult,
    *,
    audit_log_path: Path,
    username: str,
) -> ServiceActionResult:
    """Append an audit event for a service action result."""
    try:
        append_audit_event(
            audit_log_path,
            username=username,
            action=result.action,
            instance=result.instance,
            target=result.service_name,
            success=result.success,
            message=result.message,
            exit_code=result.exit_code,
        )
    except AuditLogError:
        return replace(
            result,
            success=False,
            message="Service action completed but audit logging failed.",
            exit_code=1,
            audit_written=False,
        )
    return result


def run_service_action_and_audit(
    action: str,
    *,
    audit_log_path: Path,
    username: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ServiceActionResult:
    """Run a service action and append a safe audit event."""
    result = run_service_action(action, instance=instance)
    return audit_service_action_result(
        result,
        audit_log_path=audit_log_path,
        username=username,
    )
