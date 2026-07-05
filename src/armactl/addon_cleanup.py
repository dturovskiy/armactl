"""Safe cleanup for stale Arma Reforger Workshop addon directories."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armactl.redaction import redact_sensitive_text

log = logging.getLogger(__name__)

ADDON_DIR_MOD_ID_RE = re.compile(r"(?i)(?:^|_)([0-9a-f]{16})$")
MOD_ID_RE = re.compile(r"(?i)^[0-9a-f]{16}$")
MANIFEST_SUBDIR = "mod-cleanup"
MANIFEST_VERSION = 1
MAX_MANIFEST_MODS = 50
MAX_MANIFEST_NAME_LENGTH = 128


@dataclass(frozen=True)
class CleanupManifestRef:
    """Audit/UI-safe reference to an addon cleanup recovery manifest."""

    name: str
    status: str
    planned_delete_count: int = 0
    completed_delete_count: int = 0
    failed_delete_count: int = 0
    skipped_count: int = 0


@dataclass
class CleanupResult:
    """Structured result of an addon cleanup operation."""

    deleted: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bytes_deleted: int = 0
    manifests: list[CleanupManifestRef] = field(default_factory=list)

    @property
    def freed_display(self) -> str:
        """Human-readable representation of freed space."""
        return _format_bytes(self.bytes_deleted)

    @property
    def manifest(self) -> CleanupManifestRef | None:
        """Return the most recent recovery manifest reference, if one exists."""
        return self.manifests[-1] if self.manifests else None


@dataclass(frozen=True)
class _PlannedAddonCleanup:
    """One addon directory selected for deletion, represented safely."""

    path: Path
    mod_id: str
    name: str


def _format_bytes(size: int) -> str:
    """Format bytes into KB, MB, or GB."""
    if size < 1024 * 1024:
        return f"{size / 1024:.2f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def normalize_mod_id(mod_id: Any) -> str | None:
    """Return an uppercase 16-hex mod ID, or None for invalid input."""
    value = str(mod_id or "").strip()
    if not MOD_ID_RE.fullmatch(value):
        return None
    return value.upper()


def extract_mod_id_from_addon_dir_name(name: str) -> str | None:
    """Extract the final 16-hex addon mod ID from a directory name."""
    match = ADDON_DIR_MOD_ID_RE.search(name)
    if match is None:
        return None
    return match.group(1).upper()


def active_mod_ids_from_config_data(config: dict[str, Any]) -> set[str]:
    """Return valid active server-facing mod IDs from config data, uppercased."""
    game = config.get("game", {})
    mods = game.get("mods", [])
    return _normalized_mod_ids(mods)


def _normalized_mod_ids(mods: Iterable[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for mod in mods:
        mod_id = normalize_mod_id(mod.get("modId"))
        if mod_id is not None:
            ids.add(mod_id)
    return ids


def resolve_safe_addons_dir(config_path: Path | str) -> Path:
    """Return the safe ``<instance>/config/addons`` path for a config file.

    The cleanup routines intentionally accept only the canonical armactl layout:

        <instance>/config/config.json
        <instance>/config/addons/

    Anything else is rejected so a caller cannot accidentally point cleanup at
    ``server/addons`` or an arbitrary directory. The returned path is absolute
    and resolved, but it may not exist yet.
    """
    config_path = Path(config_path)
    if config_path.name != "config.json":
        raise ValueError(f"Refusing addon cleanup for non-config.json path: {config_path}")

    config_dir = config_path.parent
    if config_dir.name != "config":
        raise ValueError(f"Refusing addon cleanup outside a config directory: {config_path}")
    if config_dir.is_symlink():
        raise ValueError(f"Refusing addon cleanup through symlinked config dir: {config_dir}")
    if not config_dir.is_dir():
        raise ValueError(f"Refusing addon cleanup because config dir is missing: {config_dir}")

    config_dir_resolved = config_dir.resolve(strict=True)
    addons = config_dir / "addons"

    if addons.is_symlink():
        raise ValueError(f"Refusing addon cleanup through symlinked addons dir: {addons}")
    if addons.exists():
        if not addons.is_dir():
            raise ValueError(
                f"Refusing addon cleanup because addons path is not a directory: {addons}"
            )

    addons_resolved = addons.resolve(strict=False)
    try:
        relative = addons_resolved.relative_to(config_dir_resolved)
    except ValueError as exc:
        raise ValueError(f"Refusing addon cleanup outside config dir: {addons}") from exc

    if relative != Path("addons"):
        raise ValueError(f"Refusing addon cleanup for unexpected addons path: {addons}")

    return addons_resolved


def addons_dir_for_config(config_path: Path | str) -> Path:
    """Backward-compatible alias for the strict safe addons resolver."""
    return resolve_safe_addons_dir(config_path)


def is_path_inside(child: Path, parent: Path) -> bool:
    """Return True when *child* resolves to a strict descendant of *parent*."""
    child_resolved = child.resolve(strict=False)
    parent_resolved = parent.resolve(strict=False)
    if child_resolved == parent_resolved:
        return False
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        return False
    return True


def dir_size(path: Path) -> int:
    """Recursively compute directory size without following symlinks."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        return 0

    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def is_enospc(exc: Exception) -> bool:
    """Return True if an exception chain contains ENOSPC."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno == errno.ENOSPC:
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False



def _safe_manifest_text(value: Any, *, max_length: int = MAX_MANIFEST_NAME_LENGTH) -> str:
    text = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    lowered = text.lower()
    for marker in ("token=", "token:", "password=", "password:"):
        marker_start = lowered.find(marker)
        if marker_start >= 0:
            marker_end = marker_start + len(marker)
            text = f"{text[:marker_end]}***"
            break
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def _safe_addon_entry_name(path: Path) -> str:
    return _safe_manifest_text(Path(path).name or "unknown")


def _instance_from_config_path(config_path: Path | str) -> str:
    path = Path(config_path)
    if path.parent.name == "config" and path.parent.parent.name:
        return _safe_manifest_text(path.parent.parent.name, max_length=80) or "default"
    return "default"


def _manifest_root_for_config(config_path: Path | str) -> Path:
    path = Path(config_path)
    if path.parent.name == "config":
        return path.parent.parent / "backups" / MANIFEST_SUBDIR
    return path.parent / "backups" / MANIFEST_SUBDIR


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)


def _write_manifest_payload(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _next_manifest_path(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    manifest_path = root / f"mod-cleanup-{timestamp}.json"
    suffix = 1
    while manifest_path.exists():
        manifest_path = root / f"mod-cleanup-{timestamp}-{suffix}.json"
        suffix += 1
    return manifest_path


def _safe_mod_entry(mod: Any) -> dict[str, str]:
    if isinstance(mod, Mapping):
        raw_id = mod.get("modId") or mod.get("mod_id") or ""
        mod_id = normalize_mod_id(raw_id) or _safe_manifest_text(raw_id, max_length=64).upper()
        name = _safe_manifest_text(mod.get("name"), max_length=MAX_MANIFEST_NAME_LENGTH)
        return {"modId": mod_id, "name": name}
    mod_id = normalize_mod_id(mod) or _safe_manifest_text(mod, max_length=64).upper()
    return {"modId": mod_id, "name": ""}


def _affected_mods_for_manifest(
    affected_mods: Iterable[Mapping[str, Any] | str] | None,
    planned_entries: list[_PlannedAddonCleanup],
) -> list[dict[str, str]]:
    safe_mods: list[dict[str, str]] = []
    seen: set[str] = set()
    for mod in affected_mods or ():
        safe = _safe_mod_entry(mod)
        key = safe["modId"]
        if not key or key in seen:
            continue
        seen.add(key)
        safe_mods.append(safe)
        if len(safe_mods) >= MAX_MANIFEST_MODS:
            return safe_mods

    for entry in planned_entries:
        if entry.mod_id in seen:
            continue
        seen.add(entry.mod_id)
        safe_mods.append({"modId": entry.mod_id, "name": entry.name})
        if len(safe_mods) >= MAX_MANIFEST_MODS:
            break
    return safe_mods


def _planned_addons_payload(
    planned_entries: list[_PlannedAddonCleanup],
) -> list[dict[str, str]]:
    return [
        {"modId": entry.mod_id, "name": entry.name}
        for entry in planned_entries[:MAX_MANIFEST_MODS]
    ]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _manifest_status(result: CleanupResult) -> str:
    if result.errors and result.deleted:
        return "partial"
    if result.errors:
        return "failed"
    return "completed"


def _manifest_ref(
    manifest_path: Path,
    *,
    status: str,
    planned_delete_count: int,
    result: CleanupResult,
) -> CleanupManifestRef:
    return CleanupManifestRef(
        name=manifest_path.name,
        status=status,
        planned_delete_count=planned_delete_count,
        completed_delete_count=len(result.deleted),
        failed_delete_count=len(result.errors),
        skipped_count=len(result.skipped),
    )


def _set_manifest_ref(result: CleanupResult, ref: CleanupManifestRef) -> None:
    for index, existing in enumerate(result.manifests):
        if existing.name == ref.name:
            result.manifests[index] = ref
            return
    result.manifests.append(ref)


def _create_cleanup_manifest(
    config_path: Path | str,
    *,
    action: str,
    instance: str | None,
    affected_mods: Iterable[Mapping[str, Any] | str] | None,
    planned_entries: list[_PlannedAddonCleanup],
    skipped_count: int,
    target_mod_count: int,
    result: CleanupResult,
) -> tuple[Path, dict[str, Any]] | None:
    manifest_root = _manifest_root_for_config(config_path)
    manifest_path = _next_manifest_path(manifest_root)
    payload: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "action": _safe_manifest_text(action, max_length=80),
        "instance": _safe_manifest_text(
            instance or _instance_from_config_path(config_path),
            max_length=80,
        ),
        "timestamp": _utc_timestamp(),
        "status": "planned",
        "affected_mods": _affected_mods_for_manifest(affected_mods, planned_entries),
        "affected_mods_omitted_count": str(
            max(0, target_mod_count - MAX_MANIFEST_MODS)
        ),
        "planned_operations": {
            "delete_addon_dirs": len(planned_entries),
            "skip_addon_dirs": skipped_count,
            "target_mods": target_mod_count,
        },
        "planned_addons": _planned_addons_payload(planned_entries),
        "planned_addons_omitted_count": str(
            max(0, len(planned_entries) - MAX_MANIFEST_MODS)
        ),
        "result": {
            "deleted_count": 0,
            "failed_count": 0,
            "skipped_count": skipped_count,
            "bytes_deleted": 0,
        },
        "path_details": "Absolute paths and path-specific errors are intentionally omitted.",
    }
    try:
        _write_manifest_payload(manifest_path, payload)
    except OSError as exc:
        result.errors.append("Failed to create cleanup recovery manifest.")
        log.warning("Failed to create addon cleanup manifest: %s", exc)
        return None
    _set_manifest_ref(
        result,
        _manifest_ref(
            manifest_path,
            status="planned",
            planned_delete_count=len(planned_entries),
            result=result,
        ),
    )
    return manifest_path, payload


def _finalize_cleanup_manifest(
    manifest_path: Path,
    payload: dict[str, Any],
    *,
    result: CleanupResult,
    planned_delete_count: int,
) -> None:
    status = _manifest_status(result)
    payload["status"] = status
    payload["updated_at"] = _utc_timestamp()
    payload["result"] = {
        "deleted_count": len(result.deleted),
        "failed_count": len(result.errors),
        "skipped_count": len(result.skipped),
        "bytes_deleted": result.bytes_deleted,
    }
    payload["errors_suppressed"] = bool(result.errors)
    try:
        _write_manifest_payload(manifest_path, payload)
    except OSError as exc:
        result.errors.append("Failed to update cleanup recovery manifest.")
        log.warning("Failed to update addon cleanup manifest: %s", exc)
        status = _manifest_status(result)
    _set_manifest_ref(
        result,
        _manifest_ref(
            manifest_path,
            status=status,
            planned_delete_count=planned_delete_count,
            result=result,
        ),
    )


def _delete_planned_addons(
    config_path: Path | str,
    planned_entries: list[_PlannedAddonCleanup],
    result: CleanupResult,
    *,
    dry_run: bool,
    manifest_action: str,
    manifest_instance: str | None,
    affected_mods: Iterable[Mapping[str, Any] | str] | None,
    target_mod_count: int,
) -> None:
    manifest: tuple[Path, dict[str, Any]] | None = None
    if not dry_run and planned_entries:
        manifest = _create_cleanup_manifest(
            config_path,
            action=manifest_action,
            instance=manifest_instance,
            affected_mods=affected_mods,
            planned_entries=planned_entries,
            skipped_count=len(result.skipped),
            target_mod_count=target_mod_count,
            result=result,
        )
        if manifest is None:
            return

    for entry in planned_entries:
        try:
            _delete_entry(entry.path, result, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - keep cleanup result controlled.
            result.errors.append(
                "Unexpected addon cleanup failure; path-specific details were suppressed."
            )
            log.exception("Unexpected addon cleanup failure for %s: %s", entry.path, exc)
            break

    if manifest is not None:
        manifest_path, payload = manifest
        _finalize_cleanup_manifest(
            manifest_path,
            payload,
            result=result,
            planned_delete_count=len(planned_entries),
        )


def _prepare_addons_root(config_path: Path | str, result: CleanupResult) -> Path | None:
    try:
        addons = resolve_safe_addons_dir(config_path)
    except ValueError as exc:
        result.errors.append(str(exc))
        return None

    if not addons.exists():
        return None
    if addons.is_symlink():
        result.errors.append(f"Refusing addon cleanup through symlinked addons dir: {addons}")
        return None
    if not addons.is_dir():
        result.errors.append(
            f"Refusing addon cleanup because addons path is not a directory: {addons}"
        )
        return None
    return addons.resolve(strict=True)


def _iter_safe_addon_entries(addons_root: Path, result: CleanupResult) -> Iterable[Path]:
    try:
        entries = sorted(addons_root.iterdir())
    except OSError as exc:
        result.errors.append(f"Failed to list addon directory {addons_root}: {exc}")
        return

    for entry in entries:
        if entry.is_symlink():
            result.skipped.append(entry)
            continue
        if not entry.is_dir():
            continue

        try:
            entry_resolved = entry.resolve(strict=True)
        except OSError as exc:
            result.errors.append(f"Failed to resolve addon directory {entry}: {exc}")
            continue

        if not is_path_inside(entry_resolved, addons_root):
            result.errors.append(f"Refusing to delete unsafe addon path outside root: {entry}")
            continue

        yield entry_resolved


def _delete_entry(entry: Path, result: CleanupResult, *, dry_run: bool) -> None:
    size = dir_size(entry)
    if dry_run:
        result.deleted.append(entry)
        result.bytes_deleted += size
        return

    try:
        shutil.rmtree(entry)
    except OSError as exc:
        result.errors.append(f"Failed to delete {entry}: {exc}")
        log.warning("Failed to delete addon directory %s: %s", entry, exc)
        return

    result.deleted.append(entry)
    result.bytes_deleted += size
    log.info("Deleted addon directory: %s (%d bytes)", entry, size)


def cleanup_addons_by_mod_ids(
    config_path: Path | str,
    mod_ids: set[str] | frozenset[str],
    *,
    dry_run: bool = False,
    manifest_action: str = "mod.remove",
    manifest_instance: str | None = None,
    affected_mods: Iterable[Mapping[str, Any] | str] | None = None,
) -> CleanupResult:
    """Delete addon directories matching the provided removed mod IDs."""
    result = CleanupResult()
    target_ids = {
        normalized for mod_id in mod_ids if (normalized := normalize_mod_id(mod_id)) is not None
    }
    if not target_ids:
        return result

    addons_root = _prepare_addons_root(config_path, result)
    if addons_root is None:
        return result

    planned_entries: list[_PlannedAddonCleanup] = []
    for entry in _iter_safe_addon_entries(addons_root, result):
        parsed_id = extract_mod_id_from_addon_dir_name(entry.name)
        if parsed_id is None:
            result.skipped.append(entry)
            continue
        if parsed_id not in target_ids:
            continue
        planned_entries.append(
            _PlannedAddonCleanup(entry, parsed_id, _safe_addon_entry_name(entry))
        )

    _delete_planned_addons(
        config_path,
        planned_entries,
        result,
        dry_run=dry_run,
        manifest_action=manifest_action,
        manifest_instance=manifest_instance,
        affected_mods=affected_mods,
        target_mod_count=len(target_ids),
    )
    return result


def cleanup_unconfigured_addons(
    config_path: Path | str,
    active_mod_ids: set[str] | None = None,
    *,
    dry_run: bool = False,
    manifest_action: str = "mod.cleanup",
    manifest_instance: str | None = None,
) -> CleanupResult:
    """Delete valid addon dirs whose IDs are not active in config.json."""
    result = CleanupResult()
    addons_root = _prepare_addons_root(config_path, result)
    if addons_root is None:
        return result

    if active_mod_ids is None:
        from armactl.config_manager import load_config
        from armactl.mods_state import load_disabled_mods

        config_data = load_config(config_path)
        game = config_data.get("game", {})
        legacy_disabled = game.get("disabledMods", []) if isinstance(game, dict) else []
        active_upper = (
            active_mod_ids_from_config_data(config_data)
            | _normalized_mod_ids(load_disabled_mods(config_path))
            | _normalized_mod_ids(legacy_disabled)
        )
    else:
        active_upper = {
            normalized
            for mod_id in active_mod_ids
            if (normalized := normalize_mod_id(mod_id)) is not None
        }

    planned_entries: list[_PlannedAddonCleanup] = []
    for entry in _iter_safe_addon_entries(addons_root, result):
        parsed_id = extract_mod_id_from_addon_dir_name(entry.name)
        if parsed_id is None:
            result.skipped.append(entry)
            continue
        if parsed_id in active_upper:
            continue
        planned_entries.append(
            _PlannedAddonCleanup(entry, parsed_id, _safe_addon_entry_name(entry))
        )

    _delete_planned_addons(
        config_path,
        planned_entries,
        result,
        dry_run=dry_run,
        manifest_action=manifest_action,
        manifest_instance=manifest_instance,
        affected_mods=None,
        target_mod_count=len(planned_entries),
    )
    return result
