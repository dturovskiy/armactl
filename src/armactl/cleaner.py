"""Cleaner module — handles safe deletion of server junk files."""

from __future__ import annotations

import os
from typing import Any

from armactl import paths as P

def get_junk_stats(instance: str) -> dict[str, Any]:
    """Scan for old logs, crash dumps, and stale backups."""
    profile_dir = P.config_dir(instance)
    logs_dir = profile_dir / "logs"
    backups_dir = P.backups_dir(instance)
    
    stats = {
        "logs": {"count": 0, "size": 0, "paths": []},
        "dumps": {"count": 0, "size": 0, "paths": []},
        "backups": {"count": 0, "size": 0, "paths": []},
        "total_size": 0
    }
    
    # 1. Arma Reforger Logs (console.log, net.log, script.log, etc)
    if profile_dir.exists():
        for f in profile_dir.rglob("*.log"):
            stat = f.stat()
            stats["logs"]["count"] += 1
            stats["logs"]["size"] += stat.st_size
            stats["logs"]["paths"].append(f)
            stats["total_size"] += stat.st_size
            
    # 2. Crash Dumps (Arma Reforger uses .mdmp and .bidmp)
    if profile_dir.exists():
        for ext in ("*.mdmp", "*.bidmp", "*.rpt"):
            for f in profile_dir.rglob(ext):
                stat = f.stat()
                stats["dumps"]["count"] += 1
                stats["dumps"]["size"] += stat.st_size
                stats["dumps"]["paths"].append(f)
                stats["total_size"] += stat.st_size
                
    # 3. Old config backups (keep only the 2 latest)
    if backups_dir.exists():
        backups = list(sorted(backups_dir.glob("config.json.*.bak"), key=os.path.getmtime))
        if len(backups) > 2:
            for f in backups[:-2]:
                stat = f.stat()
                stats["backups"]["count"] += 1
                stats["backups"]["size"] += stat.st_size
                stats["backups"]["paths"].append(f)
                stats["total_size"] += stat.st_size

    return stats

def clean_junk(instance: str) -> dict[str, int]:
    """Execute the cleanup and return stats of what was freed."""
    stats = get_junk_stats(instance)
    freed_bytes = 0
    files_deleted = 0
    
    for category in ["logs", "dumps", "backups"]:
        for p in stats[category]["paths"]:
            try:
                # Get size before deleting
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                freed_bytes += size
                files_deleted += 1
            except OSError:
                pass
                
    return {"freed_bytes": freed_bytes, "files_deleted": files_deleted}

def format_size(size_bytes: int) -> str:
    """Format bytes into MB/GB."""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
