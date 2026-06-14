"""Safe allowlisted web editing for server config.json."""

from __future__ import annotations

import copy
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from armactl import config_manager, discovery, paths
from armactl.redaction import redact_sensitive_text


class ConfigEditError(ValueError):
    """Raised when a web config edit cannot be safely applied."""


@dataclass(frozen=True)
class ConfigEditResult:
    """Safe result for a successful config edit."""

    config_path: Path
    backup_path: Path


_STRING_LIMIT = 512


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


def save_basic_config_file(config_path: Path | str, form: Mapping[str, Any]) -> ConfigEditResult:
    """Apply an allowlisted web config edit to one config file."""
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
    backup_path = create_web_config_backup(path)
    try:
        config_manager.save_config(path, updated, backup=False)
    except config_manager.ConfigError as exc:
        raise ConfigEditError(redact_sensitive_text(exc)) from exc
    return ConfigEditResult(config_path=path, backup_path=backup_path)


def save_default_config(instance: str, form: Mapping[str, Any]) -> ConfigEditResult:
    """Discover and safely update the config for the requested instance."""
    state = discovery.discover(instance=instance or paths.DEFAULT_INSTANCE_NAME, save=False)
    if not state.config_path:
        raise ConfigEditError("Server config path is unavailable.")
    return save_basic_config_file(state.config_path, form)
