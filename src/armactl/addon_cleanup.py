"""Safe cleanup for stale Arma Reforger Workshop addon directories."""

from __future__ import annotations

import errno
import logging
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ADDON_DIR_MOD_ID_RE = re.compile(r"(?i)(?:^|_)([0-9a-f]{16})$")
MOD_ID_RE = re.compile(r"(?i)^[0-9a-f]{16}$")


@dataclass
class CleanupResult:
    """Structured result of an addon cleanup operation."""

    deleted: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bytes_deleted: int = 0

    @property
    def freed_display(self) -> str:
        """Human-readable representation of freed space."""
        return _format_bytes(self.bytes_deleted)


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
    """Return valid active mod IDs from parsed config data, uppercased."""
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
            raise ValueError(f"Refusing addon cleanup because addons path is not a directory: {addons}")

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
        result.errors.append(f"Refusing addon cleanup because addons path is not a directory: {addons}")
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

    for entry in _iter_safe_addon_entries(addons_root, result):
        parsed_id = extract_mod_id_from_addon_dir_name(entry.name)
        if parsed_id is None:
            result.skipped.append(entry)
            continue
        if parsed_id not in target_ids:
            continue
        _delete_entry(entry, result, dry_run=dry_run)

    return result


def cleanup_unconfigured_addons(
    config_path: Path | str,
    active_mod_ids: set[str] | None = None,
    *,
    dry_run: bool = False,
) -> CleanupResult:
    """Delete valid addon dirs whose IDs are not active in config.json."""
    result = CleanupResult()
    addons_root = _prepare_addons_root(config_path, result)
    if addons_root is None:
        return result

    if active_mod_ids is None:
        from armactl.config_manager import load_config

        config_data = load_config(config_path)
        active_upper = active_mod_ids_from_config_data(config_data)
    else:
        active_upper = {
            normalized
            for mod_id in active_mod_ids
            if (normalized := normalize_mod_id(mod_id)) is not None
        }

    for entry in _iter_safe_addon_entries(addons_root, result):
        parsed_id = extract_mod_id_from_addon_dir_name(entry.name)
        if parsed_id is None:
            result.skipped.append(entry)
            continue
        if parsed_id in active_upper:
            continue
        _delete_entry(entry, result, dry_run=dry_run)

    return result
