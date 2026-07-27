"""Controlled web actions for Arma game admins."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from armactl import admin_acl_sync, admins_manager, discovery, paths
from armactl.config_manager import ConfigError
from armactl.redaction import redact_sensitive_text
from armactl.web.services import mutation_recovery, pending_work
from armactl.web.services.audit import AuditLogError, append_audit_event

ACTION_ADD = "admin.add"
ACTION_UPDATE = "admin.update"
ACTION_REMOVE = "admin.remove"
SUPPORTED_ACTIONS = frozenset({ACTION_ADD, ACTION_REMOVE})
MAX_ADMIN_REFERENCE_LENGTH = 256
MAX_ADMIN_LABEL_LENGTH = 128


class AdminActionError(ValueError):
    """Raised when an admin action request is invalid."""


@dataclass(frozen=True)
class AdminActionResult:
    """Safe result for rendering and audit logging."""

    action: str
    instance: str
    target: str
    success: bool
    changed: bool
    message: str
    exit_code: int
    audit_written: bool = True
    intent_audited: bool = True
    backend_success: bool | None = None
    backend_message: str = ""
    pending_work_warning: str = ""
    pending_work_error: str = ""

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def normalize_admin_action(action: str) -> str:
    """Normalize a user-facing admin action name."""
    return action.strip().lower()


def is_supported_action(action: str) -> bool:
    """Return whether the action is a known admin action."""
    return normalize_admin_action(action) in SUPPORTED_ACTIONS


def confirmation_failure(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    target: str = "",
) -> AdminActionResult:
    """Return a controlled failure for missing remove confirmation."""
    return AdminActionResult(
        action=ACTION_REMOVE,
        instance=instance,
        target=_safe_text(target),
        success=False,
        changed=False,
        message="Confirmation is required to remove this admin.",
        exit_code=1,
        audit_written=False,
    )


def _safe_text(value: object, *, max_length: int = 500) -> str:
    redacted = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(redacted) > max_length:
        return f"{redacted[:max_length]}..."
    return redacted


def _validated_text(
    value: Any,
    *,
    required: bool,
    max_length: int,
    message: str,
) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise AdminActionError(message)
    if len(text) > max_length or any(ord(char) < 32 for char in text):
        raise AdminActionError(message)
    return text


def _admin_reference(value: Any) -> str:
    return _validated_text(
        value,
        required=True,
        max_length=MAX_ADMIN_REFERENCE_LENGTH,
        message="Admin reference is required.",
    )


def _admin_label(value: Any) -> str:
    return _validated_text(
        value,
        required=False,
        max_length=MAX_ADMIN_LABEL_LENGTH,
        message="Admin label is invalid.",
    )


def _fingerprint_admin_entry(admin: object) -> dict[str, str]:
    if not isinstance(admin, dict):
        return {"identityId": _safe_text(admin, max_length=128), "name": "", "source": ""}
    return {
        "identityId": _safe_text(admin.get("identityId"), max_length=128).upper(),
        "name": _safe_text(admin.get("name"), max_length=128),
        "source": _safe_text(admin.get("source"), max_length=128),
    }


def _admin_restart_fingerprint(config_path: Path) -> str:
    admins = [_fingerprint_admin_entry(admin) for admin in admins_manager.get_admins(config_path)]
    admins.sort(key=lambda item: (item["identityId"], item["name"], item["source"]))
    return pending_work.safe_state_fingerprint({"admins": admins})


def _safe_admin_restart_fingerprint(config_path: Path) -> str:
    try:
        return _admin_restart_fingerprint(config_path)
    except Exception:  # noqa: BLE001 - pending tracking falls back to action-only.
        return ""


def _config_path(instance: str) -> Path:
    try:
        state = discovery.discover(instance=instance, save=False)
    except Exception as exc:  # noqa: BLE001 - discovery preflight fails closed.
        raise AdminActionError("Server config path is unavailable.") from exc
    if not state.config_path:
        raise AdminActionError("Server config path is unavailable.")
    return Path(state.config_path)


def _result(
    *,
    action: str,
    instance: str,
    target: str,
    success: bool,
    changed: bool,
    message: str,
    exit_code: int,
) -> AdminActionResult:
    return AdminActionResult(
        action=action,
        instance=instance,
        target=_safe_text(target),
        success=success,
        changed=changed,
        message=_safe_text(message),
        exit_code=exit_code,
    )


def _failure(
    *,
    action: str,
    instance: str,
    target: str,
    message: object,
) -> AdminActionResult:
    return _result(
        action=action,
        instance=instance,
        target=target,
        success=False,
        changed=False,
        message=_safe_text(message),
        exit_code=1,
    )


def add_or_update_admin(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    admin_reference: str,
    label: str = "",
) -> AdminActionResult:
    """Add or update one game admin through admins_manager."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        reference = _admin_reference(admin_reference)
        name = _admin_label(label)
        config_path = _config_path(normalized_instance)
        mutation = admin_acl_sync.add_admin_and_sync(
            config_path, reference, name
        )
        created = bool(mutation.created)
    except AdminActionError as error:
        return _failure(
            action=ACTION_ADD,
            instance=normalized_instance,
            target=admin_reference,
            message=error,
        )
    except ConfigError as error:
        return _failure(
            action=ACTION_ADD,
            instance=normalized_instance,
            target=reference,
            message=error,
        )

    action = ACTION_ADD if created else ACTION_UPDATE
    return _result(
        action=action,
        instance=normalized_instance,
        target=reference,
        success=True,
        changed=True,
        message="Admin added." if created else "Admin updated.",
        exit_code=0,
    )


def remove_admin(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    admin_reference: str,
) -> AdminActionResult:
    """Remove one game admin through admins_manager."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        reference = _admin_reference(admin_reference)
        config_path = _config_path(normalized_instance)
        mutation = admin_acl_sync.remove_admin_and_sync(config_path, reference)
        removed = mutation.changed
    except AdminActionError as error:
        return _failure(
            action=ACTION_REMOVE,
            instance=normalized_instance,
            target=admin_reference,
            message=error,
        )
    except ConfigError as error:
        return _failure(
            action=ACTION_REMOVE,
            instance=normalized_instance,
            target=reference,
            message=error,
        )

    if not removed:
        return _result(
            action=ACTION_REMOVE,
            instance=normalized_instance,
            target=reference,
            success=True,
            changed=False,
            message="Admin was unchanged.",
            exit_code=0,
        )
    return _result(
        action=ACTION_REMOVE,
        instance=normalized_instance,
        target=reference,
        success=True,
        changed=True,
        message="Admin removed.",
        exit_code=0,
    )


def run_admin_action(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    admin_reference: str = "",
    label: str = "",
) -> AdminActionResult:
    """Run a controlled game-admin action without auditing."""
    normalized = normalize_admin_action(action)
    if normalized == ACTION_ADD:
        return add_or_update_admin(
            instance=instance,
            admin_reference=admin_reference,
            label=label,
        )
    if normalized == ACTION_REMOVE:
        return remove_admin(instance=instance, admin_reference=admin_reference)
    raise AdminActionError("Unknown admin action.")


def _append_admin_action_intent(action, *, audit_log_path, username, instance, target):
    append_audit_event(
        audit_log_path,
        username=username,
        action=action,
        instance=instance,
        target=_safe_text(target),
        success=True,
        message="Admin action requested.",
        exit_code=0,
        details={"phase": "intent"},
    )


def _admin_intent_audit_failure(action, instance, target):
    return AdminActionResult(
        action=action,
        instance=instance,
        target=_safe_text(target),
        success=False,
        changed=False,
        message="Admin action was not run because audit logging failed.",
        exit_code=1,
        audit_written=False,
        intent_audited=False,
        backend_success=False,
    )


def _pending_admin_details(result: AdminActionResult, label: str = "") -> str:
    target = _safe_text(result.target, max_length=MAX_ADMIN_REFERENCE_LENGTH)
    safe_label = _safe_text(label, max_length=MAX_ADMIN_LABEL_LENGTH)
    if safe_label and result.action in {ACTION_ADD, ACTION_UPDATE}:
        return f"{target}; label={safe_label}"
    return target


def audit_admin_action_result(
    result: AdminActionResult,
    *,
    audit_log_path: Path,
    username: str,
) -> AdminActionResult:
    """Append an audit event for a game-admin action result."""
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
            details={"phase": "outcome", "changed": "yes" if result.changed else "no"},
        )
    except Exception:  # noqa: BLE001 - outcome audit runs after backend mutation.
        return replace(
            result,
            success=False,
            message="Admin action completed but audit logging failed.",
            exit_code=1,
            backend_success=result.success,
            backend_message=result.message,
            audit_written=False,
        )
    return result


def _mark_restart_pending_for_admin_result(
    result: AdminActionResult,
    *,
    db_path: Path | None,
    username: str,
    baseline_fingerprint: str = "",
    label: str = "",
) -> AdminActionResult:
    if db_path is None or not result.changed:
        return result
    current_fingerprint = ""
    if baseline_fingerprint:
        try:
            current_fingerprint = _safe_admin_restart_fingerprint(
                _config_path(result.instance)
            )
        except AdminActionError:
            current_fingerprint = ""
    write_result = mutation_recovery.mark_restart_pending_for_mutation(
        mutation_recovery.RestartPendingRecovery(
            db_path=db_path,
            instance=result.instance,
            kind=pending_work.KIND_ADMINS,
            source_action=result.action,
            username=username,
            details=_pending_admin_details(result, label),
            baseline_fingerprint=baseline_fingerprint,
            current_fingerprint=current_fingerprint,
        )
    )
    return replace(
        result,
        pending_work_warning=write_result.warning,
        pending_work_error=write_result.error,
    )


def run_admin_action_and_audit(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    admin_reference: str = "",
    label: str = "",
    audit_log_path: Path,
    username: str,
    db_path: Path | None = None,
) -> AdminActionResult:
    """Run a game-admin action and append safe audit events."""
    normalized = normalize_admin_action(action)
    if normalized not in SUPPORTED_ACTIONS:
        raise AdminActionError("Unknown admin action.")
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        _append_admin_action_intent(
            normalized,
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=admin_reference,
        )
    except AuditLogError:
        return _admin_intent_audit_failure(normalized, normalized_instance, admin_reference)
    baseline_fingerprint = ""
    if db_path is not None:
        try:
            baseline_fingerprint = _admin_restart_fingerprint(_config_path(normalized_instance))
        except (AdminActionError, ConfigError):
            baseline_fingerprint = ""
    result = run_admin_action(
        normalized,
        instance=normalized_instance,
        admin_reference=admin_reference,
        label=label,
    )
    result = audit_admin_action_result(
        result,
        audit_log_path=audit_log_path,
        username=username,
    )
    return _mark_restart_pending_for_admin_result(
        result,
        db_path=db_path,
        username=username,
        baseline_fingerprint=baseline_fingerprint,
        label=label,
    )
