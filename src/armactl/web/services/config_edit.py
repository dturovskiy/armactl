"""Safe allowlisted web editing for server config.json."""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from armactl import config_manager, discovery, paths
from armactl.redaction import redact_sensitive_text
from armactl.server_config_schema import (
    RESTART_BEHAVIOR_CHANGED_ONLY,
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
from armactl.web.services import mutation_recovery, pending_work
from armactl.web.services.audit import AuditLogError, append_audit_event


class ConfigEditError(ValueError):
    """Raised when a web config edit cannot be safely applied."""


class SecretConfigEditError(ConfigEditError):
    """Raised when raw config input attempts to mutate a protected secret."""


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

@dataclass(frozen=True)
class RawConfigReplacementValidation:
    """Validated raw config replacement payload with existing secret guards applied."""

    replacement_text: str
    changed_fields: tuple[str, ...]
    baseline_fingerprint: str = ""
    current_fingerprint: str = ""


_STRING_LIMIT = 512
CONFIG_SAVE_ACTION = "config.save"
CONFIG_AUDIT_TARGET = "config.json"

RAW_CONFIG_SECRET_PLACEHOLDER = "<redacted: unchanged>"
RAW_CONFIG_MAX_BYTES = 512 * 1024
SECRET_CONFIG_PATHS = (
    ("game", "password"),
    ("game", "passwordAdmin"),
    ("rcon", "password"),
)

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


def _path_exists(data: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = data
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


def _set_secret_placeholder(data: dict[str, Any], path: tuple[str, ...]) -> None:
    try:
        schema_set_nested_value(data, path, RAW_CONFIG_SECRET_PLACEHOLDER)
    except ServerConfigSchemaError:
        return


def _is_secret_path(path: tuple[str, ...]) -> bool:
    return path in SECRET_CONFIG_PATHS


def _redacted_config_copy(config: Mapping[str, Any]) -> dict[str, Any]:
    redacted = copy.deepcopy(dict(config))
    for path in SECRET_CONFIG_PATHS:
        if not _path_exists(redacted, path):
            continue
        value = _nested_value(redacted, path)
        if value not in (None, ""):
            _set_secret_placeholder(redacted, path)
    return redacted


def _config_state_for_fingerprint(config: Mapping[str, Any]) -> dict[str, Any]:
    redacted = copy.deepcopy(dict(config))
    for path in SECRET_CONFIG_PATHS:
        if _path_exists(redacted, path):
            _set_secret_placeholder(redacted, path)
    return redacted


def _iter_leaf_paths(value: Any, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if isinstance(value, Mapping):
        paths: set[tuple[str, ...]] = set()
        for key, child in value.items():
            paths.update(_iter_leaf_paths(child, (*prefix, str(key))))
        return paths or {prefix}
    if isinstance(value, list):
        return {prefix}
    return {prefix}


def _changed_raw_config_fields(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[str, ...]:
    paths = sorted(
        _iter_leaf_paths(before) | _iter_leaf_paths(after),
        key=lambda item: ".".join(item),
    )
    changed = []
    for path in paths:
        if not path or _is_secret_path(path):
            continue
        before_value = _nested_value(before, path) if _path_exists(before, path) else None
        after_value = _nested_value(after, path) if _path_exists(after, path) else None
        if before_value != after_value:
            changed.append(".".join(path))
    if len(changed) > 30:
        return (*changed[:30], f"and {len(changed) - 30} more")
    return tuple(changed)


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
    return pending_work.safe_state_fingerprint(_config_state_for_fingerprint(config))


def build_raw_config_editor_text(config: Mapping[str, Any]) -> str:
    """Return formatted config JSON with secret values replaced by placeholders."""
    return json.dumps(_redacted_config_copy(config), ensure_ascii=False, indent=2)


def safe_raw_config_editor_text_for_rerender(raw_config: str) -> str | None:
    """Return submitted raw config text after redaction when it is safe to rerender."""
    if len(str(raw_config).encode("utf-8")) > RAW_CONFIG_MAX_BYTES:
        return None
    return redact_sensitive_text(raw_config)


def raw_config_editor_text_after_error(
    raw_config: str,
    error: ConfigEditError,
) -> str | None:
    """Return submitted raw config text when it is safe to echo after a save error."""
    if isinstance(error, SecretConfigEditError):
        return None
    return safe_raw_config_editor_text_for_rerender(raw_config)


def _parse_raw_config_text(raw_config: str) -> dict[str, Any]:
    text = _safe_text(raw_config)
    if not text:
        raise ConfigEditError("Config JSON is required.")
    if len(text.encode("utf-8")) > RAW_CONFIG_MAX_BYTES:
        raise ConfigEditError("Config JSON is too large for the web editor.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigEditError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigEditError("Config root must be an object.")
    return parsed


def _validate_server_config(data: dict[str, Any]) -> None:
    errors = config_manager.validate_config(data=data)
    if errors:
        raise ConfigEditError("Invalid config: " + "; ".join(errors))


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


def _config_edit_field_dto(
    descriptor: ServerConfigField,
    form: Mapping[str, Any],
) -> dict[str, Any]:
    ui = descriptor.ui
    assert ui is not None
    restart_required = descriptor.restart_behavior == RESTART_BEHAVIOR_CHANGED_ONLY
    return {
        "name": descriptor.form_name,
        "value": form[descriptor.form_name],
        "config_path": ".".join(descriptor.config_path),
        "risk_class": descriptor.risk_class,
        "permission": descriptor.permission,
        "secret_behavior": descriptor.secret_behavior,
        "audit_field_name": descriptor.audit_field_name,
        "restart_behavior": descriptor.restart_behavior,
        "restart_required": restart_required,
        "restart_label": "Restart required" if restart_required else "",
        "ui": {
            "label": ui.label,
            "control": ui.control,
            "group": ui.group,
            "css_class": ui.css_class,
            "required": ui.required,
            "max_length": ui.max_length,
            "min_value": ui.min_value,
            "step": ui.step,
            "helper_text": ui.helper_text,
            "section": ui.section,
            "section_label": ui.section_label,
            "section_helper_text": ui.section_helper_text,
            "impact_class": ui.impact_class,
            "impact_label": ui.impact_label,
        },
    }


def build_config_edit_fields(config: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return safe editable values plus UI metadata for the config form."""
    form = build_config_edit_form(config)
    return tuple(
        _config_edit_field_dto(descriptor, form)
        for descriptor in CONFIG_FIELD_DESCRIPTORS
    )


def build_config_edit_field_groups(config: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return safe editable fields grouped by descriptor UI metadata."""
    groups: list[dict[str, Any]] = []
    group_by_section: dict[str, dict[str, Any]] = {}
    for field in build_config_edit_fields(config):
        ui = field["ui"]
        section = str(ui.get("section") or ui.get("group") or "default")
        group = group_by_section.get(section)
        if group is None:
            group = {
                "section": section,
                "label": ui.get("section_label") or "",
                "helper_text": ui.get("section_helper_text") or "",
                "fields": [],
            }
            group_by_section[section] = group
            groups.append(group)
        group["fields"].append(field)
    return tuple(
        {**group, "fields": tuple(group["fields"]), "config_fields": tuple(group["fields"])}
        for group in groups
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


def _restore_secret_placeholders(
    submitted_config: dict[str, Any],
    current_config: Mapping[str, Any],
) -> dict[str, Any]:
    restored = copy.deepcopy(submitted_config)
    for path in SECRET_CONFIG_PATHS:
        current_exists = _path_exists(current_config, path)
        current_value = _nested_value(current_config, path) if current_exists else None
        submitted_exists = _path_exists(restored, path)
        submitted_value = _nested_value(restored, path) if submitted_exists else None

        if current_exists:
            if not submitted_exists:
                raise SecretConfigEditError(
                    "Secret fields cannot be removed in the web config editor."
                )
            if (
                current_value not in (None, "")
                and submitted_value == RAW_CONFIG_SECRET_PLACEHOLDER
            ):
                _set_nested_value(restored, path, current_value)
                continue
            if submitted_value != current_value:
                raise SecretConfigEditError(
                    "Secret fields cannot be changed in the web config editor."
                )
            continue

        if submitted_exists:
            raise SecretConfigEditError(
                "Secret fields cannot be changed in the web config editor."
            )
    return restored


def _prepare_raw_config_edit(config_path, raw_config):
    path = Path(config_path)
    if not path.is_file():
        raise ConfigEditError("Config file was not found.")
    try:
        data = config_manager.load_config(path)
    except config_manager.ConfigError as exc:
        raise ConfigEditError(redact_sensitive_text(exc)) from exc
    if not isinstance(data, dict):
        raise ConfigEditError("Config root must be an object.")

    submitted = _parse_raw_config_text(raw_config)
    current_game = data.get("game") if isinstance(data.get("game"), dict) else {}
    submitted_game = (
        submitted.get("game") if isinstance(submitted.get("game"), dict) else {}
    )
    if submitted_game.get("admins", []) != current_game.get("admins", []):
        raise ConfigEditError(
            "Server admins must be changed through the Admins workflow "
            "so supported mod permissions stay synchronized."
        )
    updated = _restore_secret_placeholders(submitted, data)
    _validate_server_config(updated)
    changed_fields = _changed_raw_config_fields(
        _config_state_for_fingerprint(data),
        _config_state_for_fingerprint(updated),
    )
    return _PreparedConfigEdit(
        config_path=path,
        updated_config=updated,
        changed_fields=changed_fields,
        baseline_fingerprint=_config_restart_fingerprint(data),
        current_fingerprint=_config_restart_fingerprint(updated),
    )

def validate_raw_config_replacement(config_path, raw_config: str):
    """Validate uploaded config.json content without bypassing web secret guards."""
    prepared = _prepare_raw_config_edit(config_path, raw_config)
    return RawConfigReplacementValidation(
        replacement_text=json.dumps(prepared.updated_config, indent=4),
        changed_fields=prepared.changed_fields,
        baseline_fingerprint=prepared.baseline_fingerprint,
        current_fingerprint=prepared.current_fingerprint,
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


def _safe_basename(value: Path | str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    names: list[str] = []
    for candidate in (Path(text).name, PureWindowsPath(text).name):
        if not candidate or "/" in candidate or "\\" in candidate:
            continue
        if candidate not in names:
            names.append(candidate)
    if names:
        return min(names, key=len)
    return ""


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
    backup_name = _safe_basename(backup_path)
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
            "backup_created": bool(backup_path),
            "backup_name": backup_name,
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
    details = ", ".join(result.changed_fields)
    if len(details) > 500:
        details = details[:497].rstrip() + "..."
    write_result = mutation_recovery.mark_restart_pending_for_mutation(
        mutation_recovery.RestartPendingRecovery(
            db_path=db_path,
            instance=instance,
            kind=pending_work.KIND_CONFIG,
            source_action=CONFIG_SAVE_ACTION,
            username=username,
            details=details,
            baseline_fingerprint=baseline_fingerprint,
            current_fingerprint=current_fingerprint,
        )
    )
    return replace(
        result,
        pending_work_warning=write_result.warning,
        pending_work_error=write_result.error,
    )


def save_default_config_and_audit(instance, form, *, audit_log_path, username, db_path=None):
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    target = CONFIG_AUDIT_TARGET
    try:
        state = discovery.discover(instance=normalized_instance, save=False)
        if not state.config_path:
            raise ConfigEditError("Server config path is unavailable.")
        prepared = _prepare_basic_config_edit(state.config_path, form)
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
            target=CONFIG_AUDIT_TARGET,
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


def save_default_raw_config_and_audit(
    instance,
    raw_config: str,
    *,
    audit_log_path,
    username,
    db_path=None,
):
    """Save full config JSON through the guarded web config editor."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    target = CONFIG_AUDIT_TARGET
    try:
        state = discovery.discover(instance=normalized_instance, save=False)
        if not state.config_path:
            raise ConfigEditError("Server config path is unavailable.")
        prepared = _prepare_raw_config_edit(state.config_path, raw_config)
    except ConfigEditError as error:
        try:
            _audit_config_save(
                audit_log_path=audit_log_path,
                username=username,
                instance=normalized_instance,
                target=target,
                success=False,
                message=str(error),
                changed_fields=("raw_config",),
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
            message="Raw config save requested.",
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
            target=CONFIG_AUDIT_TARGET,
            success=True,
            message="Raw config saved.",
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
        raise ConfigAuditError("Config saved but audit logging failed.", result=result) from exc
    return _mark_restart_pending_for_config_result(
        result,
        db_path=db_path,
        username=username,
        instance=normalized_instance,
        baseline_fingerprint=prepared.baseline_fingerprint,
        current_fingerprint=prepared.current_fingerprint,
    )
