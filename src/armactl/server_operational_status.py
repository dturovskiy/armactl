"""Infer the current Reforger server lifecycle and health from bounded logs."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from armactl.metric_formatting import format_duration
from armactl.metric_models import ServerOperationalStatus
from armactl.server_fps_metrics import FPS_STATS_RE
from armactl.server_log_diagnostics import (
    WORKSHOP_ADDON_NOT_FOUND_RE,
    all_log_lines,
    line_has_game_destroyed,
    line_has_mission_error,
    line_has_runtime_crash,
    line_has_startup_failure,
    line_has_workshop_metadata_error,
    safe_operational_details,
)

OPERATIONAL_STATUS_TAIL_LINES = 300
SERVER_FPS_CRITICAL_THRESHOLD = 10.0
SERVER_FPS_DEGRADED_THRESHOLD = 30.0
SERVER_FPS_CRITICAL_SAMPLE_COUNT = 3
SERVER_FPS_SAMPLE_LIMIT = 36

LatestLog = Callable[[Path], Path | None]
RecentLogs = Callable[[Path, int], list[Path]]
ReadTail = Callable[[Path], str]
GetMtime = Callable[[Path], float]
Clock = Callable[[], float]


@dataclass(frozen=True)
class _OperationalFpsSample:
    """One bounded telemetry sample used to classify live server health."""

    index: int
    fps: float
    players: int
    ai: int
    ai_char: int
    second_of_day: float | None


_ENGINE_TIME_OF_DAY_RE = re.compile(
    r"^\s*(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,6}))?"
)


def _engine_second_of_day(line: str) -> float | None:
    match = _ENGINE_TIME_OF_DAY_RE.match(line)
    if match is None:
        return None
    try:
        fraction = float(f"0.{match.group('fraction') or '0'}")
        return (
            int(match.group("hour")) * 3600
            + int(match.group("minute")) * 60
            + int(match.group("second"))
            + fraction
        )
    except (TypeError, ValueError):
        return None


def _operational_fps_samples(lines: list[str]) -> list[_OperationalFpsSample]:
    samples: list[_OperationalFpsSample] = []
    for index, line in enumerate(lines):
        match = FPS_STATS_RE.search(line)
        if match is None:
            continue
        try:
            samples.append(
                _OperationalFpsSample(
                    index=index,
                    fps=float(match.group("fps")),
                    players=int(match.group("players")),
                    ai=int(match.group("ai")),
                    ai_char=int(match.group("ai_char")),
                    second_of_day=_engine_second_of_day(line),
                )
            )
        except (IndexError, ValueError):
            continue
    return samples[-SERVER_FPS_SAMPLE_LIMIT:]


def _consecutive_samples_at_or_below(
    samples: list[_OperationalFpsSample],
    threshold: float,
) -> list[_OperationalFpsSample]:
    consecutive: list[_OperationalFpsSample] = []
    for sample in reversed(samples):
        if sample.fps > threshold:
            break
        consecutive.append(sample)
    consecutive.reverse()
    return consecutive


def _sample_duration_seconds(samples: list[_OperationalFpsSample]) -> float | None:
    if len(samples) < 2:
        return None
    start = samples[0].second_of_day
    end = samples[-1].second_of_day
    if start is None or end is None:
        return None
    if end < start:
        end += 24 * 60 * 60
    return max(end - start, 0.0)


def _low_fps_signals(lines: list[str], *, start_index: int) -> str:
    """Summarize bounded correlation signals without exposing arbitrary log text."""
    window = lines[max(start_index - 80, 0) :]
    resource_failures = sum(
        "RESOURCES" in line and "Failed to open" in line for line in window
    )
    has_gm_activity = any(
        (
            "Editor EDIT:" in line
            and any(marker in line.lower() for marker in ("spawned", "waypoint"))
        )
        or "Game Master" in line
        for line in window
    )
    has_loadout_errors = any(
        "WCS_LoadoutEditor" in line
        and any(marker in line for marker in ("Skipping item", "not found", "missing"))
        for line in window
    )
    has_fortex_references = any(
        marker in line for line in window for marker in ("FRTX_", "/FRTX", "FORTEX")
    )

    signals: list[str] = []
    if resource_failures:
        signals.append(f"resource load failures ({resource_failures})")
    if has_fortex_references:
        signals.append("FORTEX resource references")
    if has_gm_activity:
        signals.append("Game Master spawn/waypoint activity")
    if has_loadout_errors:
        signals.append("loadout/prefab compatibility errors")
    if not signals:
        return ""
    return f"Correlated signals: {'; '.join(signals)}."


def _fps_operational_status(
    lines: list[str],
    *,
    age_seconds: float,
    source: str,
) -> ServerOperationalStatus | None:
    """Classify fresh telemetry without calling every FPS sample healthy."""
    samples = _operational_fps_samples(lines)
    if not samples:
        return None
    latest = samples[-1]
    if latest.fps >= SERVER_FPS_DEGRADED_THRESHOLD:
        return None

    critical_samples = _consecutive_samples_at_or_below(
        samples,
        SERVER_FPS_CRITICAL_THRESHOLD,
    )
    degraded_samples = _consecutive_samples_at_or_below(
        samples,
        SERVER_FPS_DEGRADED_THRESHOLD,
    )
    is_critical = latest.fps <= 1.0 or (
        latest.fps <= SERVER_FPS_CRITICAL_THRESHOLD
        and len(critical_samples) >= SERVER_FPS_CRITICAL_SAMPLE_COUNT
    )
    relevant = critical_samples if is_critical else degraded_samples
    threshold = (
        SERVER_FPS_CRITICAL_THRESHOLD if is_critical else SERVER_FPS_DEGRADED_THRESHOLD
    )
    minimum = min(sample.fps for sample in relevant)
    duration = _sample_duration_seconds(relevant)
    duration_text = (
        f" over {format_duration(duration)}" if duration is not None else ""
    )
    details = [
        (
            f"FPS {latest.fps:.1f}; {len(relevant)} consecutive sample(s) at or below "
            f"{threshold:.1f} FPS{duration_text}; minimum {minimum:.1f} FPS."
        ),
        (
            f"Telemetry at detection: {latest.players} player(s), {latest.ai} AI, "
            f"{latest.ai_char} AI character(s)."
        ),
    ]
    signal = _low_fps_signals(lines, start_index=relevant[0].index)
    if signal:
        details.append(signal)
    return ServerOperationalStatus(
        True,
        state="fps_critical" if is_critical else "fps_degraded",
        severity="error" if is_critical else "warning",
        message="Critical server FPS" if is_critical else "Low server FPS",
        details=safe_operational_details(details),
        age_seconds=age_seconds,
        source=source,
    )


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


def _startup_failure_details(lines: list[str], index: int) -> tuple[str, ...]:
    window = lines[max(index - 12, 0) : index + 1]
    details = [
        line
        for line in window
        if (
            line_has_startup_failure(line)
            or line_has_workshop_metadata_error(line)
            or line_has_mission_error(line)
            or "Unknown type" in line
            or "Unknown keyword/data" in line
            or "no function with this name" in line
            or "Failed to load" in line
        )
    ]
    return safe_operational_details(tuple(details or [lines[index]]))


def _runtime_crash_details(lines: list[str], index: int) -> tuple[str, ...]:
    window = lines[max(index - 40, 0) : index + 1]
    details = [
        line
        for line in window
        if (
            line_has_runtime_crash(line)
            or line_has_game_destroyed(line)
            or " (E):" in line
            or " (F):" in line
            or "Wrong GUID/name for resource" in line
            or "SIGSEGV" in line
        )
    ]
    return safe_operational_details(tuple(details or [lines[index]]))


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
            if line_has_runtime_crash(lines[index])
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
    recent_logs: RecentLogs,
    read_tail: ReadTail,
    getmtime: GetMtime,
    now: float,
) -> ServerOperationalStatus | None:
    for candidate in recent_logs(config_dir, 4):
        if candidate == latest_log:
            continue
        try:
            age_seconds = max(now - getmtime(candidate), 0.0)
            if age_seconds > max_age_seconds:
                continue
            text = read_tail(candidate)
        except OSError:
            continue
        status = _runtime_crash_status(
            all_log_lines(text),
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
            line_has_game_destroyed(line)
            or line_has_startup_failure(line)
            or line_has_workshop_metadata_error(line)
            or line_has_mission_error(line)
            or " (E):" in line
            or " (F):" in line
        )
    ]
    return safe_operational_details(tuple(details or [lines[index]]))


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
            details=safe_operational_details(details),
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
            details=safe_operational_details(details),
        )

    return None


def query_server_operational_status(
    config_dir: str | Path,
    max_age_seconds: float,
    *,
    latest_log: LatestLog,
    recent_logs: RecentLogs,
    read_tail: ReadTail,
    getmtime: GetMtime,
    clock: Clock,
) -> ServerOperationalStatus:
    """Return a user-facing lifecycle status from the latest console log."""
    config_path = Path(config_dir)
    latest = latest_log(config_path)
    if latest is None:
        return ServerOperationalStatus(
            False,
            state="unknown",
            severity="warning",
            message="Telemetry log unavailable",
            error="server console log is not available",
        )

    source = str(latest)
    try:
        log_mtime = getmtime(latest)
        text = read_tail(latest)
    except OSError as error:
        return ServerOperationalStatus(
            False,
            state="unknown",
            severity="warning",
            message="Telemetry log unavailable",
            source=source,
            error=str(error),
        )

    current_time = clock()
    age_seconds = max(current_time - log_mtime, 0.0)
    complete_lines = all_log_lines(text)
    lines = complete_lines[-OPERATIONAL_STATUS_TAIL_LINES:]
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
            if line_has_startup_failure(lines[index])
            and (last_fps_index is None or index > last_fps_index)
        ),
        None,
    )
    if startup_failure_index is not None:
        details = _startup_failure_details(lines, startup_failure_index)
        missing_addon = next(
            (
                match
                for item in details
                if (match := WORKSHOP_ADDON_NOT_FOUND_RE.search(item)) is not None
            ),
            None,
        )
        if missing_addon is not None:
            message = f"Workshop addon unavailable: {missing_addon.group('mod_id').upper()}"
        elif any(line_has_workshop_metadata_error(item) for item in details):
            message = "Workshop addon metadata error"
        else:
            message = "Server startup failed"
        return ServerOperationalStatus(
            True,
            state="startup_failed",
            severity="error",
            message=message,
            details=details,
            age_seconds=age_seconds,
            source=source,
        )

    backend_incident_status = _latest_backend_incident_status(complete_lines)
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
            if line_has_game_destroyed(lines[index])
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
            config_path,
            latest_log=latest,
            max_age_seconds=max_age_seconds,
            recent_logs=recent_logs,
            read_tail=read_tail,
            getmtime=getmtime,
            now=current_time,
        )
        if previous_crash is not None:
            return previous_crash

    if age_seconds > max_age_seconds:
        return ServerOperationalStatus(
            False,
            state="telemetry_stale",
            severity="warning",
            message="Telemetry stale",
            details=safe_operational_details([lines[-1]]),
            age_seconds=age_seconds,
            source=source,
            error="server console log is stale",
        )

    fps_status = _fps_operational_status(
        complete_lines,
        age_seconds=age_seconds,
        source=source,
    )

    for line in reversed(lines):
        if FPS_STATS_RE.search(line):
            if fps_status is not None:
                return fps_status
            return ServerOperationalStatus(
                True,
                state="ready",
                severity="success",
                message="Ready",
                details=safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if _line_has_download_retry(line):
            return ServerOperationalStatus(
                True,
                state="downloading_mods",
                severity="warning",
                message="Downloading mods (retrying)",
                details=safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if _line_has_download_progress(line):
            return ServerOperationalStatus(
                True,
                state="downloading_mods",
                severity="warning",
                message="Downloading mods",
                details=safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if line_has_mission_error(line):
            return ServerOperationalStatus(
                True,
                state="mission_error",
                severity="error",
                message="Mission/config error",
                details=safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

        if _line_has_starting_status(line):
            return ServerOperationalStatus(
                True,
                state="starting",
                severity="info",
                message="Starting",
                details=safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

    for line in reversed(complete_lines):
        if FPS_STATS_RE.search(line):
            if fps_status is not None:
                return fps_status
            return ServerOperationalStatus(
                True,
                state="ready",
                severity="success",
                message="Ready",
                details=safe_operational_details([line]),
                age_seconds=age_seconds,
                source=source,
            )

    return ServerOperationalStatus(
        True,
        state="waiting_for_telemetry",
        severity="warning",
        message="Waiting for server telemetry",
        details=safe_operational_details([lines[-1]]),
        age_seconds=age_seconds,
        source=source,
    )
