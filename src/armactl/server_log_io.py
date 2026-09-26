"""Bounded filesystem access shared by server telemetry and incident parsing."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

CONSOLE_LOG_TAIL_BYTES = 512 * 1024
RecentLogProbe = Callable[[Path, int], list[Path]]


def recent_console_logs(config_dir: Path, limit: int = 3) -> list[Path]:
    """Return newest readable console-log candidates within a bounded count."""
    logs_dir = config_dir / "logs"
    try:
        candidates = list(logs_dir.glob("*/console.log"))
    except OSError:
        return []

    dated: list[tuple[float, Path]] = []
    for candidate in candidates:
        try:
            mtime = os.path.getmtime(candidate)
        except OSError:
            continue
        dated.append((mtime, candidate))
    dated.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in dated[: max(limit, 0)]]


def latest_console_log(
    config_dir: Path,
    *,
    recent: RecentLogProbe = recent_console_logs,
) -> Path | None:
    """Return the newest readable console log, if present."""
    logs = recent(config_dir, 1)
    return logs[0] if logs else None


def read_tail_text_file(
    path: Path,
    max_bytes: int = CONSOLE_LOG_TAIL_BYTES,
) -> str:
    """Read a bounded UTF-8 tail while preserving recent complete lines."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(max(size - max_bytes, 0))
            data = handle.read(max_bytes)
            first_newline = data.find(b"\n")
            if first_newline != -1:
                data = data[first_newline + 1 :]
        else:
            data = handle.read()
    return data.decode("utf-8", errors="replace")
