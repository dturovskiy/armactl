"""Linux process metrics for armactl status views."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def _cpu_count() -> int:
    """Return a sane CPU count for percentage normalization."""
    return max(os.cpu_count() or 1, 1)


def estimate_service_cpu_percent(service_status: dict[str, Any]) -> float | None:
    """Estimate CPU percent from systemd show data when /proc PID metrics are unavailable."""
    cpu_usage_nsec = service_status.get("cpu_usage_nsec")
    start_usec = (
        service_status.get("exec_main_start_usec")
        or service_status.get("active_enter_usec")
    )
    if (
        not isinstance(cpu_usage_nsec, int)
        or cpu_usage_nsec < 0
        or not isinstance(start_usec, int)
        or start_usec <= 0
    ):
        return None

    try:
        uptime_seconds = float(_read_text(Path("/proc/uptime")).split()[0])
    except (IndexError, OSError, ValueError):
        return None

    elapsed_usec = max(int(uptime_seconds * 1_000_000) - start_usec, 1)
    elapsed_nsec = elapsed_usec * 1_000
    return (cpu_usage_nsec / elapsed_nsec) * 100.0 / _cpu_count()


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
        cpu_percent = (total_ticks / clock_ticks) / elapsed_seconds * 100.0 / _cpu_count()
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


def query_service_runtime_metrics(service_status: dict[str, Any]) -> ProcessMetrics:
    """Return the best available runtime metrics using PID data and systemd fallbacks."""
    pid = int(service_status.get("main_pid", 0) or 0)
    proc_metrics = query_process_metrics(pid)

    cpu_percent = proc_metrics.cpu_percent
    if cpu_percent is None:
        cpu_percent = estimate_service_cpu_percent(service_status)

    memory_rss_bytes = None
    fallback_memory = service_status.get("memory_current_bytes")
    if isinstance(fallback_memory, int) and fallback_memory >= 0:
        memory_rss_bytes = fallback_memory
    elif proc_metrics.memory_rss_bytes is not None:
        memory_rss_bytes = proc_metrics.memory_rss_bytes

    return ProcessMetrics(
        available=(cpu_percent is not None or memory_rss_bytes is not None),
        pid=pid,
        cpu_percent=cpu_percent,
        memory_rss_bytes=memory_rss_bytes,
        error=proc_metrics.error,
    )
