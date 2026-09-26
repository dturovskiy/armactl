"""Infer and load bounded operator-facing Reforger server incidents."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armactl.metric_models import ServerIncident
from armactl.server_fps_metrics import FPS_STATS_RE
from armactl.server_log_diagnostics import (
    OPERATIONAL_STATUS_DETAIL_MAX_CHARS,
    WORKSHOP_ADDON_NOT_FOUND_RE,
    all_log_lines,
    line_has_game_destroyed,
    line_has_runtime_crash,
    line_has_startup_failure,
    safe_operational_detail,
)

RECENT_INCIDENT_LOG_LIMIT = 12
RECENT_INCIDENT_MAX_ITEMS = 5
RECENT_INCIDENT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
RECENT_INCIDENT_CONTEXT_LINES = 240
RECENT_INCIDENT_EVIDENCE_MAX_ITEMS = 6
RECENT_INCIDENT_REPEAT_WINDOW_SECONDS = 24 * 60 * 60

RecentLogs = Callable[[Path, int], list[Path]]
ReadTail = Callable[[Path], str]
GetMtime = Callable[[Path], float]
Clock = Callable[[], float]
LinePredicate = Callable[[str], bool]


def _incident_timestamp(path: Path, *, getmtime: GetMtime) -> str:
    modified = getmtime(path)
    return datetime.fromtimestamp(modified, tz=timezone.utc).replace(microsecond=0).isoformat()


def _last_line_index(lines: list[str], predicate: LinePredicate) -> int | None:
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
    missing_addon = WORKSHOP_ADDON_NOT_FOUND_RE.search(joined)
    has_kornet = any(
        marker in joined
        for marker in ("Tripod_KORNET", "CLBR_KORNET", "Pod_Kornet", "KORNETNOOPTIC")
    )
    has_stugna = "Stugna" in joined
    has_remote_turret = "CLBR_RemoteTurretDriveComponent" in joined
    has_drone_bullet_class = "SAL_DroneBulletComponent" in joined
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

    if not runtime_crash and missing_addon is not None:
        mod_id = missing_addon.group("mod_id").upper()
        return (
            f"Workshop addon {mod_id}",
            "high",
            "The active profile requires this addon, but the Workshop reported it as "
            "unavailable. Keep the profile inactive until the addon is restored or replaced.",
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
        return ("ATGM / CLBR weapon stack", "high", reason)

    if runtime_crash and has_drone_bullet_class:
        return (
            "Realistic Combat Drones / FPV dependency stack",
            "high",
            "The Realistic Combat Drones class SAL_DroneBulletComponent was unresolved "
            "immediately before the native crash. This strongly identifies the drone "
            "dependency path; the final native fault may still be inside Enfusion.",
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
    categories: tuple[LinePredicate, ...] = (
        lambda line: any(
            marker in line
            for marker in ("Tripod_KORNET", "Pod_Kornet", "Stugna", "Mi24", "Mi-24")
        )
        and ("SpawnEntityPrefab" in line or "Create entity" in line),
        lambda line: any(
            marker in line
            for marker in ("CLBR_KORNET", "CLBR_RemoteTurretDriveComponent")
        ),
        lambda line: any(
            marker in line
            for marker in (
                "Wrong GUID/name for resource",
                "incompatible ammo",
                "Unknown class",
                "Unknown type",
                "no function with this name",
                "Addon loading failed",
                "Addon was not found on workshop",
                "Cannot create game",
            )
        ),
        lambda line: "[PERSISTENCE] Save" in line,
        line_has_runtime_crash,
        line_has_game_destroyed,
    )
    selected: dict[int, str] = {}
    for predicate in categories:
        index = _last_line_index(window, predicate)
        if index is not None:
            selected[index] = window[index]
    if not selected and window:
        bounded_index = min(max(terminal_index, 0), len(window) - 1)
        selected[bounded_index] = window[bounded_index]
    ordered = [selected[index] for index in sorted(selected)]
    if len(ordered) > RECENT_INCIDENT_EVIDENCE_MAX_ITEMS:
        ordered = ordered[-RECENT_INCIDENT_EVIDENCE_MAX_ITEMS:]
    return tuple(safe_operational_detail(line) for line in ordered)


def _incident_from_console_log(
    path: Path,
    *,
    read_tail: ReadTail,
    getmtime: GetMtime,
) -> ServerIncident | None:
    text = read_tail(path)
    lines = all_log_lines(text)
    if not lines:
        return None

    crash_index = _last_line_index(lines, line_has_runtime_crash)
    runtime_crash = crash_index is not None
    terminal_index = crash_index
    kind = "runtime_crash"
    summary = "Native game crash (crash dump)"

    if terminal_index is None:
        startup_failure_index = _last_line_index(lines, line_has_startup_failure)
        game_destroyed_index = _last_line_index(lines, line_has_game_destroyed)
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
    suspect, confidence, reason = _incident_signature(window, runtime_crash=runtime_crash)
    return ServerIncident(
        occurred_at=_incident_timestamp(path, getmtime=getmtime),
        kind=kind,
        severity="error",
        summary=summary,
        suspect=suspect,
        confidence=confidence,
        reason=reason,
        evidence=_incident_evidence(window, terminal_index - window_start),
    )


def _collected_incident_from_metadata(
    path: Path,
    *,
    read_tail: ReadTail,
    getmtime: GetMtime,
) -> ServerIncident | None:
    """Load one bounded collector record without exposing arbitrary bundle data."""
    try:
        if path.is_symlink() or path.stat().st_size > 512 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None

    occurred_at = str(value.get("occurred_at") or "")
    try:
        datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return None

    def safe(value: Any, limit: int = OPERATIONAL_STATUS_DETAIL_MAX_CHARS) -> str:
        return safe_operational_detail(str(value or ""))[:limit]

    raw_evidence = value.get("evidence")
    if not isinstance(raw_evidence, list):
        raw_evidence = []
    evidence = tuple(
        safe(item)
        for item in raw_evidence[:RECENT_INCIDENT_EVIDENCE_MAX_ITEMS]
        if isinstance(item, str) and safe(item)
    )
    suspect = safe(value.get("suspect")) or "Unknown"
    confidence = safe(value.get("confidence"), 24) or "low"
    reason = safe(value.get("reason"), 500)
    if suspect in {
        "Enfusion runtime / active addon stack",
        "Unknown native engine crash",
        "Unknown",
    }:
        console_path = path.parent / "engine" / "console.log"
        try:
            inferred = (
                None
                if console_path.is_symlink()
                else _incident_from_console_log(
                    console_path,
                    read_tail=read_tail,
                    getmtime=getmtime,
                )
            )
        except OSError:
            inferred = None
        if inferred is not None and inferred.suspect not in {
            "Unknown native engine crash",
            "Unknown",
        }:
            suspect = inferred.suspect
            confidence = inferred.confidence
            reason = inferred.reason
            merged_evidence = list(evidence)
            for item in inferred.evidence:
                if item not in merged_evidence:
                    merged_evidence.append(item)
            evidence = tuple(merged_evidence[-RECENT_INCIDENT_EVIDENCE_MAX_ITEMS:])
    raw_artifacts = value.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raw_artifacts = []
    artifacts = tuple(
        safe(item, 160)
        for item in raw_artifacts[:32]
        if isinstance(item, str) and safe(item, 160)
    )
    try:
        pid = max(int(value.get("pid") or 0), 0)
    except (TypeError, ValueError):
        pid = 0
    try:
        occurrence_count = max(int(value.get("occurrence_count") or 1), 1)
    except (TypeError, ValueError):
        occurrence_count = 1
    severity = safe(value.get("severity"), 24)
    if severity not in {"error", "warning", "info", "success"}:
        severity = "error"
    return ServerIncident(
        occurred_at=occurred_at,
        kind=safe(value.get("kind"), 80) or "runtime_crash",
        severity=severity,
        summary=safe(value.get("summary")) or "Captured server incident",
        suspect=suspect,
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        source="collector",
        incident_id=safe(value.get("id"), 160),
        captured_at=safe(value.get("captured_at"), 80),
        bundle=safe(value.get("bundle"), 200),
        confirmed=bool(value.get("confirmed")),
        pid=pid,
        artifacts=artifacts,
        first_seen_at=safe(value.get("first_seen_at"), 80) or occurred_at,
        last_seen_at=safe(value.get("last_seen_at"), 80) or occurred_at,
        occurrence_count=occurrence_count,
    )


def _query_collected_incidents(
    config_dir: Path,
    *,
    max_age_seconds: float,
    max_incidents: int,
    now: float,
    read_tail: ReadTail,
    getmtime: GetMtime,
) -> list[ServerIncident]:
    root = config_dir.parent / "incidents"
    try:
        candidates = [
            item / "metadata.json"
            for item in root.iterdir()
            if item.is_dir() and not item.is_symlink()
        ]
    except OSError:
        return []
    incidents: list[ServerIncident] = []
    for metadata in sorted(candidates, key=lambda item: item.parent.name, reverse=True):
        incident = _collected_incident_from_metadata(
            metadata,
            read_tail=read_tail,
            getmtime=getmtime,
        )
        if incident is None:
            continue
        try:
            occurred = datetime.fromisoformat(
                incident.occurred_at.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            continue
        if max(now - occurred, 0.0) > max_age_seconds:
            continue
        incidents.append(incident)
        if len(incidents) >= max(max_incidents, 0):
            break
    return incidents


def _incident_duplicates_collected(
    inferred: ServerIncident,
    collected: list[ServerIncident],
) -> bool:
    inferred_evidence = set(inferred.evidence)
    if not inferred_evidence:
        return False
    return any(inferred_evidence.intersection(item.evidence) for item in collected)


def _missing_workshop_addon_id(incident: ServerIncident) -> str:
    if incident.kind != "startup_failure":
        return ""
    text = "\n".join((incident.suspect, incident.reason, *incident.evidence))
    match = WORKSHOP_ADDON_NOT_FOUND_RE.search(text)
    if match is not None:
        return match.group("mod_id").upper()
    if "workshop addon" not in incident.suspect.casefold():
        return ""
    fallback = re.search(r"\b[0-9A-Fa-f]{16}\b", incident.suspect)
    return fallback.group(0).upper() if fallback is not None else ""


def _coalesce_repeated_startup_incidents(
    incidents: list[ServerIncident],
) -> list[ServerIncident]:
    """Collapse one deterministic Workshop outage without deleting evidence bundles."""
    result: list[ServerIncident] = []
    grouped: dict[str, int] = {}
    for incident in incidents:
        mod_id = _missing_workshop_addon_id(incident)
        if not mod_id or mod_id not in grouped:
            if mod_id:
                grouped[mod_id] = len(result)
            result.append(incident)
            continue

        index = grouped[mod_id]
        current = result[index]
        current_latest = _parse_incident_time(current.last_seen_at or current.occurred_at)
        candidate_latest = _parse_incident_time(incident.last_seen_at or incident.occurred_at)
        if (
            current_latest is None
            or candidate_latest is None
            or abs(current_latest - candidate_latest) > RECENT_INCIDENT_REPEAT_WINDOW_SECONDS
        ):
            grouped[mod_id] = len(result)
            result.append(incident)
            continue

        timestamps = [
            value
            for value in (
                current.first_seen_at or current.occurred_at,
                current.last_seen_at or current.occurred_at,
                incident.first_seen_at or incident.occurred_at,
                incident.last_seen_at or incident.occurred_at,
            )
            if _parse_incident_time(value) is not None
        ]
        first_seen = min(timestamps, key=lambda value: _parse_incident_time(value) or 0.0)
        last_seen = max(timestamps, key=lambda value: _parse_incident_time(value) or 0.0)
        evidence = tuple(dict.fromkeys((*current.evidence, *incident.evidence)))[
            -RECENT_INCIDENT_EVIDENCE_MAX_ITEMS:
        ]
        suspect = (
            current.suspect
            if not current.suspect.startswith("Workshop addon ")
            else incident.suspect
        )
        result[index] = replace(
            current,
            occurred_at=first_seen,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            occurrence_count=current.occurrence_count + incident.occurrence_count,
            summary="Workshop addon unavailable",
            suspect=suspect,
            confidence="high",
            evidence=evidence,
        )
    return result


def _parse_incident_time(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def query_recent_server_incidents(
    config_dir: str | Path,
    *,
    max_incidents: int,
    max_age_seconds: float,
    max_log_files: int,
    recent_logs: RecentLogs,
    read_tail: ReadTail,
    getmtime: GetMtime,
    clock: Clock,
) -> tuple[ServerIncident, ...]:
    """Return bounded recent crash/startup incidents without changing server state."""
    if max_incidents <= 0 or max_log_files <= 0:
        return ()
    now = clock()
    config_path = Path(config_dir)
    collected = _query_collected_incidents(
        config_path,
        max_age_seconds=max_age_seconds,
        max_incidents=max_incidents,
        now=now,
        read_tail=read_tail,
        getmtime=getmtime,
    )
    inferred: list[ServerIncident] = []
    for candidate in recent_logs(config_path, max_log_files):
        try:
            if max(now - getmtime(candidate), 0.0) > max_age_seconds:
                continue
            incident = _incident_from_console_log(
                candidate,
                read_tail=read_tail,
                getmtime=getmtime,
            )
        except OSError:
            continue
        if incident is not None and not _incident_duplicates_collected(incident, collected):
            inferred.append(incident)
        if len(inferred) >= max(max_incidents, 0):
            break
    incidents = collected + inferred
    incidents.sort(key=lambda item: item.occurred_at, reverse=True)
    incidents = _coalesce_repeated_startup_incidents(incidents)
    return tuple(incidents[: max(max_incidents, 0)])
