"""Mod manager - handle mods configuration in config.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from armactl.addon_cleanup import (
    CleanupResult,
    cleanup_addons_by_mod_ids,
    is_enospc,
    normalize_mod_id,
)
from armactl.config_manager import ConfigError, load_config, save_config
from armactl.i18n import _, tr
from armactl.mods_state import (
    load_disabled_mods,
    migrate_legacy_disabled_mods,
    save_disabled_mods,
)


@dataclass
class ModUpdateResult:
    """Details for a config mod-list update and any addon cleanup."""

    config_changed: bool
    cleanup_result: CleanupResult | None = None
    enospc_retry_performed: bool = False
    removed_ids: set[str] = field(default_factory=set)


def _merge_cleanup_results(target: CleanupResult | None, source: CleanupResult) -> CleanupResult:
    """Merge cleanup metadata into a single result object."""
    if target is None:
        target = CleanupResult()
    target.deleted.extend(source.deleted)
    target.skipped.extend(source.skipped)
    target.errors.extend(source.errors)
    target.bytes_deleted += source.bytes_deleted
    return target


def _mod_id_matches(mod: dict[str, Any], target_id: str | None, raw_mod_id: str) -> bool:
    if target_id is not None:
        return normalize_mod_id(mod.get("modId")) == target_id
    return str(mod.get("modId") or "") == raw_mod_id


def _pop_mod_by_id(
    mods: list[dict[str, Any]],
    mod_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    target_id = normalize_mod_id(mod_id)
    remaining: list[dict[str, Any]] = []
    removed: dict[str, Any] | None = None
    for mod in mods:
        if _mod_id_matches(mod, target_id, mod_id):
            if removed is None:
                removed = mod
            continue
        remaining.append(mod)
    return remaining, removed


def get_mods(config_path: Path | str) -> list[dict[str, Any]]:
    """Return the list of mods from config."""
    config = load_config(config_path)
    game = config.get("game", {})
    return game.get("mods", [])


def get_disabled_mods(config_path: Path | str) -> list[dict[str, Any]]:
    """Return armactl-only disabled mods from the instance sidecar."""
    migrate_legacy_disabled_mods(config_path)
    return load_disabled_mods(config_path)


def _mod_ids(mods: list[dict[str, Any]]) -> set[str]:
    """Return valid normalized mod IDs from a mod list."""
    ids: set[str] = set()
    for mod in mods:
        mod_id = normalize_mod_id(mod.get("modId"))
        if mod_id is not None:
            ids.add(mod_id)
    return ids


def _mod_key(mod_id: Any) -> str:
    """Return a stable comparison key for valid and legacy mod IDs."""
    value = str(mod_id or "").strip()
    return normalize_mod_id(value) or value


def save_mods_with_removed_addon_cleanup(
    config_path: Path | str,
    config: dict[str, Any],
    old_mods: list[dict[str, Any]],
    new_mods: list[dict[str, Any]],
    *,
    cleanup_removed: bool = True,
) -> ModUpdateResult:
    """Save active mods and safely clean addons no longer referenced anywhere."""
    migrate_legacy_disabled_mods(config_path)
    old_ids = _mod_ids(old_mods)
    new_ids = _mod_ids(new_mods)

    game = config.setdefault("game", {})
    game.pop("disabledMods", None)
    disabled_ids = _mod_ids(load_disabled_mods(config_path))
    removed_ids = (old_ids - new_ids) - disabled_ids
    game["mods"] = new_mods

    cleanup_result: CleanupResult | None = None
    enospc_retry_performed = False

    try:
        save_config(config_path, config)
    except Exception as exc:
        if not (cleanup_removed and removed_ids and is_enospc(exc)):
            raise

        enospc_retry_performed = True
        cleanup_result = cleanup_addons_by_mod_ids(config_path, removed_ids)

        try:
            save_config(config_path, config)
        except Exception as retry_exc:
            raise ConfigError(
                tr(
                    "Failed to save config after freeing addon files for removed mods: {error}",
                    error=retry_exc,
                )
            ) from retry_exc

    if cleanup_removed and removed_ids and cleanup_result is None:
        cleanup_result = cleanup_addons_by_mod_ids(config_path, removed_ids)
    elif cleanup_removed and cleanup_result is None:
        cleanup_result = CleanupResult()

    return ModUpdateResult(
        config_changed=True,
        cleanup_result=cleanup_result,
        enospc_retry_performed=enospc_retry_performed,
        removed_ids=removed_ids,
    )


def set_mods(
    config_path: Path | str,
    mods_list: list[dict[str, Any]],
    *,
    _cleanup_removed: bool = True,
) -> None:
    """Save a new list of mods to config.

    If *_cleanup_removed* is True, addon directories for mod IDs that
    existed before but are no longer present will be deleted.
    """
    set_mods_detailed(config_path, mods_list, _cleanup_removed=_cleanup_removed)


def set_mods_detailed(
    config_path: Path | str,
    mods_list: list[dict[str, Any]],
    *,
    _cleanup_removed: bool = True,
) -> ModUpdateResult:
    """Save a new active list and return cleanup metadata."""
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    old_mods = list(config.get("game", {}).get("mods", []))
    return save_mods_with_removed_addon_cleanup(
        config_path,
        config,
        old_mods,
        mods_list,
        cleanup_removed=_cleanup_removed,
    )


def add_mod(
    config_path: Path | str,
    mod_id: str,
    name: str = "",
    version: str = "",
) -> bool:
    """Add or reactivate a mod. Returns True if added, False if already active."""
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    game = config.setdefault("game", {})
    mods = list(game.get("mods", []))
    disabled_mods = load_disabled_mods(config_path)
    disabled_mods, disabled_entry = _pop_mod_by_id(disabled_mods, mod_id)
    target_id = normalize_mod_id(mod_id)

    for mod in mods:
        if _mod_id_matches(mod, target_id, mod_id):
            if name:
                mod["name"] = name
            if version:
                mod["version"] = version
            save_disabled_mods(config_path, disabled_mods)
            save_config(config_path, config)
            return False

    if disabled_entry is not None:
        if name:
            disabled_entry["name"] = name
        if version:
            disabled_entry["version"] = version
        disabled_entry["modId"] = str(disabled_entry.get("modId") or mod_id).upper()
        mods.append(disabled_entry)
        game["mods"] = mods
        save_config(config_path, config)
        save_disabled_mods(config_path, disabled_mods)
        return True

    mods.append({"modId": mod_id, "name": name, "version": version})
    game["mods"] = mods
    save_config(config_path, config)
    return True


def disable_mod(config_path: Path | str, mod_id: str) -> bool:
    """Move a mod from game.mods into the armactl-only sidecar."""
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    game = config.setdefault("game", {})
    active_mods = list(game.get("mods", []))
    disabled_mods = load_disabled_mods(config_path)

    active_mods, removed = _pop_mod_by_id(active_mods, mod_id)
    if removed is None:
        return False

    removed_id = normalize_mod_id(removed.get("modId"))
    if removed_id not in _mod_ids(disabled_mods):
        disabled_mods.append(removed)

    # Save sidecar first so local addon cleanup never sees the disabled mod as orphaned.
    save_disabled_mods(config_path, disabled_mods)
    game["mods"] = active_mods
    game.pop("disabledMods", None)
    save_config(config_path, config)
    return True


def enable_mod(config_path: Path | str, mod_id: str) -> bool:
    """Move a disabled mod from the sidecar back into game.mods."""
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    game = config.setdefault("game", {})
    active_mods = list(game.get("mods", []))
    disabled_mods = load_disabled_mods(config_path)

    disabled_mods, restored = _pop_mod_by_id(disabled_mods, mod_id)
    if restored is None:
        return False

    restored_id = normalize_mod_id(restored.get("modId"))
    if restored_id not in _mod_ids(active_mods):
        active_mods.append(restored)

    game["mods"] = active_mods
    game.pop("disabledMods", None)
    save_config(config_path, config)
    save_disabled_mods(config_path, disabled_mods)
    return True


def remove_mod(config_path: Path | str, mod_id: str) -> bool:
    """Remove a mod from config by ID and clean up its addon directory.

    Returns True if the mod was found and removed.
    """
    return remove_mod_detailed(config_path, mod_id).config_changed


def remove_mod_detailed(config_path: Path | str, mod_id: str) -> ModUpdateResult:
    """Remove an active or disabled mod and clean up its local addon directory."""
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    game = config.setdefault("game", {})
    mods = list(game.get("mods", []))
    disabled_mods = load_disabled_mods(config_path)
    target_id = normalize_mod_id(mod_id)
    if target_id is None:
        new_mods = [mod for mod in mods if mod.get("modId") != mod_id]
        new_disabled_mods = [mod for mod in disabled_mods if mod.get("modId") != mod_id]
    else:
        new_mods = [mod for mod in mods if normalize_mod_id(mod.get("modId")) != target_id]
        new_disabled_mods = [
            mod for mod in disabled_mods if normalize_mod_id(mod.get("modId")) != target_id
        ]

    if len(new_mods) == len(mods) and len(new_disabled_mods) == len(disabled_mods):
        return ModUpdateResult(config_changed=False)

    save_disabled_mods(config_path, new_disabled_mods)
    result = save_mods_with_removed_addon_cleanup(config_path, config, mods, new_mods)

    disabled_removed_ids = _mod_ids(disabled_mods) - _mod_ids(new_disabled_mods)
    extra_cleanup_ids = disabled_removed_ids - result.removed_ids
    if extra_cleanup_ids:
        cleanup_result = cleanup_addons_by_mod_ids(config_path, extra_cleanup_ids)
        result.cleanup_result = _merge_cleanup_results(result.cleanup_result, cleanup_result)
        result.removed_ids.update(extra_cleanup_ids)

    return result


def clear_mods(config_path: Path | str) -> int:
    """Remove all active and disabled mods and clean up their addon directories."""
    mods = get_mods(config_path)
    disabled_mods = get_disabled_mods(config_path)
    if not mods and not disabled_mods:
        return 0
    clear_mods_detailed(config_path)
    return len(mods) + len(disabled_mods)


def clear_mods_detailed(config_path: Path | str) -> ModUpdateResult:
    """Remove all mods and return cleanup metadata."""
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    game = config.setdefault("game", {})
    mods = list(game.get("mods", []))
    disabled_mods = load_disabled_mods(config_path)
    if not mods and not disabled_mods:
        return ModUpdateResult(config_changed=False)
    save_disabled_mods(config_path, [])
    result = save_mods_with_removed_addon_cleanup(config_path, config, mods, [])
    extra_cleanup_ids = _mod_ids(disabled_mods) - result.removed_ids
    if extra_cleanup_ids:
        result.cleanup_result = _merge_cleanup_results(
            result.cleanup_result,
            cleanup_addons_by_mod_ids(config_path, extra_cleanup_ids),
        )
        result.removed_ids.update(extra_cleanup_ids)
    return result


def dedupe_mods(config_path: Path | str) -> int:
    """Remove duplicate mods across active config and disabled sidecar."""
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    game = config.setdefault("game", {})
    mods = list(game.get("mods", []))
    disabled_mods = load_disabled_mods(config_path)
    seen: set[str] = set()
    deduped_mods: list[dict[str, Any]] = []
    deduped_disabled_mods: list[dict[str, Any]] = []

    for mod in mods:
        mod_key = _mod_key(mod.get("modId"))
        if not mod_key or mod_key in seen:
            continue
        seen.add(mod_key)
        deduped_mods.append(mod)

    for mod in disabled_mods:
        mod_key = _mod_key(mod.get("modId"))
        if not mod_key or mod_key in seen:
            continue
        seen.add(mod_key)
        deduped_disabled_mods.append(mod)

    duplicates_removed = (
        len(mods)
        + len(disabled_mods)
        - len(deduped_mods)
        - len(deduped_disabled_mods)
    )
    if duplicates_removed > 0:
        game["mods"] = deduped_mods
        game.pop("disabledMods", None)
        save_config(config_path, config)
        save_disabled_mods(config_path, deduped_disabled_mods)

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
    added_count, skipped_count, _ = import_mods_detailed(
        config_path,
        import_file,
        append=append,
    )
    return added_count, skipped_count


def import_mods_detailed(
    config_path: Path | str,
    import_file: Path | str,
    append: bool = False,
) -> tuple[int, int, ModUpdateResult]:
    """Import active mods and reactivate matching disabled sidecar entries."""
    imported_mods = _load_import_mods(import_file)
    migrate_legacy_disabled_mods(config_path)
    config = load_config(config_path)
    game = config.setdefault("game", {})
    old_mods = list(game.get("mods", []))
    disabled_mods = load_disabled_mods(config_path)
    current_mods = list(old_mods) if append else []
    seen_ids = {_mod_key(mod.get("modId")) for mod in current_mods}
    activated_ids: set[str] = set()

    added_count = 0
    skipped_count = 0
    for mod in imported_mods:
        mod_id = mod.get("modId")
        mod_key = _mod_key(mod_id)
        activated_ids.add(mod_key)
        if mod_key in seen_ids:
            skipped_count += 1
            continue

        current_mods.append(
            {
                "modId": mod_id,
                "name": mod.get("name", ""),
                "version": mod.get("version", ""),
            }
        )
        seen_ids.add(mod_key)
        added_count += 1

    remaining_disabled = [
        mod for mod in disabled_mods if _mod_key(mod.get("modId")) not in activated_ids
    ]

    update_result = save_mods_with_removed_addon_cleanup(
        config_path,
        config,
        old_mods,
        current_mods,
    )
    save_disabled_mods(config_path, remaining_disabled)
    return added_count, skipped_count, update_result
