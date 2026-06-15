"""Controlled web actions for Arma game admins."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from armactl import admins_manager, discovery, paths
from armactl.config_manager import ConfigError
from armactl.redaction import redact_sensitive_text
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


def _config_path(instance: str) -> Path:
    try:
        state = discovery.discover(instance=instance, save=False)
    except Exception as exc:
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
        created = admins_manager.add_admin(config_path, reference, name)
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
    except Exception:
        return _failure(
            action=ACTION_ADD,
            instance=normalized_instance,
            target=reference,
            message="Admin action is unavailable.",
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
        removed = admins_manager.remove_admin(config_path, reference)
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
    except Exception:
        return _failure(
            action=ACTION_REMOVE,
            instance=normalized_instance,
            target=reference,
            message="Admin action is unavailable.",
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
            details={"changed": "yes" if result.changed else "no"},
        )
    except AuditLogError:
        return replace(
            result,
            success=False,
            message="Admin action completed but audit logging failed.",
            exit_code=1,
            audit_written=False,
        )
    return result


def run_admin_action_and_audit(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    admin_reference: str = "",
    label: str = "",
    audit_log_path: Path,
    username: str,
) -> AdminActionResult:
    """Run a game-admin action and append a safe audit event."""
    result = run_admin_action(
        action,
        instance=instance,
        admin_reference=admin_reference,
        label=label,
    )
    return audit_admin_action_result(
        result,
        audit_log_path=audit_log_path,
        username=username,
    )
