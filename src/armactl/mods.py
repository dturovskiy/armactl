"""Mod management — add, remove, dedupe, import/export mods in config.json."""

from __future__ import annotations

from typing import Any
from armactl.config_manager import load_config, save_config

def list_mods(config_path: str | Any) -> list[dict[str, str]]:
    conf = load_config(config_path)
    return conf.get("game", {}).get("mods", [])

def add_mod(config_path: str | Any, mod_id: str, name: str = "", version: str = "") -> bool:
    conf = load_config(config_path)
    game = conf.setdefault("game", {})
    mods: list[dict[str, str]] = game.setdefault("mods", [])
    
    # Check if exists
    for m in mods:
        if m.get("modId") == mod_id:
            m["name"] = name or m.get("name", "")
            m["version"] = version or m.get("version", "")
            save_config(config_path, conf)
            return False # updated
            
    mods.append({"modId": mod_id, "name": name, "version": version})
    save_config(config_path, conf)
    return True # added

def remove_mod(config_path: str | Any, mod_id: str) -> bool:
    conf = load_config(config_path)
    game = conf.get("game", {})
    mods: list[dict[str, str]] = game.get("mods", [])
    
    initial_len = len(mods)
    mods = [m for m in mods if m.get("modId") != mod_id]
    
    if len(mods) != initial_len:
        game["mods"] = mods
        save_config(config_path, conf)
        return True
    return False

def dedupe_mods(config_path: str | Any) -> int:
    conf = load_config(config_path)
    game = conf.get("game", {})
    mods: list[dict[str, str]] = game.get("mods", [])
    
    seen = set()
    deduped = []
    
    for m in mods:
        mid = m.get("modId")
        if mid not in seen:
            seen.add(mid)
            deduped.append(m)
            
    removed = len(mods) - len(deduped)
    if removed > 0:
        game["mods"] = deduped
        save_config(config_path, conf)
    return removed

def export_mods(config_path: str | Any) -> str:
    """Returns a comma separated list of modIds."""
    mods = list_mods(config_path)
    return ",".join(m.get("modId", "") for m in mods if m.get("modId"))

def import_mods(config_path: str | Any, mod_list_str: str) -> int:
    """Import mods from comma/space/newline separated string."""
    import re
    conf = load_config(config_path)
    game = conf.setdefault("game", {})
    mods: list[dict[str, str]] = game.setdefault("mods", [])
    
    # Extract IDs: we assume IDs are hex/alphanumeric strings
    raw_ids = re.split(r'[,;|\s]+', mod_list_str)
    raw_ids = [r.strip() for r in raw_ids if r.strip()]
    
    added_count = 0
    existing_ids = {m.get("modId") for m in mods}
    
    for mid in raw_ids:
        if mid not in existing_ids:
            mods.append({"modId": mid, "name": f"Imported {mid}", "version": ""})
            existing_ids.add(mid)
            added_count += 1
            
    if added_count > 0:
        save_config(config_path, conf)
        
    return added_count
