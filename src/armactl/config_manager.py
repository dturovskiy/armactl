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


def save_config(
    config_path: Path | str,
    data: dict[str, Any],
    backup: bool = True,
    *,
    validate: bool = True,
) -> None:
    """Save config.json atomically after validating server-facing content."""
    config_path = Path(config_path)
    if validate:
        errors = validate_config(data=data)
        if errors:
            raise ConfigError(
                "Refusing to save invalid config: " + "; ".join(errors)
            )

    if backup and config_path.exists():
        _create_backup(config_path)

    tmp_path = config_path.with_suffix(".json.tmp")

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        # Atomic replace (os.replace works across platforms, but this is Linux)
        os.replace(tmp_path, config_path)
    except OSError as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(tr("Failed to save config file: {error}", error=e)) from e


def _create_backup(config_path: Path) -> None:
    """Create a timestamped backup of config.json in the backups/ directory."""
    backup_path: Path | None = None
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

        # Rotate BEFORE creating the new backup to free disk space first.
        # This prevents ENOSPC when the disk is nearly full.
        _rotate_backups(backups_dir, max_backups=9)

        timestamp = int(time.time())
        backup_name = f"config.json.{timestamp}.bak"
        backup_path = backups_dir / backup_name

        shutil.copy2(config_path, backup_path)

        # Rotate again after to enforce the cap with the new backup included.
        _rotate_backups(backups_dir, max_backups=10)
    except Exception as e:
        if backup_path is not None:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ConfigError(tr("Failed to create config backup: {error}", error=e)) from e


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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_port(errors: list[str], path: str, value: Any, *, required: bool = False) -> None:
    if value is None:
        if required:
            errors.append(tr("Missing '{path}'.", path=path))
        return
    if not _is_int(value) or not 1 <= value <= 65535:
        errors.append(tr("'{path}' must be an integer between 1 and 65535.", path=path))


def validate_config(
    config_path: Path | str | None = None,
    data: dict[str, Any] | None = None,
) -> list[str]:
    """Validate the server-facing config without rejecting future upstream keys."""
    if data is None and config_path:
        try:
            data = load_config(Path(config_path))
        except ConfigError as e:
            return [str(e)]

    if data is None:
        return [_('No data provided to validate.')]
    if not isinstance(data, dict):
        return [_('Config root must be a JSON object (dict).')]

    errors: list[str] = []

    bind_address = data.get("bindAddress")
    if bind_address is None:
        errors.append(_("Missing 'bindAddress' in config."))
    elif not isinstance(bind_address, str):
        errors.append(_("'bindAddress' must be a string."))

    _validate_port(errors, "bindPort", data.get("bindPort"), required=True)
    _validate_port(errors, "publicPort", data.get("publicPort"))

    if "publicAddress" in data and not isinstance(data["publicAddress"], str):
        errors.append(_("'publicAddress' must be a string."))

    game = data.get("game")
    if game is None:
        errors.append(_("Missing 'game' section in config."))
    elif not isinstance(game, dict):
        errors.append(_("'game' section must be an object."))
    else:
        if "disabledMods" in game:
            errors.append(
                _(
                    "'game.disabledMods' is armactl metadata and must not be "
                    "written to server config. Run repair or the disabled-mods "
                    "migration."
                )
            )
        admins = game.get("admins", [])
        if not isinstance(admins, list):
            errors.append(_("'game.admins' must be a list."))
        else:
            if len(admins) > 20:
                errors.append(_("'game.admins' supports at most 20 unique IDs."))
            seen_admin_ids: set[str] = set()
            for index, admin_id in enumerate(admins):
                if not isinstance(admin_id, str) or not admin_id.strip():
                    errors.append(
                        tr(
                            "'game.admins[{index}]' must be a non-empty "
                            "IdentityId or SteamID string.",
                            index=index,
                        )
                    )
                    continue
                key = admin_id.strip().upper()
                if key in seen_admin_ids:
                    errors.append(
                        tr(
                            "Duplicate admin ID in 'game.admins': {admin_id}.",
                            admin_id=admin_id,
                        )
                    )
                seen_admin_ids.add(key)

        for key in ("name", "scenarioId"):
            value = game.get(key)
            if value is None:
                errors.append(tr("Missing 'game.{key}'.", key=key))
            elif not isinstance(value, str):
                errors.append(tr("'game.{key}' must be a string.", key=key))

        max_players = game.get("maxPlayers")
        if max_players is None:
            errors.append(_("Missing 'game.maxPlayers'."))
        elif not _is_int(max_players) or max_players <= 0:
            errors.append(_("'game.maxPlayers' must be a positive integer."))

        mods = game.get("mods", [])
        if not isinstance(mods, list):
            errors.append(_("'game.mods' must be a list."))
        else:
            seen_mod_ids: set[str] = set()
            for index, mod in enumerate(mods):
                if not isinstance(mod, dict):
                    errors.append(tr("'game.mods[{index}]' must be an object.", index=index))
                    continue
                mod_id = str(mod.get("modId") or "").strip()
                if not mod_id:
                    errors.append(tr("'game.mods[{index}].modId' is required.", index=index))
                    continue
                key = mod_id.upper()
                if key in seen_mod_ids:
                    errors.append(tr("Duplicate mod ID in 'game.mods': {mod_id}.", mod_id=mod_id))
                seen_mod_ids.add(key)

    for section in ("a2s", "rcon"):
        value = data.get(section)
        if value is None:
            continue
        if not isinstance(value, dict):
            errors.append(tr("'{section}' must be an object.", section=section))
            continue
        _validate_port(errors, f"{section}.port", value.get("port"))

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
