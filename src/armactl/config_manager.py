"""Config manager — safe reading and writing of config.json.

Handles parsing, validating, backing up, and atomic writing
of the Arma Reforger dedicated server config file.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from armactl import paths as P


class ConfigError(Exception):
    """Raised when there's an error reading/writing/validating config."""
    pass


def load_config(config_path: Path | str) -> dict[str, Any]:
    """Load config.json from disk."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config file: {e}")
    except OSError as e:
        raise ConfigError(f"Failed to read config file: {e}")


def save_config(config_path: Path | str, data: dict[str, Any], backup: bool = True) -> None:
    """Save config.json to disk safely with optional backup.
    
    1. Creates a backup if requested.
    2. Writes to a .tmp file.
    3. Atomically renames .tmp to config.json.
    """
    config_path = Path(config_path)
    if backup and config_path.exists():
        _create_backup(config_path)

    tmp_path = config_path.with_suffix(".json.tmp")
    
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        
        # Atomic replace (os.replace works across platforms, but this is Linux)
        os.replace(tmp_path, config_path)
    except OSError as e:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise ConfigError(f"Failed to save config file: {e}")


def _create_backup(config_path: Path) -> None:
    """Create a timestamped backup of config.json in the backups/ directory."""
    try:
        # Determine the instance name from the config_path.
        # e.g., ~/armactl-data/default/config/config.json -> 'default'
        # config_path.parent == config/
        # config_path.parent.parent == default/
        instance_dir = config_path.parent.parent
        instance_name = instance_dir.name
        
        # We can use P.backups_dir to be sure, though it requires instance name.
        if instance_name:
            backups_dir = P.backups_dir(instance_name)
        else:
            # Fallback if path structure is non-standard
            backups_dir = config_path.parent / "backups"
            
        backups_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = int(time.time())
        backup_name = f"config.json.{timestamp}.bak"
        backup_path = backups_dir / backup_name
        
        shutil.copy2(config_path, backup_path)
        
        # Optional: rotate old backups to avoid filling up disk.
        _rotate_backups(backups_dir)
    except (OSError, Exception) as e:
        raise ConfigError(f"Failed to create config backup: {e}")


def _rotate_backups(backups_dir: Path, max_backups: int = 10) -> None:
    """Keep only the latest `max_backups` files matching config.json.*.bak."""
    backups = list(sorted(backups_dir.glob("config.json.*.bak"), key=os.path.getmtime))
    if len(backups) > max_backups:
        for old_backup in backups[:-max_backups]:
            try:
                old_backup.unlink()
            except OSError:
                pass


def validate_config(config_path: Path | str | None = None, data: dict[str, Any] | None = None) -> list[str]:
    """Validate config content.
    
    Returns a list of error strings. Empty list means the config is valid.
    """
    if data is None and config_path:
        try:
            data = load_config(Path(config_path))
        except ConfigError as e:
            return [str(e)]
    
    if data is None:
        return ["No data provided to validate."]

    errors = []
    
    if not isinstance(data, dict):
        return ["Config root must be a JSON object (dict)."]

    # Check for core sections according to typical Arma Reforger config
    if "bindAddress" not in data:
        errors.append("Missing 'bindAddress' in config.")
    if "bindPort" not in data:
        errors.append("Missing 'bindPort' in config.")
    
    if "game" not in data:
        errors.append("Missing 'game' section in config.")
    elif not isinstance(data["game"], dict):
        errors.append("'game' section must be an object.")
    else:
        # Check game properties
        game = data["game"]
        if "name" not in game:
            errors.append("Missing 'game.name' (server name).")
        if "scenarioId" not in game:
            errors.append("Missing 'game.scenarioId'.")
        if "maxPlayers" not in game:
            errors.append("Missing 'game.maxPlayers'.")
        elif not isinstance(game["maxPlayers"], int):
            errors.append("'game.maxPlayers' must be an integer.")

    return errors


def set_value(config_path: Path | str, section: str, key: str, value: Any) -> None:
    """Set a nested value in the config."""
    config_path = Path(config_path)
    data = load_config(config_path)
    
    if section:
        if section not in data:
            data[section] = {}
        data[section][key] = value
    else:
        data[key] = value

    save_config(config_path, data)
