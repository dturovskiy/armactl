"""Mod management - add, remove, dedupe, import, and export mods in config.json."""

from __future__ import annotations

import re
from typing import Any

from armactl.config_manager import load_config, save_config


def list_mods(config_path: str | Any) -> list[dict[str, str]]:
    """Return the configured mod list."""
    conf = load_config(config_path)
    return conf.get("game", {}).get("mods", [])


def add_mod(
    config_path: str | Any,
    mod_id: str,
    name: str = "",
    version: str = "",
) -> bool:
    """Add or update a mod. Returns True when added, False when updated."""
    conf = load_config(config_path)
    game = conf.setdefault("game", {})
    mods: list[dict[str, str]] = game.setdefault("mods", [])

    for mod in mods:
        if mod.get("modId") == mod_id:
            mod["name"] = name or mod.get("name", "")
            mod["version"] = version or mod.get("version", "")
            save_config(config_path, conf)
            return False

    mods.append({"modId": mod_id, "name": name, "version": version})
    save_config(config_path, conf)
    return True


def remove_mod(config_path: str | Any, mod_id: str) -> bool:
    """Remove a mod by id."""
    conf = load_config(config_path)
    game = conf.get("game", {})
    mods: list[dict[str, str]] = game.get("mods", [])

    initial_len = len(mods)
    mods = [mod for mod in mods if mod.get("modId") != mod_id]

    if len(mods) != initial_len:
        game["mods"] = mods
        save_config(config_path, conf)
        return True
    return False


def dedupe_mods(config_path: str | Any) -> int:
    """Remove duplicate mods and return the number removed."""
    conf = load_config(config_path)
    game = conf.get("game", {})
    mods: list[dict[str, str]] = game.get("mods", [])

    seen: set[str | None] = set()
    deduped: list[dict[str, str]] = []
    for mod in mods:
        mod_id = mod.get("modId")
        if mod_id not in seen:
            seen.add(mod_id)
            deduped.append(mod)

    removed = len(mods) - len(deduped)
    if removed > 0:
        game["mods"] = deduped
        save_config(config_path, conf)
    return removed


def export_mods(config_path: str | Any) -> str:
    """Return a comma-separated list of mod ids."""
    mods = list_mods(config_path)
    return ",".join(mod.get("modId", "") for mod in mods if mod.get("modId"))


def import_mods(config_path: str | Any, mod_list_str: str) -> int:
    """Import mods from a comma, space, or newline separated string."""
    conf = load_config(config_path)
    game = conf.setdefault("game", {})
    mods: list[dict[str, str]] = game.setdefault("mods", [])

    raw_ids = re.split(r"[,;|\s]+", mod_list_str)
    raw_ids = [item.strip() for item in raw_ids if item.strip()]

    added_count = 0
    existing_ids = {mod.get("modId") for mod in mods}
    for mod_id in raw_ids:
        if mod_id not in existing_ids:
            mods.append(
                {
                    "modId": mod_id,
                    "name": f"Imported {mod_id}",
                    "version": "",
                }
            )
            existing_ids.add(mod_id)
            added_count += 1

    if added_count > 0:
        save_config(config_path, conf)

    return added_count
