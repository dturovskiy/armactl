"""Linux process metrics for armactl status views."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProcessMetrics:
    """Best-effort process CPU and memory metrics."""

    available: bool
    pid: int
    cpu_percent: float | None = None
    memory_rss_bytes: int | None = None
    error: str = ""


def format_bytes(value: int | None) -> str:
    """Format a byte count using small binary units."""
    if value is None:
        return "Unknown"

    size = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(value)} B"


def format_cpu_percent(value: float | None) -> str:
    """Format a CPU percentage for status output."""
    if value is None:
        return "Unknown"
    return f"{value:.1f}%"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def query_process_metrics(pid: int) -> ProcessMetrics:
    """Return best-effort CPU and RSS memory metrics for a Linux process."""
    if pid <= 0:
        return ProcessMetrics(False, pid, error="main pid is not available")

    proc_dir = Path("/proc") / str(pid)
    stat_path = proc_dir / "stat"
    status_path = proc_dir / "status"
    uptime_path = Path("/proc/uptime")

    try:
        stat_text = _read_text(stat_path)
        status_text = _read_text(status_path)
        uptime_text = _read_text(uptime_path)
    except OSError as error:
        return ProcessMetrics(False, pid, error=str(error))

    try:
        stat_fields = stat_text.split()
        total_ticks = int(stat_fields[13]) + int(stat_fields[14])
        start_ticks = int(stat_fields[21])
        clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        uptime_seconds = float(uptime_text.split()[0])
        elapsed_seconds = max(uptime_seconds - (start_ticks / clock_ticks), 0.001)
        cpu_percent = (total_ticks / clock_ticks) / elapsed_seconds * 100.0
    except (IndexError, KeyError, ValueError, OSError) as error:
        return ProcessMetrics(False, pid, error=str(error))

    memory_rss_bytes: int | None = None
    for line in status_text.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            memory_rss_bytes = int(parts[1]) * 1024
        break

    return ProcessMetrics(
        True,
        pid,
        cpu_percent=cpu_percent,
        memory_rss_bytes=memory_rss_bytes,
    )
