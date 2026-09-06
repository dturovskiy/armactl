"""Linux process metrics for armactl status views."""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armactl.redaction import redact_sensitive_text


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


@dataclass
class ServerFpsMetrics:
    """Real Arma Reforger engine FPS metrics parsed from -logStats output."""

    available: bool
    fps: float | None = None
    frame_avg_ms: float | None = None
    frame_min_ms: float | None = None
    frame_max_ms: float | None = None
    engine_memory_kb: int | None = None
    players: int | None = None
    ai: int | None = None
    ai_char: int | None = None
    age_seconds: float | None = None
    source: str = ""
    stale: bool = False
    error: str = ""


@dataclass
class ServerOperationalStatus:
    """Best-effort lifecycle state parsed from recent server log lines."""

    available: bool
    state: str = "unknown"
    severity: str = "info"
    message: str = "Unknown"
    details: tuple[str, ...] = ()
    age_seconds: float | None = None
    source: str = ""
    error: str = ""


@dataclass(frozen=True)
class ServerIncident:
    """A bounded, operator-facing explanation of a recent server exit."""

    occurred_at: str
    kind: str
    severity: str
    summary: str
    suspect: str
    confidence: str
    reason: str
    evidence: tuple[str, ...] = ()


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


def format_fps(value: float | None) -> str:
    """Format a real server FPS value parsed from engine telemetry."""
    if value is None:
        return "Unknown"
    return f"{value:.1f}"


def format_frame_time_ms(value: float | None) -> str:
    """Format a frame time in milliseconds."""
    if value is None:
        return "Unknown"
    return f"{value:.1f} ms"


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


def _latest_console_log(config_dir: Path) -> Path | None:
    logs = _recent_console_logs(config_dir, limit=1)
    return logs[0] if logs else None


def _recent_console_logs(config_dir: Path, *, limit: int = 3) -> list[Path]:
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


def query_server_fps_metrics(
    config_dir: str | Path,
    max_age_seconds: float = 45.0,
) -> ServerFpsMetrics:
    """Parse real server FPS/frame-time metrics from the latest -logStats console log."""
    latest_log = _latest_console_log(Path(config_dir))
    if latest_log is None:
        return ServerFpsMetrics(
            False,
            error="server FPS telemetry log is not available",
        )

    source = str(latest_log)
    try:
        log_mtime = os.path.getmtime(latest_log)
        text = _read_tail_text_file(latest_log)
    except OSError as error:
        return ServerFpsMetrics(False, source=source, error=str(error))

    match = None
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
        age_seconds = max(time.time() - log_mtime, 0.0)
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


OPERATIONAL_STATUS_TAIL_LINES = 300
CONSOLE_LOG_TAIL_BYTES = 512 * 1024
OPERATIONAL_STATUS_DETAIL_MAX_CHARS = 180
OPERATIONAL_STATUS_DETAIL_MAX_ITEMS = 4
RECENT_INCIDENT_LOG_LIMIT = 12
RECENT_INCIDENT_MAX_ITEMS = 5
RECENT_INCIDENT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
RECENT_INCIDENT_CONTEXT_LINES = 240
RECENT_INCIDENT_EVIDENCE_MAX_ITEMS = 6


def _read_tail_text_file(
    path: Path,
    max_bytes: int = CONSOLE_LOG_TAIL_BYTES,
) -> str:
    """Read a bounded tail from a text file while preserving recent complete lines."""
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


def _tail_recent_log_lines(
    text: str,
    max_lines: int = OPERATIONAL_STATUS_TAIL_LINES,
) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-max_lines:]


def _all_log_lines(text: str) -> list[str]:
    """Return non-empty lines from an already bounded console-log tail."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _safe_operational_detail(line: str) -> str:
    """Return an operator-safe, bounded status diagnostic line."""
    text = redact_sensitive_text(line).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= OPERATIONAL_STATUS_DETAIL_MAX_CHARS:
        return text
    return f"{text[: OPERATIONAL_STATUS_DETAIL_MAX_CHARS - 1].rstrip()}…"


def _safe_operational_details(lines: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    details = [
        detail
        for detail in (
            _safe_operational_detail(line)
            for line in lines[-OPERATIONAL_STATUS_DETAIL_MAX_ITEMS:]
        )
        if detail
    ]
    return tuple(details)


def _incident_timestamp(path: Path) -> str:
    modified = os.path.getmtime(path)
    return datetime.fromtimestamp(modified, tz=timezone.utc).replace(microsecond=0).isoformat()


def _last_line_index(lines: list[str], predicate: Any) -> int | None:
    return next(
        (index for index in range(len(lines) - 1, -1, -1) if predicate(lines[index])),
        None,
    )


def _incident_signature(
    window: list[str],
    *,
    runtime_crash: bool,
) -> tuple[str, str, str]:
    joined = "\n".join(window)
    has_kornet = any(
        marker in joined
        for marker in ("Tripod_KORNET", "CLBR_KORNET", "Pod_Kornet", "KORNETNOOPTIC")
    )
    has_stugna = "Stugna" in joined
    has_remote_turret = "CLBR_RemoteTurretDriveComponent" in joined
    has_mi24 = any(marker in joined for marker in ("Mi24", "Mi-24", "Mi_24"))
    has_persistence = "[PERSISTENCE] Save" in joined
    has_addon_resource_error = any(
        marker in joined
        for marker in (
            "Wrong GUID/name for resource",
            "Addon loading failed",
            "Can't compile \"Game\" script module",
            "no function with this name",
        )
    )

    if runtime_crash and (has_kornet or has_stugna or has_remote_turret):
        reason = (
            "Kornet prefab and CLBR weapon code appeared immediately before the native "
            "crash. This is a strong correlation; the final fault may still be inside "
            "the Enfusion engine."
            if has_kornet
            else "Stugna/remote-turret prefab and CLBR weapon code appeared immediately "
            "before the native crash. This is a strong correlation; the final fault may "
            "still be inside the Enfusion engine."
        )
        return (
            "ATGM / CLBR weapon stack",
            "high",
            reason,
        )

    if runtime_crash and has_mi24:
        return (
            "WCS Mi-24 / addon integration",
            "medium",
            "Mi-24 addon activity appeared shortly before the native engine crash, but "
            "the available log does not identify the exact failing component.",
        )

    if has_addon_resource_error:
        return (
            "Addon resource or script compatibility",
            "medium",
            "Addon resource/script errors occurred before the process exited. Review the "
            "listed evidence and test the affected profile on a canary server.",
        )

    if runtime_crash and has_persistence:
        return (
            "Scenario persistence / mod object interaction",
            "medium",
            "The native crash followed a persistence save, but the log does not name a "
            "single responsible addon.",
        )

    if runtime_crash:
        return (
            "Unknown native engine crash",
            "low",
            "The Reforger process produced a crash dump, but the bounded log context does "
            "not contain a reliable addon or scenario signature.",
        )

    return (
        "Addon, scenario, or backend startup failure",
        "medium" if has_addon_resource_error else "low",
        "The game exited before reaching stable telemetry. The evidence below contains "
        "the last actionable startup errors found in the server log.",
    )


def _incident_evidence(window: list[str], terminal_index: int) -> tuple[str, ...]:
    categories = (
        lambda line: any(
            marker in line
            for marker in (
                "Tripod_KORNET",
                "Pod_Kornet",
                "Stugna",
                "Mi24",
                "Mi-24",
            )
        )
        and ("SpawnEntityPrefab" in line or "Create entity" in line),
        lambda line: "CLBR_" in line,
        lambda line: any(
            marker in line
            for marker in (
                "Wrong GUID/name for resource",
                "incompatible ammo",
                "Unknown type",
                "no function with this name",
                "Addon loading failed",
                "Cannot create game",
            )
        ),
        lambda line: "[PERSISTENCE] Save" in line,
        lambda line: _line_has_runtime_crash(line),
        lambda line: _line_has_game_destroyed(line),
    )
    selected: dict[int, str] = {}
    for predicate in categories:
        index = _last_line_index(window, predicate)
        if index is not None:
            selected[index] = window[index]
    if not selected and window:
        selected[min(max(terminal_index, 0), len(window) - 1)] = window[
            min(max(terminal_index, 0), len(window) - 1)
        ]
    ordered = [selected[index] for index in sorted(selected)]
    if len(ordered) > RECENT_INCIDENT_EVIDENCE_MAX_ITEMS:
        ordered = ordered[-RECENT_INCIDENT_EVIDENCE_MAX_ITEMS:]
    return tuple(_safe_operational_detail(line) for line in ordered)


def _incident_from_console_log(path: Path) -> ServerIncident | None:
    text = _read_tail_text_file(path)
    lines = _all_log_lines(text)
    if not lines:
        return None

    crash_index = _last_line_index(lines, _line_has_runtime_crash)
    runtime_crash = crash_index is not None
    terminal_index = crash_index
    kind = "runtime_crash"
    summary = "Native game crash (crash dump)"

    if terminal_index is None:
        startup_failure_index = _last_line_index(lines, _line_has_startup_failure)
        game_destroyed_index = _last_line_index(lines, _line_has_game_destroyed)
        if startup_failure_index is None and game_destroyed_index is None:
            return None
        if startup_failure_index is None and any(FPS_STATS_RE.search(line) for line in lines):
            return None
        terminal_index = max(
            index
            for index in (startup_failure_index, game_destroyed_index)
            if index is not None
        )
        if any(FPS_STATS_RE.search(line) for line in lines[terminal_index + 1 :]):
            return None
        kind = "startup_failure"
        summary = "Server exited during startup"

    window_start = max(terminal_index - RECENT_INCIDENT_CONTEXT_LINES, 0)
    window = lines[window_start : terminal_index + 1]
    suspect, confidence, reason = _incident_signature(
        window,
        runtime_crash=runtime_crash,
    )
    return ServerIncident(
        occurred_at=_incident_timestamp(path),
        kind=kind,
        severity="error",
        summary=summary,
        suspect=suspect,
        confidence=confidence,
        reason=reason,
        evidence=_incident_evidence(window, terminal_index - window_start),
    )


def query_recent_server_incidents(
    config_dir: str | Path,
    *,
    max_incidents: int = RECENT_INCIDENT_MAX_ITEMS,
    max_age_seconds: float = RECENT_INCIDENT_MAX_AGE_SECONDS,
) -> tuple[ServerIncident, ...]:
    """Return bounded recent crash/startup incidents without changing server state."""
    if max_incidents <= 0:
        return ()
    now = time.time()
    incidents: list[ServerIncident] = []
    for candidate in _recent_console_logs(
        Path(config_dir),
        limit=RECENT_INCIDENT_LOG_LIMIT,
    ):
        try:
            if max(now - os.path.getmtime(candidate), 0.0) > max_age_seconds:
                continue
            incident = _incident_from_console_log(candidate)
        except OSError:
            continue
        if incident is not None:
            incidents.append(incident)
        if len(incidents) >= max(max_incidents, 0):
            break
    return tuple(incidents)


def _line_has_download_retry(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Fragmentizer: Retrying download",
            "Fragmentizer: Download error",
        )
    )


def _line_has_download_progress(line: str) -> bool:
    return (
        "Addon Download started" in line
        or "Download speed" in line
        or ("Downloading " in line and ("addons" in line or "version" in line))
        or re.search(r":\s*\[[=>_ ]+\]\s*\d+%", line) is not None
    )


def _line_has_mission_error(line: str) -> bool:
    return (
        "MissionHeader::ReadMissionHeader cannot load" in line
        or "cannot load the resource" in line
    )


def _line_has_startup_failure(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Unable to initialize the game",
            "Failed to fetch addon details from workshop API",
            'Can\'t compile "Game" script module',
            "Cannot create game",
            "Addon loading failed",
        )
    )


def _line_has_workshop_metadata_error(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Failed to fetch addon details from workshop API",
            "WorkshopApi/GetDownloadListS2S",
            "SSL connect error",
        )
    )


def _startup_failure_details(lines: list[str], index: int) -> tuple[str, ...]:
    window = lines[max(index - 12, 0) : index + 1]
    details = [
        line
        for line in window
        if (
            _line_has_startup_failure(line)
            or _line_has_workshop_metadata_error(line)
            or _line_has_mission_error(line)
            or "Unknown type" in line
            or "Unknown keyword/data" in line
            or "no function with this name" in line
            or "Failed to load" in line
        )
    ]
    return _safe_operational_details(tuple(details or [lines[index]]))


def _line_has_game_destroyed(line: str) -> bool:
    return "Game destroyed." in line or line.rstrip().endswith("Game destroyed")


def _line_has_runtime_crash(line: str) -> bool:
    return "Application crashed!" in line or "Generated memory dump" in line


def _runtime_crash_details(lines: list[str], index: int) -> tuple[str, ...]:
    window = lines[max(index - 40, 0) : index + 1]
    details = [
        line
        for line in window
        if (
            _line_has_runtime_crash(line)
            or _line_has_game_destroyed(line)
            or " (E):" in line
            or " (F):" in line
            or "Wrong GUID/name for resource" in line
            or "SIGSEGV" in line
        )
    ]
    return _safe_operational_details(tuple(details or [lines[index]]))


def _runtime_crash_status(
    lines: list[str],
    *,
    age_seconds: float,
    source: str,
) -> ServerOperationalStatus | None:
    crash_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if _line_has_runtime_crash(lines[index])
        ),
        None,
    )
    if crash_index is None:
        return None
    last_fps_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if FPS_STATS_RE.search(lines[index])
        ),
        None,
    )
    if last_fps_index is not None and crash_index < last_fps_index:
        return None
    return ServerOperationalStatus(
        True,
        state="runtime_crash",
        severity="error",
        message="Game process crashed",
        details=_runtime_crash_details(lines, crash_index),
        age_seconds=age_seconds,
        source=source,
    )


def _recent_previous_runtime_crash(
    config_dir: Path,
    *,
    latest_log: Path,
    max_age_seconds: float,
) -> ServerOperationalStatus | None:
    now = time.time()
    for candidate in _recent_console_logs(config_dir, limit=4):
        if candidate == latest_log:
            continue
        try:
            age_seconds = max(now - os.path.getmtime(candidate), 0.0)
            if age_seconds > max_age_seconds:
                continue
            text = _read_tail_text_file(candidate)
        except OSError:
            continue
        status = _runtime_crash_status(
            _all_log_lines(text),
            age_seconds=age_seconds,
            source=str(candidate),
        )
        if status is not None:
            return status
    return None


def _startup_exit_details(lines: list[str], index: int) -> tuple[str, ...]:
    window = lines[max(index - 12, 0) : index + 1]
    details = [
        line
        for line in window
        if (
            _line_has_game_destroyed(line)
            or _line_has_startup_failure(line)
            or _line_has_workshop_metadata_error(line)
            or _line_has_mission_error(line)
            or " (E):" in line
            or " (F):" in line
        )
    ]
    return _safe_operational_details(tuple(details or [lines[index]]))


def _line_has_starting_status(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Loading dedicated server config",
            "Game successfully created",
            "Starting dedicated server",
        )
    )


def _line_has_backend_heartbeat_failure(line: str) -> bool:
    return (
        "DS Room Heartbeat fail" in line
        or "DS Heartbeat Failing for too long" in line
    )


def _line_has_backend_heartbeat_terminal(line: str) -> bool:
    return "DS Heartbeat Failing for too long" in line


def _line_has_backend_connectivity_failure(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Curl error=Timeout was reached",
            "Curl error=Could not resolve hostname",
            "GameConfig/List Timeout",
            "GameConfig/List Error",
            "WorkshopApi/GetServers",
        )
    )


def _line_has_shutdown_marker(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "shutting down",
            "Save (SHUTDOWN) started",
            "Save (SHUTDOWN) completed",
            "Application hangs (force crash)",
            "Application crashed!",
        )
    )


def _latest_backend_incident_status(lines: list[str]) -> ServerOperationalStatus | None:
    last_fps_index: int | None = None
    for index, line in enumerate(lines):
        if FPS_STATS_RE.search(line):
            last_fps_index = index

    def after_latest_fps(index: int) -> bool:
        return last_fps_index is None or index > last_fps_index

    severe_index: int | None = None
    connectivity_index: int | None = None
    for index, line in enumerate(lines):
        if not after_latest_fps(index):
            continue
        if _line_has_backend_heartbeat_terminal(line):
            severe_index = index if severe_index is None else severe_index
        if (
            _line_has_backend_heartbeat_failure(line)
            or _line_has_backend_connectivity_failure(line)
        ):
            connectivity_index = index if connectivity_index is None else connectivity_index

    if severe_index is not None:
        details = [
            line
            for line in lines[max(severe_index - 12, 0) :]
            if (
                _line_has_backend_heartbeat_failure(line)
                or _line_has_backend_connectivity_failure(line)
                or _line_has_shutdown_marker(line)
            )
        ]
        return ServerOperationalStatus(
            True,
            state="backend_heartbeat_failure",
            severity="error",
            message="Backend heartbeat failure",
            details=_safe_operational_details(details),
        )

    if last_fps_index is None and connectivity_index is not None:
        details = [
            line
            for line in lines[max(connectivity_index - 6, 0) :]
            if _line_has_backend_connectivity_failure(line)
        ]
        return ServerOperationalStatus(
            True,
            state="backend_connectivity_issue",
            severity="warning",
            message="Backend connectivity issue",
            details=_safe_operational_details(details),
        )

    return None


def query_server_operational_status(
    config_dir: str | Path,
    max_age_seconds: float = 120.0,
) -> ServerOperationalStatus:
    """Return a user-facing lifecycle status from the latest console log."""
    latest_log = _latest_console_log(Path(config_dir))
    if latest_log is None:
        return ServerOperationalStatus(
            False,
            state="unknown",
            severity="warning",
            message="Telemetry log unavailable",
            error="server console log is not available",
        )

    source = str(latest_log)
    try:
        log_mtime = os.path.getmtime(latest_log)
        text = _read_tail_text_file(latest_log)
    except OSError as error:
        return ServerOperationalStatus(
            False,
            state="unknown",
            severity="warning",
            message="Telemetry log unavailable",
            source=source,
            error=str(error),
        )

    age_seconds = max(time.time() - log_mtime, 0.0)
    all_lines = _all_log_lines(text)
    lines = all_lines[-OPERATIONAL_STATUS_TAIL_LINES:]
    if not lines:
        return ServerOperationalStatus(
            False,
            state="unknown",
            severity="warning",
            message="Telemetry log unavailable",
            age_seconds=age_seconds,
            source=source,
            error="server console log is empty",
        )

    # A backend timeout can be the cause of a terminal startup failure.  Prefer
    # the later, actionable failure over the earlier connectivity warning.
    last_fps_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if FPS_STATS_RE.search(lines[index])
        ),
        None,
    )
    startup_failure_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if _line_has_startup_failure(lines[index])
            and (last_fps_index is None or index > last_fps_index)
        ),
        None,
    )
    if startup_failure_index is not None:
        details = _startup_failure_details(lines, startup_failure_index)
        message = (
            "Workshop addon metadata error"
            if any(_line_has_workshop_metadata_error(item) for item in details)
            else "Server startup failed"
        )
        return ServerOperationalStatus(
            True,
            state="startup_failed",
            severity="error",
            message=message,
            details=details,
            age_seconds=age_seconds,
            source=source,
        )

    backend_incident_status = _latest_backend_incident_status(all_lines)
    if backend_incident_status is not None:
        backend_incident_status.age_seconds = age_seconds
        backend_incident_status.source = source
        return backend_incident_status

    runtime_crash = _runtime_crash_status(
        lines,
        age_seconds=age_seconds,
        source=source,
    )
    if runtime_crash is not None:
        return runtime_crash

    startup_exit_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if _line_has_game_destroyed(lines[index])
            and (last_fps_index is None or index > last_fps_index)
        ),
        None,
    )
    if startup_exit_index is not None:
        return ServerOperationalStatus(
            True,
            state="startup_failed",
            severity="error",
            message="Server startup failed",
            details=_startup_exit_details(lines, startup_exit_index),
            age_seconds=age_seconds,
            source=source,
        )

    if last_fps_index is None:
        previous_crash = _recent_previous_runtime_crash(
            Path(config_dir),
            latest_log=latest_log,
            max_age_seconds=max_age_seconds,
        )
        if previous_crash is not None:
            return previous_crash

    if age_seconds > max_age_seconds:
        return ServerOperationalStatus(
            False,
            state="telemetry_stale",
            severity="warning",
            message="Telemetry stale",
            details=_safe_operational_details([lines[-1]]),
            age_seconds=age_seconds,
            source=source,
            error="server console log is stale",
        )

    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if FPS_STATS_RE.search(line):
            return ServerOperationalStatus(
                True,
                state="ready",
                severity="success",
                message="Ready",
                details=_safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if _line_has_download_retry(line):
            return ServerOperationalStatus(
                True,
                state="downloading_mods",
                severity="warning",
                message="Downloading mods (retrying)",
                details=_safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if _line_has_download_progress(line):
            return ServerOperationalStatus(
                True,
                state="downloading_mods",
                severity="warning",
                message="Downloading mods",
                details=_safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if _line_has_mission_error(line):
            return ServerOperationalStatus(
                True,
                state="mission_error",
                severity="error",
                message="Mission/config error",
                details=_safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if _line_has_starting_status(line):
            return ServerOperationalStatus(
                True,
                state="starting",
                severity="info",
                message="Starting",
                details=_safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

    for line in reversed(all_lines):
        if FPS_STATS_RE.search(line):
            return ServerOperationalStatus(
                True,
                state="ready",
                severity="success",
                message="Ready",
                details=_safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

    return ServerOperationalStatus(
        True,
        state="waiting_for_telemetry",
        severity="warning",
        message="Waiting for server telemetry",
        details=_safe_operational_details([lines[-1]]),
        age_seconds=age_seconds,
        source=source,
    )


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

    elapsed_seconds = service_elapsed_seconds(service_status)
    if elapsed_seconds is None:
        return None
    elapsed_usec = max(int(elapsed_seconds * 1_000_000), 1)
    elapsed_nsec = elapsed_usec * 1_000
    return (cpu_usage_nsec / elapsed_nsec) * 100.0 / _cpu_count()


def service_elapsed_seconds(service_status: dict[str, Any]) -> float | None:
    """Return elapsed time for the current systemd service process."""
    start_usec = service_status.get("exec_main_start_usec") or service_status.get(
        "active_enter_usec"
    )
    if not isinstance(start_usec, int) or start_usec <= 0:
        return None
    try:
        uptime_seconds = float(_read_text(Path("/proc/uptime")).split()[0])
    except (IndexError, OSError, ValueError):
        return None
    return max(uptime_seconds - (start_usec / 1_000_000), 0.0)


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
