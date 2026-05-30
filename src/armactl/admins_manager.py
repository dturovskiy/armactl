"""Admin identity management for Arma Reforger config.json.

The exact server-side interpretation belongs to Arma Reforger. armactl keeps
the UI isolated behind this module so the JSON key can be adjusted in one place
if Bohemia changes the schema.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from armactl.config_manager import ConfigError, load_config, save_config
from armactl.i18n import _

ADMINS_KEY = "admins"
MAX_ADMINS = 20
ADMIN_ID_RE = re.compile(r"^[0-9A-Fa-f{}\-]{16,80}$")


def _normalize_admin_id(admin_id: str) -> str:
    value = str(admin_id or "").strip()
    if not ADMIN_ID_RE.fullmatch(value):
        raise ConfigError(
            _("Admin ID must look like a Steam/Bohemia identity, UUID, or hex player ID.")
        )
    return value.upper()


def _admin_identity(admin: Any) -> str:
    if isinstance(admin, dict):
        value = (
            admin.get("identityId")
            or admin.get("playerId")
            or admin.get("uid")
            or admin.get("id")
            or ""
        )
    else:
        value = admin
    return _normalize_admin_id(str(value))


def _load_admin_ids(game: dict[str, Any]) -> list[str]:
    admins = game.get(ADMINS_KEY, [])
    if not isinstance(admins, list):
        raise ConfigError(_("game.admins must be a list."))

    admin_ids: list[str] = []
    seen: set[str] = set()
    for admin in admins:
        identity = _admin_identity(admin)
        if identity in seen:
            continue
        admin_ids.append(identity)
        seen.add(identity)
    return admin_ids


def _admin_entries(admin_ids: list[str]) -> list[dict[str, str]]:
    return [{"identityId": admin_id} for admin_id in admin_ids]


def get_admins(config_path: Path | str) -> list[dict[str, Any]]:
    """Return configured admin identities from game.admins."""
    config = load_config(config_path)
    game = config.get("game", {})
    return _admin_entries(_load_admin_ids(game))


def add_admin(config_path: Path | str, admin_id: str, name: str = "") -> bool:
    """Add or update an admin identity. Returns True when added."""
    config = load_config(config_path)
    game = config.setdefault("game", {})
    admin_ids = _load_admin_ids(game)
    normalized = _normalize_admin_id(admin_id)

    if normalized in admin_ids:
        game[ADMINS_KEY] = admin_ids
        save_config(config_path, config)
        return False

    if len(admin_ids) >= MAX_ADMINS:
        raise ConfigError(_("game.admins is limited to 20 unique IDs."))

    admin_ids.append(normalized)
    game[ADMINS_KEY] = admin_ids
    save_config(config_path, config)
    return True


def remove_admin(config_path: Path | str, admin_id: str) -> bool:
    """Remove an admin identity. Returns True when removed."""
    config = load_config(config_path)
    game = config.setdefault("game", {})
    admin_ids = _load_admin_ids(game)
    normalized = _normalize_admin_id(admin_id)
    new_admin_ids = [identity for identity in admin_ids if identity != normalized]

    if len(new_admin_ids) == len(admin_ids):
        return False

    game[ADMINS_KEY] = new_admin_ids
    save_config(config_path, config)
    return True
