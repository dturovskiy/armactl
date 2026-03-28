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

from armactl.i18n import _, tr


class ConfigError(Exception):
    """Raised when there's an error reading/writing/validating config."""
    pass


def load_config(config_path: Path | str) -> dict[str, Any]:
    """Load config.json from disk."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(tr("Config file not found: {path}", path=config_path))

    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(tr("Invalid JSON in config file: {error}", error=e))
    except OSError as e:
        raise ConfigError(tr("Failed to read config file: {error}", error=e))


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
        raise ConfigError(tr("Failed to save config file: {error}", error=e))


def _create_backup(config_path: Path) -> None:
    """Create a timestamped backup of config.json in the backups/ directory."""
    try:
        # Standard layout:
        #   <instance>/config/config.json -> <instance>/backups/
        # Non-standard layout:
        #   <dir>/config.json -> <dir>/backups/
        if config_path.parent.name == "config":
            backups_dir = config_path.parent.parent / "backups"
        else:
            backups_dir = config_path.parent / "backups"

        backups_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        backup_name = f"config.json.{timestamp}.bak"
        backup_path = backups_dir / backup_name

        shutil.copy2(config_path, backup_path)

        # Optional: rotate old backups to avoid filling up disk.
        _rotate_backups(backups_dir)
    except (OSError, Exception) as e:
        raise ConfigError(tr("Failed to create config backup: {error}", error=e))


def _rotate_backups(backups_dir: Path, max_backups: int = 10) -> None:
    """Keep only the latest `max_backups` files matching config.json.*.bak."""
    backups = list(sorted(backups_dir.glob("config.json.*.bak"), key=os.path.getmtime))
    if len(backups) > max_backups:
        num_to_delete = len(backups) - max_backups
        for old_backup in backups[:num_to_delete]:
            try:
                old_backup.unlink()
            except OSError:
                pass


def validate_config(
    config_path: Path | str | None = None,
    data: dict[str, Any] | None = None,
) -> list[str]:
    """Validate config content.

    Returns a list of error strings. Empty list means the config is valid.
    """
    if data is None and config_path:
        try:
            data = load_config(Path(config_path))
        except ConfigError as e:
            return [str(e)]

    if data is None:
        return [_("No data provided to validate.")]

    errors = []

    if not isinstance(data, dict):
        return [_("Config root must be a JSON object (dict).")]

    # Check for core sections according to typical Arma Reforger config
    if "bindAddress" not in data:
        errors.append(_("Missing 'bindAddress' in config."))
    if "bindPort" not in data:
        errors.append(_("Missing 'bindPort' in config."))

    if "game" not in data:
        errors.append(_("Missing 'game' section in config."))
    elif not isinstance(data["game"], dict):
        errors.append(_("'game' section must be an object."))
    else:
        # Check game properties
        game = data["game"]
        if "name" not in game:
            errors.append(_("Missing 'game.name' (server name)."))
        if "scenarioId" not in game:
            errors.append(_("Missing 'game.scenarioId'."))
        if "maxPlayers" not in game:
            errors.append(_("Missing 'game.maxPlayers'."))
        elif not isinstance(game["maxPlayers"], int):
            errors.append(_("'game.maxPlayers' must be an integer."))

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


def unset_value(config_path: Path | str, section: str, key: str) -> None:
    """Unset a nested value in the config."""
    config_path = Path(config_path)
    data = load_config(config_path)

    deleted = False
    if section:
        if section in data and isinstance(data[section], dict) and key in data[section]:
            data[section].pop(key, None)
            deleted = True
    else:
        if key in data:
            data.pop(key, None)
            deleted = True

    if deleted:
        save_config(config_path, data)
    else:
        raise ConfigError(tr("Key '{key}' not found in config.", key=key))
