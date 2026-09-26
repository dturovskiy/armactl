"""Arma Reforger ``-logStats`` FPS telemetry parsing."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from armactl.metric_models import ServerFpsMetrics
from armactl.server_log_io import latest_console_log, read_tail_text_file

FPS_STATS_RE = re.compile(
    r"FPS:\s*(?P<fps>\d+(?:\.\d+)?),\s*"
    r"frame time\s*\(\s*avg:\s*(?P<frame_avg>\d+(?:\.\d+)?)\s*ms,\s*"
    r"min:\s*(?P<frame_min>\d+(?:\.\d+)?)\s*ms,\s*"
    r"max:\s*(?P<frame_max>\d+(?:\.\d+)?)\s*ms(?:,\s*[^)]*)?\s*\),\s*"
    r"Mem:\s*(?P<memory_kb>\d+)\s*kB,\s*"
    r"Player:\s*(?P<players>\d+),\s*"
    r"AI:\s*(?P<ai>\d+),\s*"
    r"AIChar:\s*(?P<ai_char>\d+)"
)

LatestLogProbe = Callable[[Path], Path | None]
TailReader = Callable[[Path], str]
MtimeProbe = Callable[[Path], float]
Clock = Callable[[], float]


def query_server_fps_metrics(
    config_dir: str | Path,
    max_age_seconds: float = 45.0,
    *,
    latest_log: LatestLogProbe = latest_console_log,
    read_tail: TailReader = read_tail_text_file,
    getmtime: MtimeProbe = os.path.getmtime,
    now: Clock = time.time,
) -> ServerFpsMetrics:
    """Parse real server FPS/frame-time metrics from the latest console log."""
    log_path = latest_log(Path(config_dir))
    if log_path is None:
        return ServerFpsMetrics(
            False,
            error="server FPS telemetry log is not available",
        )

    source = str(log_path)
    try:
        log_mtime = getmtime(log_path)
        text = read_tail(log_path)
    except OSError as error:
        return ServerFpsMetrics(False, source=source, error=str(error))

    match: re.Match[str] | None = None
    for line in text.splitlines():
        line_match = FPS_STATS_RE.search(line)
        if line_match is not None:
            match = line_match

    if match is None:
        return ServerFpsMetrics(
            False,
            source=source,
            error="server FPS telemetry line is not available",
        )

    try:
        age_seconds = max(now() - log_mtime, 0.0)
        stale = age_seconds > max_age_seconds
        return ServerFpsMetrics(
            available=not stale,
            fps=float(match.group("fps")),
            frame_avg_ms=float(match.group("frame_avg")),
            frame_min_ms=float(match.group("frame_min")),
            frame_max_ms=float(match.group("frame_max")),
            engine_memory_kb=int(match.group("memory_kb")),
            players=int(match.group("players")),
            ai=int(match.group("ai")),
            ai_char=int(match.group("ai_char")),
            age_seconds=age_seconds,
            source=source,
            stale=stale,
            error="server FPS telemetry is stale" if stale else "",
        )
    except (IndexError, ValueError) as error:
        return ServerFpsMetrics(False, source=source, error=str(error))
