"""Arma Reforger server-admin management with local display-label metadata.

The dedicated-server schema officially supports ``game.admins`` as an array of
IdentityIds and/or Steam IDs. Those IDs remain in config.json so the game
server can apply passwordless ``#login`` and related admin behaviour.

armactl keeps optional human-friendly labels in an instance-scoped sidecar.
The sidecar never replaces the official server-facing ACL.
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from armactl.config_manager import ConfigError, load_config, save_config

ADMINS_STATE_FILENAME = "admins-state.json"
ADMINS_KEY = "admins"
MAX_ADMINS = 20
STEAM_WEB_API_KEY_ENV = "STEAM_WEB_API_KEY"
STEAM_ID64_RE = re.compile(r"^[0-9]{17}$")
IDENTITY_ID_RE = re.compile(r"^[0-9A-Fa-f{}\-]{16,80}$")
VANITY_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")


@dataclass(frozen=True)
class AdminsMigrationResult:
    migrated: bool
    legacy_entries: int
    restored_from_sidecar: int
    sidecar_path: Path


@dataclass(frozen=True)
class ResolvedSteamIdentity:
    identity_id: str
    display_name: str = ""
    source: str = ""


def admins_state_path_for_config(config_path: Path | str) -> Path:
    """Return the instance-scoped local admin-label sidecar path."""
    config_path = Path(config_path)
    if config_path.name != "config.json":
        raise ConfigError(f"Expected config.json path, got: {config_path}")
    if config_path.parent.name == "config":
        return config_path.parent.parent / ADMINS_STATE_FILENAME
    return config_path.parent / ADMINS_STATE_FILENAME


def _identity_value(item: Any) -> str:
    if isinstance(item, dict):
        value = (
            item.get("identityId")
            or item.get("steamId64")
            or item.get("steamid")
            or item.get("playerId")
            or item.get("uid")
            or item.get("id")
            or ""
        )
    else:
        value = item
    return str(value or "").strip()


def _normalize_entry(item: Any, *, default_source: str = "") -> dict[str, str]:
    identity = _identity_value(item)
    if not identity:
        raise ConfigError("Admin entry is missing an IdentityId or SteamID.")
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("displayName") or "").strip()
        source = str(item.get("source") or default_source).strip()
    else:
        name = ""
        source = default_source
    return {"identityId": identity, "name": name, "source": source}


def _entry_key(item: Any) -> str:
    return _identity_value(item).upper()


def merge_admins(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge entries while preserving order and updating labels by stable ID."""
    merged: list[dict[str, str]] = []
    index_by_key: dict[str, int] = {}
    for group in groups:
        for raw in group:
            entry = _normalize_entry(raw)
            key = _entry_key(entry)
            if not key:
                continue
            if key in index_by_key:
                existing = merged[index_by_key[key]]
                if entry.get("name"):
                    existing["name"] = entry["name"]
                if entry.get("source"):
                    existing["source"] = entry["source"]
                continue
            index_by_key[key] = len(merged)
            merged.append(entry)
    return merged


def _server_admin_entries(config: dict[str, Any]) -> list[dict[str, str]]:
    game = config.get("game", {})
    if not isinstance(game, dict):
        raise ConfigError("game section must be a JSON object.")
    raw_admins = game.get(ADMINS_KEY, [])
    if raw_admins in (None, ""):
        raw_admins = []
    if not isinstance(raw_admins, list):
        raise ConfigError("game.admins must be a JSON list.")
    return [_normalize_entry(item, default_source="game.admins") for item in raw_admins]


def _server_admin_ids(entries: list[dict[str, str]]) -> list[str]:
    return [entry["identityId"] for entry in merge_admins(entries)]


def load_admins(config_path: Path | str) -> list[dict[str, str]]:
    """Load optional local admin labels from admins-state.json."""
    state_path = admins_state_path_for_config(config_path)
    if not state_path.is_file():
        return []
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {state_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Failed to read {state_path}: {exc}") from exc
    if isinstance(payload, list):
        raw_admins = payload
    elif isinstance(payload, dict):
        raw_admins = payload.get("admins", [])
    else:
        raise ConfigError(f"{state_path} must contain a JSON object or list.")
    if not isinstance(raw_admins, list):
        raise ConfigError(f"{state_path}: admins must be a list.")
    return merge_admins([_normalize_entry(item) for item in raw_admins])


def save_admins(config_path: Path | str, admins: list[dict[str, str]]) -> Path:
    """Atomically store local display labels with private file permissions."""
    state_path = admins_state_path_for_config(config_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "scope": "armactl-admin-labels",
        "admins": merge_admins(admins),
    }
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, state_path)
        state_path.chmod(0o600)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"Failed to save {state_path}: {exc}") from exc
    return state_path


def _restore_admins_sidecar(
    state_path: Path,
    original_admins: list[dict[str, str]],
    *,
    originally_existed: bool,
) -> None:
    if originally_existed:
        payload = {
            "version": 1,
            "scope": "armactl-admin-labels",
            "admins": merge_admins(original_admins),
        }
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, state_path)
        state_path.chmod(0o600)
        return

    state_path.unlink(missing_ok=True)


def _rollback_config(
    config_path: Path | str,
    original_config: dict[str, Any],
    original_error: Exception,
) -> None:
    try:
        save_config(config_path, original_config, backup=False)
    except Exception as rollback_error:
        raise ConfigError(
            f"Failed to roll back config after admin sidecar save error: {rollback_error}"
        ) from original_error


def _save_config_then_admins(
    config_path: Path | str,
    config: dict[str, Any],
    admins: list[dict[str, str]],
    *,
    original_config: dict[str, Any],
) -> None:
    save_config(config_path, config)
    try:
        save_admins(config_path, admins)
    except Exception as error:
        _rollback_config(config_path, original_config, error)
        raise


def migrate_legacy_admins(config_path: Path | str) -> AdminsMigrationResult:
    """Normalize official game.admins and restore IDs removed by older armactl builds."""
    config_path = Path(config_path)
    state_path = admins_state_path_for_config(config_path)
    sidecar_existed = state_path.exists()
    config = load_config(config_path)
    game = config.setdefault("game", {})
    if not isinstance(game, dict):
        raise ConfigError("game section must be a JSON object.")

    had_server_key = ADMINS_KEY in game
    raw_server = game.get(ADMINS_KEY, [])
    if raw_server in (None, ""):
        raw_server = []
    if not isinstance(raw_server, list):
        raise ConfigError("game.admins must be a JSON list.")

    server_entries = [_normalize_entry(item, default_source="game.admins") for item in raw_server]
    sidecar_entries = load_admins(config_path)
    merged = merge_admins(server_entries, sidecar_entries)
    official_ids = _server_admin_ids(merged)
    restored_from_sidecar = len(
        {entry["identityId"].upper() for entry in merged}
        - {entry["identityId"].upper() for entry in server_entries}
    )

    should_keep_key = had_server_key or bool(official_ids)
    changed_config = False
    if should_keep_key:
        if raw_server != official_ids:
            game[ADMINS_KEY] = official_ids
            changed_config = True
    elif ADMINS_KEY in game:
        game.pop(ADMINS_KEY, None)
        changed_config = True

    changed_sidecar = sidecar_entries != merged
    if merged or state_path.exists():
        save_admins(config_path, merged)

    if changed_config:
        try:
            save_config(config_path, config, backup=True)
        except Exception:
            _restore_admins_sidecar(
                state_path,
                sidecar_entries,
                originally_existed=sidecar_existed,
            )
            raise

    return AdminsMigrationResult(
        migrated=changed_config or changed_sidecar,
        legacy_entries=len(server_entries),
        restored_from_sidecar=restored_from_sidecar,
        sidecar_path=state_path,
    )


def _open_text(url: str, *, timeout: float = 6.0) -> str:
    request = Request(url, headers={"User-Agent": "armactl/steam-admin-resolver"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ConfigError(f"Steam profile lookup failed: {exc}") from exc


def _resolve_vanity_with_web_api(vanity: str, api_key: str) -> ResolvedSteamIdentity:
    query = urlencode({"key": api_key, "vanityurl": vanity})
    text = _open_text(f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/?{query}")
    try:
        payload = json.loads(text)
        response = payload.get("response", {})
        steamid = str(response.get("steamid") or "").strip()
    except (AttributeError, json.JSONDecodeError) as exc:
        raise ConfigError("Steam Web API returned an invalid ResolveVanityURL response.") from exc
    if not STEAM_ID64_RE.fullmatch(steamid):
        raise ConfigError(f"Steam vanity slug '{vanity}' was not resolved to a SteamID64.")
    return ResolvedSteamIdentity(steamid, source=f"steam vanity:{vanity}")


def _resolve_vanity_with_public_profile(vanity: str) -> ResolvedSteamIdentity:
    """Best-effort fallback for public vanity profile slugs without an API key."""
    text = _open_text(f"https://steamcommunity.com/id/{quote(vanity)}/?xml=1")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ConfigError("Steam Community returned an invalid profile response.") from exc
    steamid = str(root.findtext("steamID64") or "").strip()
    display_name = str(root.findtext("steamID") or "").strip()
    if not STEAM_ID64_RE.fullmatch(steamid):
        raise ConfigError(f"Steam vanity slug '{vanity}' was not resolved to a SteamID64.")
    return ResolvedSteamIdentity(
        steamid,
        display_name=display_name,
        source=f"steam vanity:{vanity}",
    )


def resolve_steam_identity(reference: str, *, api_key: str | None = None) -> ResolvedSteamIdentity:
    """Resolve SteamID64, IdentityId, profile URL, or vanity slug to a stable ID."""
    value = str(reference or "").strip()
    if not value:
        raise ConfigError("Steam profile reference is required.")

    if STEAM_ID64_RE.fullmatch(value):
        return ResolvedSteamIdentity(value, source="steamid64")
    if IDENTITY_ID_RE.fullmatch(value):
        return ResolvedSteamIdentity(value.upper(), source="identityId")

    parsed = urlparse(value if "://" in value else f"https://steamcommunity.com/id/{value}")
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in {"steamcommunity.com", "www.steamcommunity.com"}:
        raise ConfigError(
            "Paste a SteamID64, IdentityId, steamcommunity.com profile URL, "
            "or Steam vanity slug."
        )

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "profiles" and STEAM_ID64_RE.fullmatch(parts[1]):
        return ResolvedSteamIdentity(parts[1], source="steam profile URL")

    vanity = ""
    if len(parts) >= 2 and parts[0].lower() == "id":
        vanity = parts[1]
    elif "://" not in value:
        vanity = value

    if not VANITY_RE.fullmatch(vanity):
        raise ConfigError("Steam nickname lookup is ambiguous. Paste the profile URL or SteamID64.")

    configured_key = str(api_key or os.environ.get(STEAM_WEB_API_KEY_ENV, "")).strip()
    if configured_key:
        return _resolve_vanity_with_web_api(vanity, configured_key)
    return _resolve_vanity_with_public_profile(vanity)


def get_admins(config_path: Path | str) -> list[dict[str, str]]:
    """Return official server admins enriched with optional local labels."""
    migrate_legacy_admins(config_path)
    config = load_config(config_path)
    server_entries = _server_admin_entries(config)
    metadata = load_admins(config_path)
    return merge_admins(server_entries, metadata)


def add_admin(config_path: Path | str, admin_reference: str, name: str = "") -> bool:
    """Add or update an official game.admins entry and its optional local label."""
    migrate_legacy_admins(config_path)
    config = load_config(config_path)
    original_config = copy.deepcopy(config)
    game = config.setdefault("game", {})
    admins = get_admins(config_path)
    resolved = resolve_steam_identity(admin_reference)
    key = resolved.identity_id.upper()
    label = str(name or resolved.display_name or "").strip()
    entry = {"identityId": resolved.identity_id, "name": label, "source": resolved.source}

    created = all(_entry_key(existing) != key for existing in admins)
    updated = merge_admins(admins, [entry])
    if created and len(updated) > MAX_ADMINS:
        raise ConfigError(f"game.admins is limited to {MAX_ADMINS} unique IDs.")

    game[ADMINS_KEY] = _server_admin_ids(updated)
    _save_config_then_admins(
        config_path,
        config,
        updated,
        original_config=original_config,
    )
    return created


def remove_admin(config_path: Path | str, admin_reference: str) -> bool:
    """Remove an official server admin by stable ID, stored source, or local label."""
    migrate_legacy_admins(config_path)
    admins = get_admins(config_path)
    raw = str(admin_reference or "").strip()
    if not raw:
        return False
    raw_upper = raw.upper()

    remaining = [
        entry
        for entry in admins
        if raw_upper
        not in {
            _entry_key(entry),
            str(entry.get("source") or "").strip().upper(),
            str(entry.get("name") or "").strip().upper(),
        }
    ]
    if len(remaining) == len(admins):
        return False

    config = load_config(config_path)
    original_config = copy.deepcopy(config)
    game = config.setdefault("game", {})
    game[ADMINS_KEY] = _server_admin_ids(remaining)
    _save_config_then_admins(
        config_path,
        config,
        remaining,
        original_config=original_config,
    )
    return True
