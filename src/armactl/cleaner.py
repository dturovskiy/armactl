"""Cleaner module - safe deletion of server junk files."""

from __future__ import annotations

import os
from typing import Any

from armactl import paths


def get_junk_stats(instance: str) -> dict[str, Any]:
    """Scan for old logs, crash dumps, and stale backups."""
    profile_dir = paths.config_dir(instance)
    backups_dir = paths.backups_dir(instance)

    stats = {
        "logs": {"count": 0, "size": 0, "paths": []},
        "dumps": {"count": 0, "size": 0, "paths": []},
        "backups": {"count": 0, "size": 0, "paths": []},
        "total_size": 0,
    }

    if profile_dir.exists():
        for log_file in profile_dir.rglob("*.log"):
            stat = log_file.stat()
            stats["logs"]["count"] += 1
            stats["logs"]["size"] += stat.st_size
            stats["logs"]["paths"].append(log_file)
            stats["total_size"] += stat.st_size

    if profile_dir.exists():
        for ext in ("*.mdmp", "*.bidmp", "*.rpt"):
            for dump_file in profile_dir.rglob(ext):
                stat = dump_file.stat()
                stats["dumps"]["count"] += 1
                stats["dumps"]["size"] += stat.st_size
                stats["dumps"]["paths"].append(dump_file)
                stats["total_size"] += stat.st_size

    if backups_dir.exists():
        backups = sorted(backups_dir.glob("config.json.*.bak"), key=os.path.getmtime)
        if len(backups) > 2:
            for backup_file in backups[:-2]:
                stat = backup_file.stat()
                stats["backups"]["count"] += 1
                stats["backups"]["size"] += stat.st_size
                stats["backups"]["paths"].append(backup_file)
                stats["total_size"] += stat.st_size

    return stats


def clean_junk(instance: str) -> dict[str, int]:
    """Execute the cleanup and return stats of what was freed."""
    stats = get_junk_stats(instance)
    freed_bytes = 0
    files_deleted = 0

    for category in ["logs", "dumps", "backups"]:
        for file_path in stats[category]["paths"]:
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
