"""Cleaner module - safe deletion of server junk files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from armactl import paths

LOG_SUFFIXES = (".log",)
DUMP_SUFFIXES = (".mdmp", ".bidmp", ".rpt")
CLEANUP_CATEGORIES = ("logs", "dumps", "backups")


def _safe_cleanup_root(root: Path) -> Path | None:
    """Return a resolved cleanup root, or None when it is unsafe/unavailable."""
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        return root.resolve(strict=True)
    except OSError:
        return None


def _is_inside_or_equal(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _iter_safe_files(root: Path, suffixes: tuple[str, ...]):
    """Yield regular files below root without following symlinked paths."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError:
            continue

        for entry in entries:
            entry_path = Path(entry.path)
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    resolved_dir = entry_path.resolve(strict=True)
                    if _is_inside_or_equal(resolved_dir, root):
                        stack.append(resolved_dir)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue

            if entry_path.name.endswith(suffixes):
                try:
                    resolved_file = entry_path.resolve(strict=True)
                except OSError:
                    continue
                if _is_inside_or_equal(resolved_file, root):
                    yield resolved_file


def _is_config_backup(path: Path) -> bool:
    return path.name.startswith("config.json.") and path.name.endswith(".bak")


def _add_file_stats(stats: dict[str, Any], category: str, file_path: Path) -> None:
    try:
        stat = file_path.stat()
    except OSError:
        return
    stats[category]["count"] += 1
    stats[category]["size"] += stat.st_size
    stats[category]["paths"].append(file_path)
    stats["total_size"] += stat.st_size


def _cleanup_roots(instance: str) -> dict[str, tuple[Path, ...]]:
    config_root = _safe_cleanup_root(paths.config_dir(instance))
    backups_root = _safe_cleanup_root(paths.backups_dir(instance))
    return {
        "logs": (config_root,) if config_root is not None else (),
        "dumps": (config_root,) if config_root is not None else (),
        "backups": (backups_root,) if backups_root is not None else (),
    }


def _is_safe_cleanup_file(file_path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    try:
        if file_path.is_symlink() or not file_path.is_file():
            return False
        resolved = file_path.resolve(strict=True)
    except OSError:
        return False
    return any(_is_inside_or_equal(resolved, root) for root in allowed_roots)


def get_junk_stats(instance: str) -> dict[str, Any]:
    """Scan for old logs, crash dumps, and stale backups."""
    stats = {
        "logs": {"count": 0, "size": 0, "paths": []},
        "dumps": {"count": 0, "size": 0, "paths": []},
        "backups": {"count": 0, "size": 0, "paths": []},
        "total_size": 0,
    }

    roots = _cleanup_roots(instance)
    if roots["logs"]:
        for log_file in _iter_safe_files(roots["logs"][0], LOG_SUFFIXES):
            _add_file_stats(stats, "logs", log_file)

    if roots["dumps"]:
        for dump_file in _iter_safe_files(roots["dumps"][0], DUMP_SUFFIXES):
            _add_file_stats(stats, "dumps", dump_file)

    if roots["backups"]:
        backups = sorted(
            (
                backup_file
                for backup_file in _iter_safe_files(roots["backups"][0], (".bak",))
                if _is_config_backup(backup_file)
            ),
            key=os.path.getmtime,
        )
        for backup_file in backups[:-2]:
            _add_file_stats(stats, "backups", backup_file)

    return stats


def clean_junk(instance: str) -> dict[str, int]:
    """Execute the cleanup and return stats of what was freed."""
    stats = get_junk_stats(instance)
    freed_bytes = 0
    files_deleted = 0
    roots = _cleanup_roots(instance)

    for category in CLEANUP_CATEGORIES:
        for file_path in stats[category]["paths"]:
            file_path = Path(file_path)
            if not _is_safe_cleanup_file(file_path, roots[category]):
                continue
            try:
                size = file_path.stat().st_size
                file_path.unlink(missing_ok=True)
                freed_bytes += size
                files_deleted += 1
            except OSError:
                pass

    return {"freed_bytes": freed_bytes, "files_deleted": files_deleted}


def format_size(size_bytes: int) -> str:
    """Format bytes into KB, MB, or GB."""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
