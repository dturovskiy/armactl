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
ALLOWLISTED_CONFIG_FORM_FIELDS = (
    "name",
    "scenario_id",
    "max_players",
    "visible",
    "battleye",
    "server_max_view_distance",
    "server_min_grass_distance",
)
_FIELD_PATHS = {
    "name": ("game", "name"),
    "scenario_id": ("game", "scenarioId"),
    "max_players": ("game", "maxPlayers"),
    "visible": ("game", "visible"),
    "battleye": ("game", "gameProperties", "battlEye"),
    "server_max_view_distance": (
        "game",
        "gameProperties",
        "serverMaxViewDistance",
    ),
    "server_min_grass_distance": (
        "game",
        "gameProperties",
        "serverMinGrassDistance",
    ),
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_field(form: Mapping[str, Any], key: str, message: str) -> str:
    value = _safe_text(form.get(key))
    if not value:
        raise ConfigEditError(message)
    if len(value) > _STRING_LIMIT or any(ord(char) < 32 for char in value):
        raise ConfigEditError(message)
    return value


def _int_field(
    form: Mapping[str, Any],
    key: str,
    message: str,
    *,
    minimum: int,
) -> int:
    raw = _safe_text(form.get(key))
    try:
        value = int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise ConfigEditError(message) from exc
    if str(value) != raw and raw not in {f"+{value}", f"0{value}"}:
        raise ConfigEditError(message)
    if value < minimum:
        raise ConfigEditError(message)
    return value


def _bool_field(form: Mapping[str, Any], key: str) -> bool:
    if key not in form or form.get(key) in (None, ""):
        return False
    value = _safe_text(form.get(key)).lower()
    if value in {"1", "true", "on", "yes"}:
        return True
    if value in {"0", "false", "off", "no"}:
        return False
    raise ConfigEditError("Boolean field value is invalid.")


def _nested_value(data: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _changed_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        field
        for field, field_path in _FIELD_PATHS.items()
        if _nested_value(before, field_path) != _nested_value(after, field_path)
    )


def _submitted_fields(form: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(field for field in ALLOWLISTED_CONFIG_FORM_FIELDS if field in form)


def _config_restart_fingerprint(config: Mapping[str, Any]) -> str:
    payload = {
        field: _nested_value(config, field_path)
        for field, field_path in _FIELD_PATHS.items()
    }
    return pending_work.safe_state_fingerprint(payload)


def build_config_edit_form(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return only safe editable values from a loaded server config."""
    game = config.get("game", {}) if isinstance(config.get("game"), Mapping) else {}
    properties = (
        game.get("gameProperties", {})
        if isinstance(game.get("gameProperties"), Mapping)
        else {}
    )
    return {
        "name": _safe_text(game.get("name")),
        "scenario_id": _safe_text(game.get("scenarioId")),
        "max_players": game.get("maxPlayers") if isinstance(game.get("maxPlayers"), int) else "",
        "visible": game.get("visible") if isinstance(game.get("visible"), bool) else False,
        "battleye": (
            properties.get("battlEye")
            if isinstance(properties.get("battlEye"), bool)
            else False
        ),
        "server_max_view_distance": (
            properties.get("serverMaxViewDistance")
            if isinstance(properties.get("serverMaxViewDistance"), int)
            else ""
        ),
        "server_min_grass_distance": (
            properties.get("serverMinGrassDistance")
            if isinstance(properties.get("serverMinGrassDistance"), int)
            else ""
        ),
    }


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

    game["name"] = _string_field(form, "name", "game.name is required.")
    game["scenarioId"] = _string_field(form, "scenario_id", "game.scenarioId is required.")
    game["maxPlayers"] = _int_field(
        form,
        "max_players",
        "game.maxPlayers must be a positive integer.",
        minimum=1,
    )
    game["visible"] = _bool_field(form, "visible")
    properties["battlEye"] = _bool_field(form, "battleye")
    properties["serverMaxViewDistance"] = _int_field(
        form,
        "server_max_view_distance",
        "game.gameProperties.serverMaxViewDistance must be a positive integer.",
        minimum=1,
    )
    properties["serverMinGrassDistance"] = _int_field(
        form,
        "server_min_grass_distance",
        "game.gameProperties.serverMinGrassDistance must be a non-negative integer.",
        minimum=0,
    )
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
