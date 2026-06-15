"""Controlled web actions for Arma Workshop mods."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from armactl import discovery, mods_manager, paths
from armactl.addon_cleanup import CleanupResult
from armactl.config_manager import ConfigError
from armactl.redaction import redact_sensitive_text
from armactl.web.services.audit import AuditLogError, append_audit_event

ACTION_ADD = "mod.add"
ACTION_UPDATE = "mod.update"
ACTION_DISABLE = "mod.disable"
ACTION_ENABLE = "mod.enable"
ACTION_REMOVE = "mod.remove"
SUPPORTED_ACTIONS = frozenset(
    {
        ACTION_ADD,
        ACTION_DISABLE,
        ACTION_ENABLE,
        ACTION_REMOVE,
    }
)
MAX_MOD_TEXT_LENGTH = 256
MAX_AUDIT_IDS = 10


class ModActionError(ValueError):
    """Raised when a mod action request is invalid."""


@dataclass(frozen=True)
class ModActionResult:
    """Safe result for rendering and audit logging."""

    action: str
    instance: str
    target: str
    success: bool
    changed: bool
    message: str
    exit_code: int
    audit_written: bool = True
    details: dict[str, object] = field(default_factory=dict)

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def normalize_mod_action(action: str) -> str:
    """Normalize a user-facing mod action name."""
    return action.strip().lower()


def is_supported_action(action: str) -> bool:
    """Return whether the action is a known mod action."""
    return normalize_mod_action(action) in SUPPORTED_ACTIONS


def confirmation_failure(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    target: str = "",
) -> ModActionResult:
    """Return a controlled failure for missing remove confirmation."""
    return ModActionResult(
        action=ACTION_REMOVE,
        instance=instance,
        target=_safe_text(target),
        success=False,
        changed=False,
        message="Confirmation is required to remove this mod.",
        exit_code=1,
        audit_written=False,
    )


def _safe_text(value: object, *, max_length: int = 500) -> str:
    redacted = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(redacted) > max_length:
        return f"{redacted[:max_length]}..."
    return redacted


def _validated_optional_text(value: Any, *, message: str) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_MOD_TEXT_LENGTH or any(ord(char) < 32 for char in text):
        raise ModActionError(message)
    return text


def _mod_id(value: Any) -> str:
    return mods_manager.require_valid_mod_id(value)


def _config_path(instance: str) -> Path:
    try:
        state = discovery.discover(instance=instance, save=False)
    except Exception as exc:
        raise ModActionError("Server config path is unavailable.") from exc
    if not state.config_path:
        raise ModActionError("Server config path is unavailable.")
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
    details: dict[str, object] | None = None,
) -> ModActionResult:
    return ModActionResult(
        action=action,
        instance=instance,
        target=_safe_text(target),
        success=success,
        changed=changed,
        message=_safe_text(message),
        exit_code=exit_code,
        details=details or {},
    )


def _failure(
    *,
    action: str,
    instance: str,
    target: str,
    message: object,
) -> ModActionResult:
    return _result(
        action=action,
        instance=instance,
        target=target,
        success=False,
        changed=False,
        message=_safe_text(message),
        exit_code=1,
    )


def _cleanup_details(result: mods_manager.ModUpdateResult) -> dict[str, object]:
    details: dict[str, object] = {}
    removed_ids = sorted(result.removed_ids)
    if removed_ids:
        details["removed_ids"] = removed_ids[:MAX_AUDIT_IDS]
        details["removed_id_count"] = str(len(removed_ids))
    cleanup = result.cleanup_result
    if cleanup is not None:
        details.update(_cleanup_result_details(cleanup))
    if result.enospc_retry_performed:
        details["enospc_retry_performed"] = "yes"
    return details


def _cleanup_result_details(cleanup: CleanupResult) -> dict[str, object]:
    return {
        "cleanup_deleted_count": str(len(cleanup.deleted)),
        "cleanup_skipped_count": str(len(cleanup.skipped)),
        "cleanup_error_count": str(len(cleanup.errors)),
        "cleanup_freed": cleanup.freed_display,
    }


def add_or_update_mod(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    mod_id: str,
    name: str = "",
    version: str = "",
) -> ModActionResult:
    """Add, update, or reactivate one Workshop mod through mods_manager."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        reference = _mod_id(mod_id)
        mod_name = _validated_optional_text(name, message="Mod name is invalid.")
        mod_version = _validated_optional_text(version, message="Mod version is invalid.")
        config_path = _config_path(normalized_instance)
        result = mods_manager.add_mod_detailed(
            config_path,
            reference,
            mod_name,
            mod_version,
        )
    except (ModActionError, ConfigError) as error:
        return _failure(
            action=ACTION_ADD,
            instance=normalized_instance,
            target=mod_id,
            message=error,
        )
    except Exception:
        return _failure(
            action=ACTION_ADD,
            instance=normalized_instance,
            target=mod_id,
            message="Mod action is unavailable.",
        )

    if result.status == "added":
        return _result(
            action=ACTION_ADD,
            instance=normalized_instance,
            target=reference,
            success=True,
            changed=True,
            message="Mod added.",
            exit_code=0,
        )
    if result.status == "updated":
        return _result(
            action=ACTION_UPDATE,
            instance=normalized_instance,
            target=reference,
            success=True,
            changed=True,
            message="Mod updated.",
            exit_code=0,
        )
    if result.status == "reactivated":
        return _result(
            action=ACTION_ENABLE,
            instance=normalized_instance,
            target=reference,
            success=True,
            changed=True,
            message="Mod enabled.",
            exit_code=0,
        )
    return _result(
        action=ACTION_ADD,
        instance=normalized_instance,
        target=reference,
        success=True,
        changed=False,
        message="Mod was unchanged.",
        exit_code=0,
    )


def disable_mod(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    mod_id: str,
) -> ModActionResult:
    """Disable one active Workshop mod through mods_manager."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        reference = _mod_id(mod_id)
        config_path = _config_path(normalized_instance)
        changed = mods_manager.disable_mod(config_path, reference)
    except (ModActionError, ConfigError) as error:
        return _failure(
            action=ACTION_DISABLE,
            instance=normalized_instance,
            target=mod_id,
            message=error,
        )
    except Exception:
        return _failure(
            action=ACTION_DISABLE,
            instance=normalized_instance,
            target=mod_id,
            message="Mod action is unavailable.",
        )

    return _result(
        action=ACTION_DISABLE,
        instance=normalized_instance,
        target=reference,
        success=True,
        changed=changed,
        message="Mod disabled." if changed else "Mod was unchanged.",
        exit_code=0,
    )


def enable_mod(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    mod_id: str,
) -> ModActionResult:
    """Enable one disabled Workshop mod through mods_manager."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        reference = _mod_id(mod_id)
        config_path = _config_path(normalized_instance)
        changed = mods_manager.enable_mod(config_path, reference)
    except (ModActionError, ConfigError) as error:
        return _failure(
            action=ACTION_ENABLE,
            instance=normalized_instance,
            target=mod_id,
            message=error,
        )
    except Exception:
        return _failure(
            action=ACTION_ENABLE,
            instance=normalized_instance,
            target=mod_id,
            message="Mod action is unavailable.",
        )

    return _result(
        action=ACTION_ENABLE,
        instance=normalized_instance,
        target=reference,
        success=True,
        changed=changed,
        message="Mod enabled." if changed else "Mod was unchanged.",
        exit_code=0,
    )


def remove_mod(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    mod_id: str,
) -> ModActionResult:
    """Remove one active or disabled Workshop mod through mods_manager."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        reference = _mod_id(mod_id)
        config_path = _config_path(normalized_instance)
        result = mods_manager.remove_mod_detailed(config_path, reference)
    except (ModActionError, ConfigError) as error:
        return _failure(
            action=ACTION_REMOVE,
            instance=normalized_instance,
            target=mod_id,
            message=error,
        )
    except Exception:
        return _failure(
            action=ACTION_REMOVE,
            instance=normalized_instance,
            target=mod_id,
            message="Mod action is unavailable.",
        )

    return _result(
        action=ACTION_REMOVE,
        instance=normalized_instance,
        target=reference,
        success=True,
        changed=result.config_changed,
        message="Mod removed." if result.config_changed else "Mod was unchanged.",
        exit_code=0,
        details=_cleanup_details(result),
    )


def run_mod_action(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    mod_id: str = "",
    name: str = "",
    version: str = "",
) -> ModActionResult:
    """Run a controlled Workshop mod action without auditing."""
    normalized = normalize_mod_action(action)
    if normalized == ACTION_ADD:
        return add_or_update_mod(
            instance=instance,
            mod_id=mod_id,
            name=name,
            version=version,
        )
    if normalized == ACTION_DISABLE:
        return disable_mod(instance=instance, mod_id=mod_id)
    if normalized == ACTION_ENABLE:
        return enable_mod(instance=instance, mod_id=mod_id)
    if normalized == ACTION_REMOVE:
        return remove_mod(instance=instance, mod_id=mod_id)
    raise ModActionError("Unknown mod action.")


def audit_mod_action_result(
    result: ModActionResult,
    *,
    audit_log_path: Path,
    username: str,
) -> ModActionResult:
    """Append an audit event for a Workshop mod action result."""
    details = {"changed": "yes" if result.changed else "no", **result.details}
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
            details=details,
        )
    except AuditLogError:
        return replace(
            result,
            success=False,
            message="Mod action completed but audit logging failed.",
            exit_code=1,
            audit_written=False,
        )
    return result


def run_mod_action_and_audit(
    action: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    mod_id: str = "",
    name: str = "",
    version: str = "",
    audit_log_path: Path,
    username: str,
) -> ModActionResult:
    """Run a Workshop mod action and append a safe audit event."""
    result = run_mod_action(
        action,
        instance=instance,
        mod_id=mod_id,
        name=name,
        version=version,
    )
    return audit_mod_action_result(
        result,
        audit_log_path=audit_log_path,
        username=username,
    )
