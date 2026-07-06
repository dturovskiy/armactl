"""Controlled web service actions for the default Arma server instance."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from armactl import discovery, paths, service_manager
from armactl.platform.service_adapter import ServiceAdapter, ServiceResult, get_service_adapter
from armactl.redaction import redact_sensitive_text
from armactl.runtime_settings import RuntimeSettingsError, normalize_max_fps_profile
from armactl.state import ServerState
from armactl.web.services import pending_work
from armactl.web.services.audit import AuditLogError, append_audit_event

ACTION_START = "start"
ACTION_STOP = "stop"
ACTION_RESTART = "restart"
ACTION_START_AT_FPS = "start-at-fps"
ACTION_RESTART_AT_FPS = "restart-at-fps"
SERVICE_BACKEND_ACTIONS = frozenset({ACTION_START, ACTION_STOP, ACTION_RESTART})
MAX_FPS_SERVICE_ACTIONS = frozenset({ACTION_START_AT_FPS, ACTION_RESTART_AT_FPS})
SUPPORTED_ACTIONS = SERVICE_BACKEND_ACTIONS | MAX_FPS_SERVICE_ACTIONS
CONFIRMATION_REQUIRED_ACTIONS = frozenset({ACTION_STOP, ACTION_RESTART, ACTION_RESTART_AT_FPS})
RESTART_WORK_CLEARING_ACTIONS = frozenset(
    {ACTION_START, ACTION_RESTART, ACTION_START_AT_FPS, ACTION_RESTART_AT_FPS}
)
MAX_FPS_BACKEND_ACTIONS = {
    ACTION_START_AT_FPS: ACTION_START,
    ACTION_RESTART_AT_FPS: ACTION_RESTART,
}
STOPPING_ACTIVE_STATES = frozenset({"deactivating"})
STOPPING_SUB_STATES = frozenset(
    {
        "stop",
        "stop-sigterm",
        "stop-sigkill",
        "stop-post",
        "final-sigterm",
        "final-sigkill",
    }
)


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
    intent_audited: bool = True
    backend_success: bool | None = None
    backend_message: str = ""
    pending_restart_work_cleared: bool = False
    pending_restart_work_warning: str = ""
    max_fps_profile: int | None = None

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def normalize_service_action(action: str) -> str:
    """Normalize a user-facing action name."""
    return action.strip().lower()


def is_supported_action(action: str) -> bool:
    """Return whether the action is a known service action."""
    return normalize_service_action(action) in SUPPORTED_ACTIONS


def is_max_fps_service_action(action: str) -> bool:
    """Return whether action controls a persisted max FPS profile."""
    return normalize_service_action(action) in MAX_FPS_SERVICE_ACTIONS


def _backend_action_for_request(action: str) -> str:
    normalized = normalize_service_action(action)
    if normalized in MAX_FPS_BACKEND_ACTIONS:
        return MAX_FPS_BACKEND_ACTIONS[normalized]
    return normalized


def requires_confirmation(action: str) -> bool:
    """Return whether the action needs explicit operator confirmation."""
    return normalize_service_action(action) in CONFIRMATION_REQUIRED_ACTIONS


def confirmation_matches(action: str, confirm: str) -> bool:
    """Return whether a submitted confirmation matches the action."""
    normalized = normalize_service_action(action)
    if not requires_confirmation(normalized):
        return True
    return confirm == normalized


def confirmation_message(action: str) -> str:
    """Return a safe validation message for missing confirmations."""
    normalized = normalize_service_action(action)
    backend_action = _backend_action_for_request(normalized)
    return f"Confirmation is required to {backend_action} the server."


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


def _resolve_service_adapter(adapter: ServiceAdapter | None) -> ServiceAdapter:
    return adapter if adapter is not None else get_service_adapter()


def _service_name(instance: str, state: ServerState | None, adapter: ServiceAdapter) -> str:
    if state is not None and state.service_name:
        return state.service_name
    return adapter.service_unit_name(instance)


def _service_status_or_none(
    adapter: ServiceAdapter,
    service_name: str,
) -> dict[str, object] | None:
    status_getter = getattr(adapter, "get_service_status", None)
    if status_getter is None:
        return None
    try:
        status = status_getter(service_name)
    except Exception:  # noqa: BLE001 - status preflight must not hide service controls.
        return None
    return status if isinstance(status, dict) else None


def _service_is_stopping(status: dict[str, object] | None) -> bool:
    if status is None:
        return False
    active_state = str(status.get("active_state") or "").strip().lower()
    sub_state = str(status.get("sub_state") or "").strip().lower()
    return active_state in STOPPING_ACTIVE_STATES or sub_state in STOPPING_SUB_STATES


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


def _run_backend_action(
    adapter: ServiceAdapter,
    action: str,
    service_name: str,
) -> ServiceResult:
    if action == ACTION_START:
        return adapter.start_service(service_name)
    if action == ACTION_STOP:
        return adapter.stop_service(service_name)
    if action == ACTION_RESTART:
        return adapter.restart_service(service_name)
    raise ServiceActionError("Unknown service action.")


def run_service_action(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    adapter: ServiceAdapter | None = None,
) -> ServiceActionResult:
    """Run a controlled service action for one instance without auditing."""
    normalized = normalize_service_action(action)
    if normalized not in SERVICE_BACKEND_ACTIONS:
        raise ServiceActionError("Unknown service action.")
    service_adapter = _resolve_service_adapter(adapter)

    try:
        state = discovery.discover(instance=instance, save=False)
    except Exception:  # noqa: BLE001 - discovery preflight fails closed.
        return _result(
            action=normalized,
            instance=instance,
            service_name=_service_name(instance, None, service_adapter),
            success=False,
            message="Service action is unavailable.",
            exit_code=1,
            performed=False,
        )

    service_name = _service_name(instance, state, service_adapter)
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

    service_status = _service_status_or_none(service_adapter, service_name)
    if _service_is_stopping(service_status):
        return _result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message=(
                "Server is stopping; wait for shutdown to finish before running "
                "another service action."
            ),
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

    backend_result = _run_backend_action(service_adapter, normalized, service_name)

    return _result(
        action=normalized,
        instance=instance,
        service_name=service_name,
        success=backend_result.success,
        message=backend_result.message,
        exit_code=backend_result.exit_code,
        performed=True,
    )


def _max_fps_action_result(
    *,
    action: str,
    instance: str,
    service_name: str,
    success: bool,
    message: str,
    exit_code: int = 0,
    performed: bool,
    max_fps_profile: int | None,
    backend_success: bool | None = None,
    backend_message: str = "",
) -> ServiceActionResult:
    return ServiceActionResult(
        action=action,
        instance=instance,
        service_name=service_name,
        success=success,
        message=_safe_message(message),
        exit_code=exit_code,
        performed=performed,
        backend_success=backend_success,
        backend_message=_safe_message(backend_message) if backend_message else "",
        max_fps_profile=max_fps_profile,
    )


def _max_fps_update_failure(
    action: str,
    instance: str,
    service_name: str,
    max_fps_profile: int | None,
    *,
    exit_code: int = 1,
    backend_message: str = "",
) -> ServiceActionResult:
    return _max_fps_action_result(
        action=action,
        instance=instance,
        service_name=service_name,
        success=False,
        message="Max FPS profile was not applied. Service action was not run.",
        exit_code=exit_code,
        performed=False,
        backend_success=False,
        backend_message=(
            "Max FPS profile update failed." if backend_message else ""
        ),
        max_fps_profile=max_fps_profile,
    )


def run_service_action_with_max_fps(
    action: str,
    max_fps_profile: int | str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    adapter: ServiceAdapter | None = None,
) -> ServiceActionResult:
    """Persist/generate max FPS, then run a controlled start or restart."""
    normalized = normalize_service_action(action)
    if normalized not in MAX_FPS_SERVICE_ACTIONS:
        raise ServiceActionError("Unknown service action.")
    backend_action = _backend_action_for_request(normalized)
    service_adapter = _resolve_service_adapter(adapter)
    try:
        profile = normalize_max_fps_profile(max_fps_profile)
    except RuntimeSettingsError:
        return _max_fps_action_result(
            action=normalized,
            instance=instance,
            service_name=service_adapter.service_unit_name(instance),
            success=False,
            message="Unsupported max FPS profile. Choose 60 or 120.",
            exit_code=1,
            performed=False,
            backend_success=False,
            max_fps_profile=None,
        )

    try:
        state = discovery.discover(instance=instance, save=False)
    except Exception:
        return _max_fps_action_result(
            action=normalized,
            instance=instance,
            service_name=_service_name(instance, None, service_adapter),
            success=False,
            message="Service action is unavailable.",
            exit_code=1,
            performed=False,
            backend_success=False,
            max_fps_profile=profile,
        )

    service_name = _service_name(instance, state, service_adapter)
    if not state.server_installed:
        return _max_fps_action_result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message="No server found.",
            exit_code=1,
            performed=False,
            backend_success=False,
            max_fps_profile=profile,
        )

    if not state.config_exists:
        return _max_fps_action_result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message="Config missing. Run ./armactl repair first.",
            exit_code=1,
            performed=False,
            backend_success=False,
            max_fps_profile=profile,
        )

    service_status = _service_status_or_none(service_adapter, service_name)
    if _service_is_stopping(service_status):
        return _max_fps_action_result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message=(
                "Server is stopping; wait for shutdown to finish before running "
                "another service action."
            ),
            exit_code=1,
            performed=False,
            backend_success=False,
            max_fps_profile=profile,
        )

    if normalized == ACTION_START_AT_FPS and state.server_running:
        return _max_fps_action_result(
            action=normalized,
            instance=instance,
            service_name=service_name,
            success=False,
            message="Server already running; use restart to apply FPS.",
            exit_code=1,
            performed=False,
            backend_success=False,
            max_fps_profile=profile,
        )

    try:
        update_result = service_manager.update_max_fps_profile(instance, profile)
    except Exception:
        return _max_fps_update_failure(
            normalized,
            instance,
            service_name,
            profile,
        )
    if not update_result.success:
        return _max_fps_update_failure(
            normalized,
            instance,
            service_name,
            profile,
            exit_code=update_result.exit_code,
            backend_message=update_result.message,
        )

    backend_result = _run_backend_action(service_adapter, backend_action, service_name)
    if backend_result.success:
        message = f"Max FPS profile set to {profile}. Server {backend_action} completed."
    else:
        message = (
            f"Max FPS profile set to {profile}, but server {backend_action} failed. "
            "The generated launch script is ready; retry the service action after "
            "resolving the service issue."
        )

    return _max_fps_action_result(
        action=normalized,
        instance=instance,
        service_name=service_name,
        success=backend_result.success,
        message=message,
        exit_code=backend_result.exit_code,
        performed=True,
        backend_success=backend_result.success,
        backend_message=(
            "" if backend_result.success else "Backend service action failed."
        ),
        max_fps_profile=profile,
    )


def _append_service_action_intent(
    action,
    *,
    audit_log_path,
    username,
    instance,
    target,
    details=None,
):
    safe_details = {"phase": "intent"}
    if details:
        safe_details.update(details)
    append_audit_event(
        audit_log_path,
        username=username,
        action=action,
        instance=instance,
        target=target,
        success=True,
        message="Service action requested.",
        exit_code=0,
        details=safe_details,
    )


def _service_intent_audit_failure(action, instance, service_name):
    return ServiceActionResult(
        action=action,
        instance=instance,
        service_name=service_name,
        success=False,
        message="Service action was not run because audit logging failed.",
        exit_code=1,
        performed=False,
        audit_written=False,
        intent_audited=False,
        backend_success=False,
    )


def _service_action_outcome_details(result: ServiceActionResult) -> dict[str, object]:
    details: dict[str, object] = {
        "phase": "outcome",
        "performed": "yes" if result.performed else "no",
    }
    if result.max_fps_profile is not None:
        details["max_fps_profile"] = result.max_fps_profile
    return details


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
            details=_service_action_outcome_details(result),
        )
    except AuditLogError:
        return replace(
            result,
            success=False,
            message="Service action completed but audit logging failed.",
            backend_success=result.success,
            backend_message=result.message,
            exit_code=1,
            audit_written=False,
        )
    return result


def _backend_success(result: ServiceActionResult) -> bool:
    return result.success if result.backend_success is None else result.backend_success


def _clear_resolved_restart_pending_work_for_result(
    result: ServiceActionResult,
    *,
    db_path: Path | None,
) -> ServiceActionResult:
    if (
        db_path is None
        or result.action not in RESTART_WORK_CLEARING_ACTIONS
        or not _backend_success(result)
        or not result.performed
    ):
        return result
    clear_result = pending_work.clear_restart_pending_work_safely(
        db_path,
        instance=result.instance,
    )
    return replace(
        result,
        pending_restart_work_cleared=clear_result.total_cleared > 0,
        pending_restart_work_warning=clear_result.warning,
    )


def run_service_action_and_audit(
    action: str,
    *,
    audit_log_path: Path,
    username: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    db_path: Path | None = None,
    adapter: ServiceAdapter | None = None,
) -> ServiceActionResult:
    """Run a service action and append safe audit events."""
    normalized = normalize_service_action(action)
    if normalized not in SERVICE_BACKEND_ACTIONS:
        raise ServiceActionError("Unknown service action.")
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    service_adapter = _resolve_service_adapter(adapter)
    intent_target = service_adapter.service_unit_name(normalized_instance)
    try:
        _append_service_action_intent(
            normalized,
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=intent_target,
        )
    except AuditLogError:
        return _service_intent_audit_failure(
            normalized,
            normalized_instance,
            intent_target,
        )
    result = run_service_action(
        normalized,
        instance=normalized_instance,
        adapter=service_adapter,
    )
    result = audit_service_action_result(
        result,
        audit_log_path=audit_log_path,
        username=username,
    )
    return _clear_resolved_restart_pending_work_for_result(result, db_path=db_path)


def run_service_action_with_max_fps_and_audit(
    action: str,
    max_fps_profile: int | str,
    *,
    audit_log_path: Path,
    username: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    db_path: Path | None = None,
    adapter: ServiceAdapter | None = None,
) -> ServiceActionResult:
    """Persist/generate max FPS, run service action, and append safe audit events."""
    normalized = normalize_service_action(action)
    if normalized not in MAX_FPS_SERVICE_ACTIONS:
        raise ServiceActionError("Unknown service action.")
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    service_adapter = _resolve_service_adapter(adapter)
    intent_target = service_adapter.service_unit_name(normalized_instance)
    try:
        profile = normalize_max_fps_profile(max_fps_profile)
    except RuntimeSettingsError:
        return _max_fps_action_result(
            action=normalized,
            instance=normalized_instance,
            service_name=intent_target,
            success=False,
            message="Unsupported max FPS profile. Choose 60 or 120.",
            exit_code=1,
            performed=False,
            backend_success=False,
            max_fps_profile=None,
        )

    try:
        _append_service_action_intent(
            normalized,
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=intent_target,
            details={"max_fps_profile": profile},
        )
    except AuditLogError:
        return _service_intent_audit_failure(
            normalized,
            normalized_instance,
            intent_target,
        )

    result = run_service_action_with_max_fps(
        normalized,
        profile,
        instance=normalized_instance,
        adapter=service_adapter,
    )
    result = audit_service_action_result(
        result,
        audit_log_path=audit_log_path,
        username=username,
    )
    return _clear_resolved_restart_pending_work_for_result(result, db_path=db_path)
