"""Mod manager — handle mods configuration in config.json."""

import json
from pathlib import Path

from armactl.config_manager import load_config, save_config, ConfigError


def get_mods(config_path: Path | str) -> list[dict]:
    """Return the list of mods from config."""
    config = load_config(config_path)
    game = config.get("game", {})
    return game.get("mods", [])


def set_mods(config_path: Path | str, mods_list: list[dict]) -> None:
    """Save a new list of mods to config."""
    config = load_config(config_path)
    if "game" not in config:
        config["game"] = {}
    config["game"]["mods"] = mods_list
    save_config(config_path, config)


def add_mod(config_path: Path | str, mod_id: str, name: str = "", version: str = "") -> bool:
    """Add a mod to config. Returns True if added, False if already exists."""
    mods = get_mods(config_path)
    
    # Check if already exists
    for mod in mods:
        if mod.get("modId") == mod_id:
            return False
            
    mods.append({
        "modId": mod_id,
        "name": name,
        "version": version
    })
    set_mods(config_path, mods)
    return True


def remove_mod(config_path: Path | str, mod_id: str) -> bool:
    """Remove a mod from config by ID. Returns True if removed."""
    mods = get_mods(config_path)
    new_mods = [m for m in mods if m.get("modId") != mod_id]
    
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
    seen = set()
    deduped = []
    
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
    mods = get_mods(config_path)
    with open(export_file, "w", encoding="utf-8") as f:
        json.dump(mods, f, indent=4)
    return len(mods)


def import_mods(config_path: Path | str, import_file: Path | str, append: bool = False) -> tuple[int, int]:
    """Import mods from a JSON file.
    
    Returns (added_count, skipped_count).
    If append is False, overwrites existing mods.
    """
    with open(import_file, "r", encoding="utf-8") as f:
        try:
            imported_mods = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in import file: {e}")

    if not isinstance(imported_mods, list):
        raise ConfigError("Import file must contain a JSON array of mod objects.")
        
    for m in imported_mods:
        if not isinstance(m, dict) or "modId" not in m:
            raise ConfigError("Each imported mod must be an object containing a 'modId' key.")

    current_mods = get_mods(config_path) if append else []
    seen_ids = {m.get("modId") for m in current_mods}
    
    added_count = 0
    skipped_count = 0
    
    for mod in imported_mods:
        mod_id = mod.get("modId")
        if mod_id in seen_ids:
            skipped_count += 1
            continue
            
        current_mods.append({
            "modId": mod_id,
            "name": mod.get("name", ""),
            "version": mod.get("version", "")
        })
        seen_ids.add(mod_id)
        added_count += 1

    set_mods(config_path, current_mods)
    return added_count, skipped_count
