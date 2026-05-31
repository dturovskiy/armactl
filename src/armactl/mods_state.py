"""armactl-only state for reversibly disabled Workshop mods.

The Arma Reforger dedicated server reads config/config.json directly and rejects
unknown keys.  Disabled mods are armactl metadata, so they must never be written
into the server-facing JSON.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armactl.config_manager import ConfigError, load_config, save_config

SIDECAR_FILENAME = "mods-state.json"
LEGACY_DISABLED_MODS_KEY = "disabledMods"


@dataclass(frozen=True)
class DisabledModsMigrationResult:
    migrated: bool
    legacy_entries: int
    sidecar_path: Path


def mods_state_path_for_config(config_path: Path | str) -> Path:
    """Return the instance-scoped armactl metadata sidecar path."""
    config_path = Path(config_path)
    if config_path.name != "config.json":
        raise ConfigError(f"Expected config.json path, got: {config_path}")
    if config_path.parent.name == "config":
        return config_path.parent.parent / SIDECAR_FILENAME
    return config_path.parent / SIDECAR_FILENAME


def _normalize_entries(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ConfigError("disabledMods metadata must be a JSON list.")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"disabledMods[{index}] must be a JSON object.")
        normalized.append(dict(item))
    return normalized


def _entry_key(entry: dict[str, Any]) -> str:
    return str(entry.get("modId") or "").strip().upper()


def merge_disabled_mods(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge disabled mod lists while preserving order and removing duplicates."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            entry = dict(raw)
            key = _entry_key(entry)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged


def load_disabled_mods(config_path: Path | str) -> list[dict[str, Any]]:
    """Load armactl-only disabled mod metadata from the sidecar."""
    state_path = mods_state_path_for_config(config_path)
    if not state_path.is_file():
        return []
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {state_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Failed to read {state_path}: {exc}") from exc
    if isinstance(payload, list):
        # Compatibility with an early sidecar prototype.
        return _normalize_entries(payload)
    if not isinstance(payload, dict):
        raise ConfigError(f"{state_path} must contain a JSON object.")
    return _normalize_entries(payload.get(LEGACY_DISABLED_MODS_KEY, []))


def save_disabled_mods(config_path: Path | str, mods: Iterable[dict[str, Any]]) -> Path:
    """Atomically store armactl-only disabled mod metadata."""
    state_path = mods_state_path_for_config(config_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {LEGACY_DISABLED_MODS_KEY: merge_disabled_mods(mods)}
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"Failed to save {state_path}: {exc}") from exc
    return state_path


def migrate_legacy_disabled_mods(config_path: Path | str) -> DisabledModsMigrationResult:
    """Move legacy game.disabledMods metadata out of server-facing config.json."""
    config_path = Path(config_path)
    state_path = mods_state_path_for_config(config_path)
    config = load_config(config_path)
    game = config.get("game")
    if not isinstance(game, dict) or LEGACY_DISABLED_MODS_KEY not in game:
        return DisabledModsMigrationResult(False, 0, state_path)

    legacy = _normalize_entries(game.get(LEGACY_DISABLED_MODS_KEY))
    existing = load_disabled_mods(config_path)
    save_disabled_mods(config_path, merge_disabled_mods(existing, legacy))
    game.pop(LEGACY_DISABLED_MODS_KEY, None)
    save_config(config_path, config, backup=True)
    return DisabledModsMigrationResult(True, len(legacy), state_path)
