"""Game-admin page DTO loader."""

from __future__ import annotations

from typing import Any

from armactl import admin_acl_sync, admins_manager, config_manager, sat_admin_guard
from armactl.web.page_models.common import (
    UNAVAILABLE_LABEL,
    _basename_display,
    _discover_management_state,
    _missing_config_page,
    _paths,
    _safe_error_message,
    _state_status,
)


def _admin_identity(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        identity = (
            raw.get("identityId")
            or raw.get("steamId64")
            or raw.get("steamid")
            or raw.get("playerId")
            or raw.get("uid")
            or raw.get("id")
            or ""
        )
        return {
            "identity_id": str(identity or "").strip(),
            "name": str(raw.get("name") or raw.get("displayName") or "").strip(),
            "source": str(raw.get("source") or "game.admins").strip(),
        }
    return {
        "identity_id": str(raw or "").strip(),
        "name": "",
        "source": "game.admins",
    }


def _admin_key(entry: dict[str, str]) -> str:
    return entry.get("identity_id", "").upper()


def load_admins_page(instance: str) -> dict[str, Any]:
    """Return official game admin IDs plus optional local labels without migration."""
    state, error_page = _discover_management_state(instance)
    if error_page is not None:
        return error_page
    assert state is not None
    if not state.config_path:
        return _missing_config_page(instance, state, "config path is not available")

    try:
        config = config_manager.load_config(state.config_path)
        game = config.get("game", {}) if isinstance(config.get("game"), dict) else {}
        raw_admins = game.get("admins", [])
        if raw_admins in (None, ""):
            raw_admins = []
        if not isinstance(raw_admins, list):
            raise ValueError("game.admins must be a list")
        official = [_admin_identity(item) for item in raw_admins]
    except Exception as error:
        return _missing_config_page(instance, state, _safe_error_message(error))

    sidecar_path = UNAVAILABLE_LABEL
    local_labels: list[dict[str, str]] = []
    label_error = ""
    try:
        state_path = admins_manager.admins_state_path_for_config(state.config_path)
        local_labels = [
            {
                "identity_id": str(item.get("identityId") or "").strip(),
                "name": str(item.get("name") or "").strip(),
                "source": str(item.get("source") or "").strip(),
            }
            for item in admins_manager.load_admins(state.config_path)
        ]
        sidecar_path = _basename_display(state_path, "admins-state.json")
    except Exception as error:
        label_error = _safe_error_message(error)

    labels_by_key = {_admin_key(item): item for item in local_labels if _admin_key(item)}
    enriched: list[dict[str, str]] = []
    for entry in official:
        label = labels_by_key.get(_admin_key(entry))
        if label is not None:
            entry = {
                **entry,
                "name": label.get("name") or entry.get("name", ""),
                "source": label.get("source") or entry.get("source", ""),
            }
        enriched.append(entry)

    # RCON reports an IdentityId even when game.admins stores a SteamID64.
    # Only the explicit SteamID -> UUID mapping is trusted for roster matching;
    # display names are not stable or unique player identities.
    official_admin_references = [entry["identity_id"] for entry in enriched]
    try:
        uuid_map = sat_admin_guard.load_sat_uuid_map(state.config_path)
    except sat_admin_guard.SatAdminGuardError:
        uuid_map = {}
    for entry in enriched:
        identity = entry["identity_id"]
        if admins_manager.STEAM_ID64_RE.fullmatch(identity):
            mapped_uuid = uuid_map.get(identity.casefold())
            if mapped_uuid:
                official_admin_references.append(mapped_uuid)

    permission_sync: dict[str, Any]
    try:
        permission_sync = admin_acl_sync.inspect_admin_acls(state.config_path).to_dict()
    except Exception:
        permission_sync = {
            "available": False,
            "synchronized": False,
            "desired_admin_count": len(enriched),
            "missing_mapping_count": 0,
            "roles": [],
            "error": "Admin permission status is unavailable.",
        }

    return {
        "instance": instance,
        "available": True,
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "official_admins": enriched,
        "official_admin_references": official_admin_references,
        "official_count": len(enriched),
        "local_labels": local_labels,
        "local_label_count": len(local_labels),
        "local_labels_path": sidecar_path,
        "local_labels_path_display": sidecar_path,
        "local_labels_error": label_error,
        "permission_sync": permission_sync,
    }
