"""Compatibility facade for server-log, host, and process metrics."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from armactl import (
    host_metrics,
    server_fps_metrics,
    server_incidents,
    server_log_diagnostics,
    server_log_io,
    server_operational_status,
)
from armactl.metric_formatting import (
    format_bytes as format_bytes,
)
from armactl.metric_formatting import (
    format_cpu_percent as format_cpu_percent,
)
from armactl.metric_formatting import (
    format_duration as format_duration,
)
from armactl.metric_formatting import (
    format_fps as format_fps,
)
from armactl.metric_formatting import (
    format_frame_time_ms as format_frame_time_ms,
)
from armactl.metric_formatting import (
    format_load_average as format_load_average,
)
from armactl.metric_models import (
    HostMetrics,
    ProcessMetrics,
    ServerFpsMetrics,
    ServerIncident,
    ServerOperationalStatus,
)

FPS_STATS_RE = server_fps_metrics.FPS_STATS_RE
WORKSHOP_ADDON_NOT_FOUND_RE = server_log_diagnostics.WORKSHOP_ADDON_NOT_FOUND_RE
OPERATIONAL_STATUS_TAIL_LINES = (
    server_operational_status.OPERATIONAL_STATUS_TAIL_LINES
)
SERVER_FPS_CRITICAL_THRESHOLD = (
    server_operational_status.SERVER_FPS_CRITICAL_THRESHOLD
)
SERVER_FPS_DEGRADED_THRESHOLD = (
    server_operational_status.SERVER_FPS_DEGRADED_THRESHOLD
)
SERVER_FPS_CRITICAL_SAMPLE_COUNT = (
    server_operational_status.SERVER_FPS_CRITICAL_SAMPLE_COUNT
)
OPERATIONAL_STATUS_DETAIL_MAX_CHARS = (
    server_log_diagnostics.OPERATIONAL_STATUS_DETAIL_MAX_CHARS
)
_all_log_lines = server_log_diagnostics.all_log_lines
_safe_operational_detail = server_log_diagnostics.safe_operational_detail
_line_has_game_destroyed = server_log_diagnostics.line_has_game_destroyed
_line_has_runtime_crash = server_log_diagnostics.line_has_runtime_crash
_line_has_startup_failure = server_log_diagnostics.line_has_startup_failure


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _latest_console_log(config_dir: Path) -> Path | None:
    return server_log_io.latest_console_log(
        config_dir,
        recent=_recent_console_logs,
    )


def _recent_console_logs(config_dir: Path, limit: int = 3) -> list[Path]:
    return server_log_io.recent_console_logs(config_dir, limit)


def query_server_fps_metrics(
    config_dir: str | Path,
    max_age_seconds: float = 45.0,
) -> ServerFpsMetrics:
    """Parse FPS telemetry through the focused server-log parser."""
    return server_fps_metrics.query_server_fps_metrics(
        config_dir,
        max_age_seconds,
        latest_log=_latest_console_log,
        read_tail=_read_tail_text_file,
        getmtime=os.path.getmtime,
        now=time.time,
    )


CONSOLE_LOG_TAIL_BYTES = server_log_io.CONSOLE_LOG_TAIL_BYTES
RECENT_INCIDENT_LOG_LIMIT = server_incidents.RECENT_INCIDENT_LOG_LIMIT
RECENT_INCIDENT_MAX_ITEMS = server_incidents.RECENT_INCIDENT_MAX_ITEMS
RECENT_INCIDENT_MAX_AGE_SECONDS = server_incidents.RECENT_INCIDENT_MAX_AGE_SECONDS


def _read_tail_text_file(
    path: Path,
    max_bytes: int = CONSOLE_LOG_TAIL_BYTES,
) -> str:
    """Read a bounded console-log tail through the shared filesystem helper."""
    return server_log_io.read_tail_text_file(path, max_bytes)


def query_recent_server_incidents(
    config_dir: str | Path,
    *,
    max_incidents: int = RECENT_INCIDENT_MAX_ITEMS,
    max_age_seconds: float = RECENT_INCIDENT_MAX_AGE_SECONDS,
    max_log_files: int = RECENT_INCIDENT_LOG_LIMIT,
) -> tuple[ServerIncident, ...]:
    """Return bounded recent incidents through the focused log analyzer."""
    return server_incidents.query_recent_server_incidents(
        config_dir,
        max_incidents=max_incidents,
        max_age_seconds=max_age_seconds,
        max_log_files=max_log_files,
        recent_logs=_recent_console_logs,
        read_tail=_read_tail_text_file,
        getmtime=os.path.getmtime,
        clock=time.time,
    )


def query_server_operational_status(
    config_dir: str | Path,
    max_age_seconds: float = 120.0,
) -> ServerOperationalStatus:
    """Infer lifecycle state through the focused server-log classifier."""
    return server_operational_status.query_server_operational_status(
        config_dir,
        max_age_seconds,
        latest_log=_latest_console_log,
        recent_logs=_recent_console_logs,
        read_tail=_read_tail_text_file,
        getmtime=os.path.getmtime,
        clock=time.time,
    )


def _cpu_count() -> int:
    """Compatibility seam for tests and legacy metric callers."""
    return host_metrics.cpu_count()


def _page_size() -> int:
    """Compatibility seam for tests and legacy metric callers."""
    return host_metrics.page_size()


def _parse_meminfo() -> tuple[int | None, int | None]:
    """Compatibility seam for tests and legacy metric callers."""
    return host_metrics.parse_meminfo(read=_read_text)


def _read_host_cpu_sample() -> tuple[int, int] | None:
    """Compatibility seam for tests and legacy metric callers."""
    return host_metrics.read_host_cpu_sample(read=_read_text)


def estimate_host_cpu_percent(sample_seconds: float = 0.05) -> float | None:
    """Estimate host CPU utilization through the focused collector."""
    return host_metrics.estimate_host_cpu_percent(
        sample_seconds,
        sample=_read_host_cpu_sample,
        sleep=time.sleep,
    )


def estimate_service_cpu_percent(service_status: dict[str, Any]) -> float | None:
    """Estimate service CPU utilization through the focused collector."""
    return host_metrics.estimate_service_cpu_percent(
        service_status,
        elapsed=service_elapsed_seconds,
        cpus=_cpu_count,
    )


def service_elapsed_seconds(service_status: dict[str, Any]) -> float | None:
    """Return service elapsed time through the focused collector."""
    return host_metrics.service_elapsed_seconds(service_status, read=_read_text)


def query_process_metrics(pid: int) -> ProcessMetrics:
    """Return process metrics through the focused collector."""
    return host_metrics.query_process_metrics(
        pid,
        read=_read_text,
        cpus=_cpu_count,
        pages=_page_size,
    )


def query_service_runtime_metrics(service_status: dict[str, Any]) -> ProcessMetrics:
    """Return service metrics through the focused collector."""
    return host_metrics.query_service_runtime_metrics(
        service_status,
        process_metrics=query_process_metrics,
        service_cpu=estimate_service_cpu_percent,
    )


def query_host_metrics(path: str | Path = "/") -> HostMetrics:
    """Return host metrics through the focused collector."""
    return host_metrics.query_host_metrics(
        path,
        host_cpu=estimate_host_cpu_percent,
        meminfo=_parse_meminfo,
        disk_usage=shutil.disk_usage,
        load_average=os.getloadavg,
        read=_read_text,
    )
