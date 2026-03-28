"""Linux process metrics for armactl status views."""

from __future__ import annotations

import os
import shutil
import time
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


@dataclass
class HostMetrics:
    """Best-effort host/VM metrics for diagnostics views."""

    available: bool
    cpu_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None
    load_average_1m: float | None = None
    load_average_5m: float | None = None
    load_average_15m: float | None = None
    uptime_seconds: float | None = None
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


def format_load_average(
    one_minute: float | None,
    five_minutes: float | None,
    fifteen_minutes: float | None,
) -> str:
    """Format a host load-average triple."""
    if (
        one_minute is None
        or five_minutes is None
        or fifteen_minutes is None
    ):
        return "Unknown"
    return f"{one_minute:.2f} / {five_minutes:.2f} / {fifteen_minutes:.2f}"


def format_duration(seconds: float | None) -> str:
    """Format a duration in a compact human-readable style."""
    if seconds is None:
        return "Unknown"

    remaining = max(int(seconds), 0)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds_part = divmod(remaining, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds_part}s")
    return " ".join(parts[:3])


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _cpu_count() -> int:
    """Return a sane CPU count for percentage normalization."""
    return max(os.cpu_count() or 1, 1)


def _page_size() -> int:
    """Return a sane Linux page size for statm RSS fallback parsing."""
    return max(int(os.sysconf("SC_PAGE_SIZE")), 1)


def _parse_meminfo() -> tuple[int | None, int | None]:
    """Return host RAM used/total bytes from /proc/meminfo."""
    meminfo: dict[str, int] = {}
    for line in _read_text(Path("/proc/meminfo")).splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            meminfo[key] = int(parts[0]) * 1024
        except ValueError:
            continue

    total = meminfo.get("MemTotal")
    available = meminfo.get("MemAvailable")
    if total is None or available is None:
        return None, None
    return max(total - available, 0), total


def _read_host_cpu_sample() -> tuple[int, int] | None:
    """Return total and idle Linux CPU jiffies from /proc/stat."""
    for line in _read_text(Path("/proc/stat")).splitlines():
        if not line.startswith("cpu "):
            continue
        parts = line.split()[1:]
        if len(parts) < 5:
            return None
        values = [int(part) for part in parts]
        total = sum(values)
        idle = values[3] + values[4]
        return total, idle
    return None


def estimate_host_cpu_percent(sample_seconds: float = 0.05) -> float | None:
    """Estimate host CPU utilization from two /proc/stat samples."""
    try:
        first_sample = _read_host_cpu_sample()
        if first_sample is None:
            return None
        time.sleep(max(sample_seconds, 0.0))
        second_sample = _read_host_cpu_sample()
        if second_sample is None:
            return None
    except (OSError, ValueError):
        return None

    total_delta = second_sample[0] - first_sample[0]
    idle_delta = second_sample[1] - first_sample[1]
    if total_delta <= 0:
        return None
    busy_delta = max(total_delta - idle_delta, 0)
    return (busy_delta / total_delta) * 100.0


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
    statm_path = proc_dir / "statm"
    uptime_path = Path("/proc/uptime")

    try:
        stat_text = _read_text(stat_path)
        uptime_text = _read_text(uptime_path)
    except OSError as error:
        return ProcessMetrics(False, pid, error=str(error))

    try:
        status_text = _read_text(status_path)
    except OSError:
        status_text = ""

    try:
        statm_text = _read_text(statm_path)
    except OSError:
        statm_text = ""

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

    if memory_rss_bytes is None and statm_text:
        try:
            resident_pages = int(statm_text.split()[1])
            memory_rss_bytes = resident_pages * _page_size()
        except (IndexError, OSError, ValueError):
            pass

    return ProcessMetrics(
        True,
        pid,
        cpu_percent=cpu_percent,
        memory_rss_bytes=memory_rss_bytes,
    )


def query_service_runtime_metrics(service_status: dict[str, Any]) -> ProcessMetrics:
    """Return the best available runtime metrics using PID data and systemd fallbacks."""
    service_is_active = bool(service_status.get("active"))
    active_state = str(service_status.get("active_state", "") or "").strip().lower()
    if not service_is_active and active_state and active_state != "active":
        return ProcessMetrics(
            False,
            int(service_status.get("main_pid", 0) or 0),
            error="service is not active",
        )

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


def query_host_metrics(path: str | Path = "/") -> HostMetrics:
    """Return best-effort host/VM metrics for diagnostics views."""
    cpu_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None
    load_average_1m: float | None = None
    load_average_5m: float | None = None
    load_average_15m: float | None = None
    uptime_seconds: float | None = None
    errors: list[str] = []

    try:
        cpu_percent = estimate_host_cpu_percent()
    except OSError as error:
        errors.append(str(error))

    try:
        memory_used_bytes, memory_total_bytes = _parse_meminfo()
    except OSError as error:
        errors.append(str(error))

    try:
        disk_usage = shutil.disk_usage(path)
        disk_used_bytes = disk_usage.used
        disk_total_bytes = disk_usage.total
    except OSError as error:
        errors.append(str(error))

    try:
        (
            load_average_1m,
            load_average_5m,
            load_average_15m,
        ) = os.getloadavg()
    except OSError as error:
        errors.append(str(error))

    try:
        uptime_seconds = float(_read_text(Path("/proc/uptime")).split()[0])
    except (IndexError, OSError, ValueError) as error:
        errors.append(str(error))

    available = any(
        value is not None
        for value in (
            cpu_percent,
            memory_total_bytes,
            disk_total_bytes,
            load_average_1m,
            uptime_seconds,
        )
    )
    return HostMetrics(
        available=available,
        cpu_percent=cpu_percent,
        memory_used_bytes=memory_used_bytes,
        memory_total_bytes=memory_total_bytes,
        disk_used_bytes=disk_used_bytes,
        disk_total_bytes=disk_total_bytes,
        load_average_1m=load_average_1m,
        load_average_5m=load_average_5m,
        load_average_15m=load_average_15m,
        uptime_seconds=uptime_seconds,
        error="; ".join(error for error in errors if error),
    )
