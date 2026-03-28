"""Mod manager - handle mods configuration in config.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from armactl.config_manager import ConfigError, load_config, save_config
from armactl.i18n import _, tr


def get_mods(config_path: Path | str) -> list[dict[str, Any]]:
    """Return the list of mods from config."""
    config = load_config(config_path)
    game = config.get("game", {})
    return game.get("mods", [])


def set_mods(config_path: Path | str, mods_list: list[dict[str, Any]]) -> None:
    """Save a new list of mods to config."""
    config = load_config(config_path)
    if "game" not in config:
        config["game"] = {}
    config["game"]["mods"] = mods_list
    save_config(config_path, config)


def add_mod(
    config_path: Path | str,
    mod_id: str,
    name: str = "",
    version: str = "",
) -> bool:
    """Add a mod to config. Returns True if added, False if already exists."""
    mods = get_mods(config_path)

    for mod in mods:
        if mod.get("modId") == mod_id:
            return False

    mods.append({"modId": mod_id, "name": name, "version": version})
    set_mods(config_path, mods)
    return True


def remove_mod(config_path: Path | str, mod_id: str) -> bool:
    """Remove a mod from config by ID. Returns True if removed."""
    mods = get_mods(config_path)
    new_mods = [mod for mod in mods if mod.get("modId") != mod_id]

    if len(new_mods) == len(mods):
        return False

    set_mods(config_path, new_mods)
    return True


def clear_mods(config_path: Path | str) -> int:
    """Remove all mods. Returns the number of removed mods."""
    mods = get_mods(config_path)
    if not mods:
        return 0
    set_mods(config_path, [])
    return len(mods)


def dedupe_mods(config_path: Path | str) -> int:
    """Remove duplicate mods (by modId). Returns number of duplicates removed."""
    mods = get_mods(config_path)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []

    for mod in mods:
        mod_id = mod.get("modId")
        if mod_id and mod_id not in seen:
            seen.add(mod_id)
            deduped.append(mod)

    duplicates_removed = len(mods) - len(deduped)
    if duplicates_removed > 0:
        set_mods(config_path, deduped)

    return duplicates_removed


def export_mods(config_path: Path | str, export_file: Path | str) -> int:
    """Export currently configured mods to a JSON file."""
    export_path = Path(export_file)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    mods = get_mods(config_path)
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(mods, f, indent=4)
    return len(mods)


def _extract_import_mods(payload: Any) -> list[dict[str, str]]:
    """Normalize imported payload to a list of mod objects."""
    if isinstance(payload, dict):
        payload = payload.get("game", {}).get("mods")

    if not isinstance(payload, list):
        raise ConfigError(
            _(
                "Import file must contain either a JSON array of mod objects "
                "or a full config object with game.mods."
            )
        )

    normalized: list[dict[str, str]] = []
    for mod in payload:
        if not isinstance(mod, dict) or "modId" not in mod:
            raise ConfigError(
                _("Each imported mod must be an object containing a 'modId' key.")
            )

        mod_id = str(mod.get("modId", "")).strip()
        if not mod_id:
            raise ConfigError(_("Imported mod 'modId' cannot be empty."))

        normalized.append(
            {
                "modId": mod_id,
                "name": str(mod.get("name", "")),
                "version": str(mod.get("version", "")),
            }
        )

    return normalized


def _load_import_mods(import_file: Path | str) -> list[dict[str, str]]:
    """Read and validate mods from an import file."""
    with open(import_file, encoding="utf-8") as f:
        try:
            return _extract_import_mods(json.load(f))
        except json.JSONDecodeError as e:
            raise ConfigError(tr("Invalid JSON in import file: {error}", error=e)) from e


def preview_import_mods(import_file: Path | str) -> int:
    """Return the number of importable mods in a JSON file."""
    return len(_load_import_mods(import_file))


def import_mods(
    config_path: Path | str,
    import_file: Path | str,
    append: bool = False,
) -> tuple[int, int]:
    """Import mods from a JSON file.

    Returns `(added_count, skipped_count)`.
    If `append` is False, overwrites existing mods.
    """
    imported_mods = _load_import_mods(import_file)
    current_mods = get_mods(config_path) if append else []
    seen_ids = {mod.get("modId") for mod in current_mods}

    added_count = 0
    skipped_count = 0
    for mod in imported_mods:
        mod_id = mod.get("modId")
        if mod_id in seen_ids:
            skipped_count += 1
            continue

        current_mods.append(
            {
                "modId": mod_id,
                "name": mod.get("name", ""),
                "version": mod.get("version", ""),
            }
        )
        seen_ids.add(mod_id)
        added_count += 1

    set_mods(config_path, current_mods)
    return added_count, skipped_count
