"""Safe allowlisted web editing for server config.json."""

from __future__ import annotations

import copy
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from armactl import config_manager, discovery, paths
from armactl.redaction import redact_sensitive_text
from armactl.server_config_schema import (
    ServerConfigField,
    ServerConfigSchemaError,
    config_field_input_value,
    parse_form_field_value,
    web_config_field_descriptors,
)
from armactl.server_config_schema import (
    nested_value as schema_nested_value,
)
from armactl.server_config_schema import (
    set_nested_value as schema_set_nested_value,
)
from armactl.web.services import pending_work
from armactl.web.services.audit import AuditLogError, append_audit_event


class ConfigEditError(ValueError):
    """Raised when a web config edit cannot be safely applied."""


class ConfigAuditError(RuntimeError):
    """Raised when a saved config change could not be audited."""

    def __init__(self, message: str, *, result: ConfigEditResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ConfigEditResult:
    """Safe result for a successful config edit."""

    config_path: Path
    backup_path: Path | None
    changed_fields: tuple[str, ...]
    intent_audited: bool = True
    backend_success: bool = True
    audit_written: bool = True
    pending_work_warning: str = ""
    pending_work_error: str = ""


@dataclass(frozen=True)
class _PreparedConfigEdit:
    config_path: Path
    updated_config: dict[str, Any]
    changed_fields: tuple[str, ...]
    baseline_fingerprint: str = ""
    current_fingerprint: str = ""


_STRING_LIMIT = 512
CONFIG_SAVE_ACTION = "config.save"

CONFIG_FIELD_DESCRIPTORS = web_config_field_descriptors()
ALLOWLISTED_CONFIG_FORM_FIELDS = tuple(
    descriptor.form_name for descriptor in CONFIG_FIELD_DESCRIPTORS
)
_FIELD_PATHS = {
    descriptor.audit_field_name: descriptor.config_path
    for descriptor in CONFIG_FIELD_DESCRIPTORS
    if descriptor.audit_field_name is not None
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _nested_value(data: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    return schema_nested_value(data, path)


def _changed_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        field
        for field, field_path in _FIELD_PATHS.items()
        if _nested_value(before, field_path) != _nested_value(after, field_path)
    )


def _submitted_fields(form: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        descriptor.audit_field_name
        for descriptor in CONFIG_FIELD_DESCRIPTORS
        if descriptor.form_name in form
    )


def _config_restart_fingerprint(config: Mapping[str, Any]) -> str:
    payload = {
        field: _nested_value(config, field_path) for field, field_path in _FIELD_PATHS.items()
    }
    return pending_work.safe_state_fingerprint(payload)


def editable_config_field_descriptors() -> tuple[ServerConfigField, ...]:
    """Return the current safe web config field projection."""
    return CONFIG_FIELD_DESCRIPTORS


def extract_config_edit_form(form: Mapping[str, Any]) -> dict[str, Any]:
    """Return only allowlisted config edit form values keyed by descriptor name."""
    return {
        descriptor.form_name: form.get(descriptor.form_name)
        for descriptor in CONFIG_FIELD_DESCRIPTORS
    }


def _form_value_for_descriptor(
    config: Mapping[str, Any],
    descriptor: ServerConfigField,
) -> Any:
    value = config_field_input_value(config, descriptor.name)
    if descriptor.ui.control == "checkbox":
        return value if isinstance(value, bool) else False
    if descriptor.ui.control == "number":
        return value if isinstance(value, int) else ""
    return _safe_text(value)


def build_config_edit_form(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return only safe editable values from a loaded server config."""
    return {
        descriptor.form_name: _form_value_for_descriptor(config, descriptor)
        for descriptor in CONFIG_FIELD_DESCRIPTORS
    }


def build_config_edit_fields(config: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return safe editable values plus UI metadata for the config form."""
    form = build_config_edit_form(config)
    return tuple(
        {
            "name": descriptor.form_name,
            "value": form[descriptor.form_name],
            "config_path": ".".join(descriptor.config_path),
            "risk_class": descriptor.risk_class,
            "permission": descriptor.permission,
            "secret_behavior": descriptor.secret_behavior,
            "audit_field_name": descriptor.audit_field_name,
            "restart_behavior": descriptor.restart_behavior,
            "ui": {
                "label": descriptor.ui.label,
                "control": descriptor.ui.control,
                "group": descriptor.ui.group,
                "css_class": descriptor.ui.css_class,
                "required": descriptor.ui.required,
                "max_length": descriptor.ui.max_length,
                "min_value": descriptor.ui.min_value,
                "step": descriptor.ui.step,
                "helper_text": descriptor.ui.helper_text,
            },
        }
        for descriptor in CONFIG_FIELD_DESCRIPTORS
    )


def _set_nested_value(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    try:
        schema_set_nested_value(data, path, value)
    except ServerConfigSchemaError as exc:
        raise ConfigEditError(str(exc)) from exc


def _updated_config(data: dict[str, Any], form: Mapping[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(data)
    game = updated.get("game")
    if not isinstance(game, dict):
        raise ConfigEditError("game section must be an object.")
    properties = game.get("gameProperties")
    if properties is None:
        properties = {}
        game["gameProperties"] = properties
    if not isinstance(properties, dict):
        raise ConfigEditError("game.gameProperties must be an object.")

    for descriptor in CONFIG_FIELD_DESCRIPTORS:
        try:
            value = parse_form_field_value(form, descriptor)
        except ServerConfigSchemaError as exc:
            raise ConfigEditError(str(exc)) from exc
        _set_nested_value(updated, descriptor.config_path, value)
    return updated


def create_web_config_backup(config_path: Path) -> Path:
    """Create a web-specific pre-save backup next to config.json."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = config_path.with_name(
        f"{config_path.name}.before-web-config-save-{timestamp}.bak"
    )
    suffix = 1
    while backup_path.exists():
        backup_path = config_path.with_name(
            f"{config_path.name}.before-web-config-save-{timestamp}.{suffix}.bak"
        )
        suffix += 1
    try:
        shutil.copy2(config_path, backup_path)
    except OSError as exc:
        raise ConfigEditError("Failed to create config backup.") from exc
    return backup_path


def _prepare_basic_config_edit(config_path, form):
    path = Path(config_path)
    if not path.is_file():
        raise ConfigEditError("Config file was not found.")
    try:
        data = config_manager.load_config(path)
    except config_manager.ConfigError as exc:
        raise ConfigEditError(redact_sensitive_text(exc)) from exc
    if not isinstance(data, dict):
        raise ConfigEditError("Config root must be an object.")

    updated = _updated_config(data, form)
    changed_fields = _changed_fields(data, updated)
    return _PreparedConfigEdit(
        config_path=path,
        updated_config=updated,
        changed_fields=changed_fields,
        baseline_fingerprint=_config_restart_fingerprint(data),
        current_fingerprint=_config_restart_fingerprint(updated),
    )


def _apply_prepared_config_edit(prepared):
    if not prepared.changed_fields:
        return ConfigEditResult(
            config_path=prepared.config_path,
            backup_path=None,
            changed_fields=prepared.changed_fields,
        )
    backup_path = create_web_config_backup(prepared.config_path)
    try:
        config_manager.save_config(prepared.config_path, prepared.updated_config, backup=False)
    except config_manager.ConfigError as exc:
        raise ConfigEditError(redact_sensitive_text(exc)) from exc
    return ConfigEditResult(
        config_path=prepared.config_path,
        backup_path=backup_path,
        changed_fields=prepared.changed_fields,
    )


def save_basic_config_file(config_path, form):
    return _apply_prepared_config_edit(_prepare_basic_config_edit(config_path, form))


def save_default_config(instance: str, form: Mapping[str, Any]) -> ConfigEditResult:
    """Discover and safely update the config for the requested instance."""
    state = discovery.discover(instance=instance or paths.DEFAULT_INSTANCE_NAME, save=False)
    if not state.config_path:
        raise ConfigEditError("Server config path is unavailable.")
    return save_basic_config_file(state.config_path, form)


def _audit_config_save(
    *,
    audit_log_path: Path,
    username: str,
    instance: str,
    target: str,
    success: bool,
    message: str,
    changed_fields: tuple[str, ...],
    backup_path: Path | str | None = None,
    phase: str = "outcome",
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=CONFIG_SAVE_ACTION,
        instance=instance,
        target=target,
        success=success,
        message=message,
        exit_code=0 if success else 1,
        details={
            "phase": phase,
            "changed_fields": changed_fields,
            "backup_path": str(backup_path) if backup_path else "",
        },
    )


def _mark_restart_pending_for_config_result(
    result: ConfigEditResult,
    *,
    db_path: Path | None,
    username: str,
    instance: str,
    baseline_fingerprint: str = "",
    current_fingerprint: str = "",
) -> ConfigEditResult:
    if db_path is None or not result.changed_fields:
        return result
    if baseline_fingerprint and current_fingerprint:
        write_result = pending_work.mark_restart_pending_for_state(
            db_path,
            instance=instance,
            kind=pending_work.KIND_CONFIG,
            source_action=CONFIG_SAVE_ACTION,
            username=username,
            details=", ".join(result.changed_fields),
            baseline_fingerprint=baseline_fingerprint,
            current_fingerprint=current_fingerprint,
        )
    else:
        write_result = pending_work.mark_restart_pending_for_service(
            db_path,
            instance=instance,
            kind=pending_work.KIND_CONFIG,
            source_action=CONFIG_SAVE_ACTION,
            username=username,
            details=", ".join(result.changed_fields),
        )
    return replace(
        result,
        pending_work_warning=write_result.warning,
        pending_work_error=write_result.error,
    )


def save_default_config_and_audit(instance, form, *, audit_log_path, username, db_path=None):
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    target = "config.json"
    try:
        state = discovery.discover(instance=normalized_instance, save=False)
        if not state.config_path:
            raise ConfigEditError("Server config path is unavailable.")
        prepared = _prepare_basic_config_edit(state.config_path, form)
        target = str(prepared.config_path)
    except ConfigEditError as error:
        try:
            _audit_config_save(
                audit_log_path=audit_log_path,
                username=username,
                instance=normalized_instance,
                target=target,
                success=False,
                message=str(error),
                changed_fields=_submitted_fields(form),
            )
        except AuditLogError:
            pass
        raise
    if not prepared.changed_fields:
        return ConfigEditResult(
            config_path=prepared.config_path,
            backup_path=None,
            changed_fields=prepared.changed_fields,
        )

    try:
        _audit_config_save(
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=target,
            success=True,
            message="Config save requested.",
            changed_fields=prepared.changed_fields,
            phase="intent",
        )
    except AuditLogError as exc:
        raise ConfigEditError("Config was not saved because audit logging failed.") from exc

    try:
        result = _apply_prepared_config_edit(prepared)
    except ConfigEditError as error:
        try:
            _audit_config_save(
                audit_log_path=audit_log_path,
                username=username,
                instance=normalized_instance,
                target=target,
                success=False,
                message=str(error),
                changed_fields=prepared.changed_fields,
            )
        except AuditLogError:
            pass
        raise

    try:
        _audit_config_save(
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=str(result.config_path),
            success=True,
            message="Config saved.",
            changed_fields=result.changed_fields,
            backup_path=result.backup_path,
        )
    except AuditLogError as exc:
        result = _mark_restart_pending_for_config_result(
            replace(result, audit_written=False),
            db_path=db_path,
            username=username,
            instance=normalized_instance,
            baseline_fingerprint=prepared.baseline_fingerprint,
            current_fingerprint=prepared.current_fingerprint,
        )
        raise ConfigAuditError(
            "Config saved but audit logging failed.",
            result=result,
        ) from exc
    return _mark_restart_pending_for_config_result(
        result,
        db_path=db_path,
        username=username,
        instance=normalized_instance,
        baseline_fingerprint=prepared.baseline_fingerprint,
        current_fingerprint=prepared.current_fingerprint,
    )
