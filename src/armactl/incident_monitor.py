"""Persistent, non-restarting evidence collection for game-server incidents.

The monitor deliberately does not recover or restart the game.  Its only job is
to preserve the evidence that otherwise disappears when systemd starts a fresh
server process.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.service_manager import service_unit_name

MONITOR_SCHEMA_VERSION: Final = 1
MONITOR_STATE_FILENAME: Final = ".monitor-state.json"
MONITOR_STATUS_FILENAME: Final = "monitor-status.json"
MONITOR_LOOKBACK: Final = "24 hours ago"
MONITOR_STALE_TELEMETRY_SECONDS: Final = 90
MONITOR_MAX_JOURNAL_BYTES: Final = 8 * 1024 * 1024
MONITOR_SCAN_TAIL_BYTES: Final = 64 * 1024
MONITOR_MAX_LOG_TAIL_BYTES: Final = 256 * 1024
MONITOR_MAX_RECENT_FINGERPRINTS: Final = 512
MONITOR_MAX_INCIDENTS: Final = 200
MONITOR_CORRELATION_SECONDS: Final = 10 * 60

_FAILURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "memory_corruption",
        re.compile(
            r"double free or corruption|malloc\(\):.*corrupt|free\(\): invalid pointer|"
            r"corrupted size|invalid next size",
            re.IGNORECASE,
        ),
    ),
    (
        "segmentation_fault",
        re.compile(
            r"SIGSEGV|segmentation fault|signal 11|status\s*=\s*11/SEGV",
            re.IGNORECASE,
        ),
    ),
    (
        "runtime_crash",
        re.compile(r"Application crashed!|Generated memory dump", re.IGNORECASE),
    ),
    (
        "hang",
        re.compile(r"Application hangs \(force crash\)", re.IGNORECASE),
    ),
    (
        "startup_failure",
        re.compile(
            r"Unable to initialize the game|Cannot create game|Addon loading failed|"
            r"Can't compile [\"']Game[\"'] script module",
            re.IGNORECASE,
        ),
    ),
)

_KIND_PRIORITY: Final = {
    "memory_corruption": 5,
    "segmentation_fault": 4,
    "runtime_crash": 3,
    "hang": 2,
    "startup_failure": 1,
    "telemetry_hang_suspected": 0,
}

_ENGINE_LINE_TIME_RE: Final = re.compile(
    r"^\s*(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,6}))?"
)


@dataclass(frozen=True)
class CommandOutput:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class IncidentSignal:
    kind: str
    occurred_at: str
    message: str
    pid: int = 0
    cursor: str = ""
    source: str = "journal"
    context: tuple[str, ...] = ()


@dataclass(frozen=True)
class IncidentMonitorResult:
    instance: str
    success: bool
    captured: int
    updated: int
    ignored: int
    incident_ids: tuple[str, ...]
    status_path: str
    error: str = ""

    @property
    def exit_code(self) -> int:
        return 0 if self.success else 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CommandRunner = Callable[[Sequence[str]], CommandOutput]


def _utc_now(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat()


def _parse_timestamp(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _safe_line(value: Any, *, limit: int = 500) -> str:
    text = redact_sensitive_text(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _safe_version(value: Any) -> str:
    text = str(value or "").strip()
    return text[:64] if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", text) else ""


def _run_command(args: Sequence[str]) -> CommandOutput:
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return CommandOutput(1, stderr=_safe_line(error))
    stdout = completed.stdout[-MONITOR_MAX_JOURNAL_BYTES:]
    stderr = completed.stderr[-4096:]
    return CommandOutput(completed.returncode, stdout=stdout, stderr=stderr)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_size > 512 * 1024:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)


def _write_evidence(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    safe = redact_sensitive_text(text).replace("\x00", "")
    if len(safe.encode("utf-8", errors="replace")) > MONITOR_MAX_LOG_TAIL_BYTES:
        safe = safe.encode("utf-8", errors="replace")[-MONITOR_MAX_LOG_TAIL_BYTES:].decode(
            "utf-8", errors="replace"
        )
        safe = "[armactl retained only the bounded tail]\n" + safe
    path.write_text(safe.rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o640)


def _systemd_snapshot(unit: str, runner: CommandRunner) -> dict[str, Any]:
    properties = (
        "ActiveState,SubState,MainPID,NRestarts,Result,ExecMainCode,ExecMainStatus,"
        "ExecMainStartTimestamp,ExecMainExitTimestamp,LimitCORE,LimitCORESoft"
    )
    result = runner(("systemctl", "show", unit, f"--property={properties}"))
    snapshot: dict[str, Any] = {"unit": unit, "available": result.returncode == 0}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"MainPID", "NRestarts", "ExecMainStatus"}:
            try:
                snapshot[key] = int(value)
            except ValueError:
                snapshot[key] = 0
        else:
            snapshot[key] = _safe_line(value, limit=240)
    if result.returncode:
        snapshot["error"] = _safe_line(result.stderr or "systemctl show failed")
    return snapshot


def _journal_records(
    unit: str,
    *,
    cursor: str,
    runner: CommandRunner,
) -> tuple[list[dict[str, Any]], str, str]:
    command = [
        "journalctl",
        "--unit",
        unit,
        "--output=json",
        "--no-pager",
        "--lines=20000",
    ]
    if cursor:
        command.append(f"--after-cursor={cursor}")
    else:
        command.extend(("--since", MONITOR_LOOKBACK))
    result = runner(tuple(command))
    if result.returncode:
        return [], cursor, _safe_line(result.stderr or "journalctl failed")

    records: list[dict[str, Any]] = []
    newest_cursor = cursor
    for raw_line in result.stdout.splitlines():
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        record_cursor = str(record.get("__CURSOR") or "")
        if record_cursor:
            newest_cursor = record_cursor
        records.append(record)
    return records, newest_cursor, ""


def _record_timestamp(record: Mapping[str, Any]) -> str:
    raw = record.get("_SOURCE_REALTIME_TIMESTAMP") or record.get("__REALTIME_TIMESTAMP")
    try:
        return _utc_now(int(str(raw)) / 1_000_000)
    except (TypeError, ValueError):
        return _utc_now()


def _kind_for_message(message: str) -> str:
    for kind, pattern in _FAILURE_PATTERNS:
        if pattern.search(message):
            return kind
    return ""


def _relevant_context(messages: Sequence[str], signal_index: int) -> tuple[str, ...]:
    start = max(signal_index - 30, 0)
    end = min(signal_index + 6, len(messages))
    return tuple(_safe_line(line) for line in messages[start:end] if _safe_line(line))


def _signals_from_journal(records: Sequence[Mapping[str, Any]]) -> list[IncidentSignal]:
    messages = [str(record.get("MESSAGE") or "") for record in records]
    signals: list[IncidentSignal] = []
    for index, (record, message) in enumerate(zip(records, messages, strict=True)):
        kind = _kind_for_message(message)
        if not kind:
            continue
        try:
            pid = int(str(record.get("_PID") or 0))
        except ValueError:
            pid = 0
        signals.append(
            IncidentSignal(
                kind=kind,
                occurred_at=_record_timestamp(record),
                message=_safe_line(message),
                pid=max(pid, 0),
                cursor=str(record.get("__CURSOR") or ""),
                source="journal",
                context=_relevant_context(messages, index),
            )
        )
    return signals


def _latest_log_directory(config_dir: Path) -> Path | None:
    logs_dir = config_dir / "logs"
    try:
        candidates = [
            item for item in logs_dir.iterdir() if item.is_dir() and not item.is_symlink()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda item: item.stat().st_mtime)
    except OSError:
        return None


def _tail_text(path: Path, max_bytes: int = MONITOR_MAX_LOG_TAIL_BYTES) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        if size > max_bytes:
            handle.seek(-max_bytes, os.SEEK_END)
            data = handle.read()
            first_newline = data.find(b"\n")
            if first_newline >= 0:
                data = data[first_newline + 1 :]
        else:
            data = handle.read()
    return data.decode("utf-8", errors="replace")


def _tail_lines_with_offsets(path: Path, max_bytes: int) -> list[tuple[int, str]]:
    """Return a bounded append-only log tail with stable absolute byte offsets."""
    if path.is_symlink() or not path.is_file():
        return []
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        start = max(size - max_bytes, 0)
        handle.seek(start)
        data = handle.read()
    if start:
        first_newline = data.find(b"\n")
        if first_newline < 0:
            return []
        consumed = first_newline + 1
        start += consumed
        data = data[consumed:]

    lines: list[tuple[int, str]] = []
    offset = start
    for raw_line in data.splitlines(keepends=True):
        message = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
        lines.append((offset, message))
        offset += len(raw_line)
    return lines


def _engine_line_occurred_at(message: str, *, file_mtime: float) -> str:
    """Resolve an engine time-of-day against the closest UTC day to the file mtime."""
    match = _ENGINE_LINE_TIME_RE.match(message)
    if match is None:
        return _utc_now(file_mtime)
    try:
        fraction = (match.group("fraction") or "").ljust(6, "0")
        anchor = datetime.fromtimestamp(file_mtime, tz=timezone.utc)
        candidate = anchor.replace(
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
            microsecond=int(fraction or 0),
        ).timestamp()
    except (OverflowError, OSError, ValueError):
        return _utc_now(file_mtime)
    candidates = (candidate - 86400, candidate, candidate + 86400)
    return _utc_now(min(candidates, key=lambda value: abs(value - file_mtime)))


def _signals_from_engine_logs(log_dir: Path | None) -> list[IncidentSignal]:
    if log_dir is None:
        return []
    signals: list[IncidentSignal] = []
    for name in ("console.log", "script.log", "error.log", "crash.log"):
        path = log_dir / name
        try:
            lines_with_offsets = _tail_lines_with_offsets(path, MONITOR_SCAN_TAIL_BYTES)
            file_mtime = path.stat().st_mtime
        except OSError:
            continue
        lines = [message for _, message in lines_with_offsets]
        for index, (offset, message) in enumerate(lines_with_offsets):
            kind = _kind_for_message(message)
            if not kind:
                continue
            signals.append(
                IncidentSignal(
                    kind=kind,
                    occurred_at=_engine_line_occurred_at(message, file_mtime=file_mtime),
                    message=_safe_line(message),
                    cursor=f"{log_dir.name}/{name}:{offset}",
                    source=f"engine:{name}",
                    context=_relevant_context(lines, index),
                )
            )
    return signals


def _signal_fingerprint(signal: IncidentSignal) -> str:
    if signal.cursor:
        material = "\x1f".join((signal.kind, signal.source, signal.cursor))
        return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()
    if signal.kind == "telemetry_hang_suspected":
        material = f"{signal.kind}\x1f{signal.pid}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
    material = "\x1f".join(
        (signal.kind, signal.occurred_at, str(signal.pid), signal.source, signal.message)
    )
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def _profile_snapshot(config_file: Path, log_dir: Path | None) -> dict[str, Any]:
    config = _read_json_object(config_file)
    update_state = _read_json_object(
        config_file.parent.parent / "server-update" / "state.json"
    )
    game = config.get("game") if isinstance(config.get("game"), dict) else {}
    mods = game.get("mods") if isinstance(game.get("mods"), list) else []
    safe_mods: list[dict[str, str]] = []
    for value in mods[:500]:
        if not isinstance(value, dict):
            continue
        safe_mods.append(
            {
                "modId": _safe_line(value.get("modId"), limit=64),
                "name": _safe_line(value.get("name"), limit=160),
                "version": _safe_version(value.get("version")),
            }
        )

    version = ""
    if log_dir is not None:
        try:
            console = _tail_text(log_dir / "console.log")
        except OSError:
            console = ""
        matches = re.findall(r"Creating game instance.*?version\s+([^,\s]+)", console)
        if matches:
            version = _safe_version(matches[-1])

    return {
        "profile_name": _safe_line(update_state.get("active_profile"), limit=160),
        "profile_mode": _safe_line(update_state.get("active_mode"), limit=32),
        "active_build": _safe_line(update_state.get("active_build"), limit=64),
        "scenario_id": _safe_line(game.get("scenarioId"), limit=240),
        "server_name": _safe_line(game.get("name"), limit=240),
        "max_players": game.get("maxPlayers") if isinstance(game.get("maxPlayers"), int) else None,
        "mods": safe_mods,
        "mod_count": len(mods),
        "game_version": version,
    }


def _process_snapshot(pid: int) -> tuple[dict[str, Any], dict[str, str]]:
    summary: dict[str, Any] = {"pid": pid, "available": False}
    artifacts: dict[str, str] = {}
    if pid <= 0:
        return summary, artifacts
    proc = Path("/proc") / str(pid)
    try:
        if not proc.is_dir():
            return summary, artifacts
        summary["available"] = True
        for name in (
            "status",
            "stat",
            "statm",
            "limits",
            "maps",
            "smaps_rollup",
            "cgroup",
            "mountinfo",
        ):
            try:
                artifacts[f"process/{name}.txt"] = (proc / name).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as error:
                artifacts[f"process/{name}.txt"] = f"unavailable: {_safe_line(error)}"
        thread_rows: list[dict[str, Any]] = []
        tasks = sorted((proc / "task").iterdir(), key=lambda item: item.name)[:256]
        for task in tasks:
            if not task.name.isdigit():
                continue
            row: dict[str, Any] = {"tid": int(task.name)}
            for name in ("comm", "wchan", "syscall", "stack"):
                try:
                    row[name] = _safe_line(
                        (task / name).read_text(encoding="utf-8", errors="replace"),
                        limit=400,
                    )
                except OSError as error:
                    row[name] = f"unavailable: {_safe_line(error)}"
            thread_rows.append(row)
        artifacts["process/threads.json"] = json.dumps(
            thread_rows, ensure_ascii=False, indent=2
        )
        summary["thread_count"] = len(thread_rows)
    except OSError as error:
        summary["error"] = _safe_line(error)
    return summary, artifacts


def _core_capture_status(service: Mapping[str, Any]) -> dict[str, Any]:
    status = {
        "service_limit_core": service.get("LimitCORE", ""),
        "service_limit_core_soft": service.get("LimitCORESoft", ""),
        "gdb_available": shutil.which("gdb") is not None,
        "coredumpctl_available": shutil.which("coredumpctl") is not None,
    }
    try:
        status["kernel_core_pattern"] = _safe_line(
            Path("/proc/sys/kernel/core_pattern").read_text(encoding="utf-8"),
            limit=240,
        )
    except OSError as error:
        status["kernel_core_pattern_error"] = _safe_line(error)
    return status


def _assessment(
    kind: str,
    *,
    context: Sequence[str] = (),
    runtime: Mapping[str, Any] | None = None,
) -> tuple[str, str, str, str]:
    joined = "\n".join(context).lower()
    active_mods = (runtime or {}).get("mods")
    active_mod_ids = {
        str(item.get("modId") or "").upper()
        for item in active_mods
        if isinstance(item, dict)
    } if isinstance(active_mods, list) else set()
    if kind in {"memory_corruption", "segmentation_fault", "runtime_crash", "hang"}:
        if any(marker in joined for marker in ("clbr_", "kornet", "stugna")):
            return (
                "Native game crash" if kind != "hang" else "Game thread hang confirmed",
                "ATGM / CLBR weapon stack",
                "high",
                "ATGM/CLBR prefab or script activity occurred in the captured failure window. "
                "This is a strong trigger correlation; the native core remains authoritative.",
            )
        if (
            any(marker in joined for marker in ("game master", "gamemaster"))
            and active_mod_ids.intersection({"64F10E068D5880A6", "5AAAC70D754245DD"})
        ):
            return (
                "Native memory corruption" if kind == "memory_corruption" else "Native game crash",
                "Game Master cleanup / admin-mod interaction",
                "medium",
                "The failure followed Game Master activity while GM/admin extensions were "
                "active. This narrows the reproduction profile but does not prove which "
                "extension or engine cleanup path owns the native fault.",
            )
        if "m777" in joined:
            return (
                "Native game crash" if kind != "hang" else "Game thread hang confirmed",
                "M777 entity / active addon interaction",
                "medium",
                "M777 entity activity occurred in the captured failure window. This is a "
                "candidate trigger, not proof of the native owner.",
            )
    if kind == "memory_corruption":
        return (
            "Native memory corruption",
            "Enfusion native heap / addon-triggered engine path",
            "high",
            "The allocator reported invalid heap ownership (for example, a double free). "
            "This identifies the failure mechanism; a native backtrace or core is still "
            "required to name the exact engine function or triggering addon.",
        )
    if kind == "segmentation_fault":
        return (
            "Native segmentation fault",
            "Enfusion native process or addon-triggered engine path",
            "high",
            "The server received SIGSEGV. Nearby prefab/script evidence is correlation; "
            "the captured native backtrace or core is authoritative when available.",
        )
    if kind == "runtime_crash":
        return (
            "Native game crash",
            "Enfusion runtime / active addon stack",
            "medium",
            "The engine reported a runtime crash. The evidence bundle preserves the active "
            "profile and adjacent engine messages for attribution.",
        )
    if kind == "hang":
        return (
            "Game thread hang confirmed",
            "Enfusion main game thread stall",
            "high",
            "The engine watchdog reported a forced crash after a prolonged stall. Process "
            "and thread state is captured when the original PID is still available.",
        )
    if kind == "telemetry_hang_suspected":
        return (
            "Live game hang suspected",
            "Active process stopped producing engine telemetry",
            "medium",
            "systemd still reports a live process, but periodic engine telemetry stopped. "
            "This early snapshot is retained before an operator or watchdog terminates it.",
        )
    return (
        "Server startup failed",
        "Addon, scenario, or backend startup path",
        "medium",
        "The game reported a terminal startup error before stable telemetry. The active "
        "scenario, mod list, and adjacent errors are retained in the evidence bundle.",
    )


def _important_evidence(signal: IncidentSignal) -> list[str]:
    markers = (
        "double free",
        "corrupt",
        "SIGSEGV",
        "segmentation fault",
        "Application crashed",
        "Application hangs",
        "Game Master",
        "GameMaster",
        "SpawnEntityPrefab",
        "Create entity",
        "CLBR_",
        "KORNET",
        "Stugna",
        "Addon loading failed",
        "Cannot create game",
        "Can't compile",
    )
    selected = [
        line
        for line in signal.context
        if any(marker.lower() in line.lower() for marker in markers)
    ]
    selected.append(signal.message)
    result: list[str] = []
    for line in selected:
        safe = _safe_line(line)
        if safe and safe not in result:
            result.append(safe)
    return result[-12:]


def _incident_id(signal: IncidentSignal) -> str:
    occurred = _parse_timestamp(signal.occurred_at) or time.time()
    stamp = datetime.fromtimestamp(occurred, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = _signal_fingerprint(signal)[:10]
    pid = str(signal.pid) if signal.pid else "unknown"
    return f"{stamp}-{signal.kind}-{pid}-{digest}"


def _incident_directories(root: Path) -> Iterable[Path]:
    try:
        candidates = [item for item in root.iterdir() if item.is_dir() and not item.is_symlink()]
    except OSError:
        return ()
    return sorted(candidates, key=lambda item: item.name, reverse=True)[:MONITOR_MAX_INCIDENTS]


def _correlatable_bundle(root: Path, signal: IncidentSignal) -> Path | None:
    signal_time = _parse_timestamp(signal.occurred_at)
    if signal_time is None:
        return None
    for candidate in _incident_directories(root):
        metadata = _read_json_object(candidate / "metadata.json")
        if signal.pid:
            if metadata.get("pid") != signal.pid:
                continue
        elif signal.kind not in {"hang", "runtime_crash", "segmentation_fault"}:
            continue
        occurred = _parse_timestamp(str(metadata.get("occurred_at") or ""))
        if occurred is not None and abs(signal_time - occurred) <= MONITOR_CORRELATION_SECONDS:
            return candidate
    return None


def _merge_journal_context(previous: str, current: Sequence[str]) -> str:
    """Keep correlated context useful without appending identical lines each cycle."""
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*previous.splitlines(), *current]:
        line = _safe_line(value, limit=2000)
        if not line or line in seen:
            continue
        seen.add(line)
        merged.append(line)
    return "\n".join(merged)


def _retained_artifact_names(bundle: Path, metadata: Mapping[str, Any]) -> list[str]:
    """Retain only existing safe bundle-relative artifacts from an earlier capture."""
    raw_names = metadata.get("artifacts")
    if not isinstance(raw_names, list):
        return []
    retained: list[str] = []
    for value in raw_names:
        if not isinstance(value, str) or not value or len(value) > 200:
            continue
        relative = Path(value)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            continue
        current = bundle
        unsafe = False
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                unsafe = True
                break
        if unsafe:
            continue
        try:
            target = (bundle / relative).resolve(strict=True)
            target.relative_to(bundle)
        except (OSError, ValueError):
            continue
        if target.is_file() and not target.is_symlink():
            retained.append(relative.as_posix())
    return retained


def _capture_bundle(
    root: Path,
    signal: IncidentSignal,
    *,
    service: Mapping[str, Any],
    config_file: Path,
    log_dir: Path | None,
    now: float,
) -> tuple[str, bool]:
    existing = _correlatable_bundle(root, signal)
    incident_id = existing.name if existing is not None else _incident_id(signal)
    bundle = existing or (root / incident_id)
    if bundle.is_symlink():
        raise OSError("incident bundle path must not be a symlink")
    resolved_root = root.resolve(strict=False)
    resolved_bundle = bundle.resolve(strict=False)
    resolved_bundle.relative_to(resolved_root)
    bundle = resolved_bundle
    bundle.mkdir(parents=True, exist_ok=True, mode=0o750)
    metadata_path = bundle / "metadata.json"
    previous = _read_json_object(metadata_path)
    previous_kind = str(previous.get("kind") or "")
    kind = (
        signal.kind
        if _KIND_PRIORITY.get(signal.kind, -1) >= _KIND_PRIORITY.get(previous_kind, -1)
        else previous_kind
    )
    runtime = _profile_snapshot(config_file, log_dir)
    summary, suspect, confidence, reason = _assessment(
        kind,
        context=signal.context,
        runtime=runtime,
    )
    evidence = [str(item) for item in previous.get("evidence", []) if isinstance(item, str)]
    for item in _important_evidence(signal):
        if item not in evidence:
            evidence.append(item)
    evidence = evidence[-16:]

    try:
        previous_pid = max(int(previous.get("pid") or 0), 0)
    except (TypeError, ValueError):
        previous_pid = 0
    pid = max(signal.pid, 0) or previous_pid
    try:
        active_pid = int(service.get("MainPID") or 0)
    except (TypeError, ValueError):
        active_pid = 0
    process, process_artifacts = _process_snapshot(pid if pid == active_pid else 0)
    if pid and pid != active_pid:
        process.update(
            {
                "incident_pid": pid,
                "available": False,
                "reason": "Original incident PID is no longer the active game-service PID.",
            }
        )
    artifact_names = _retained_artifact_names(bundle, previous)

    journal_name = "journal.log"
    previous_journal = ""
    try:
        previous_journal = (bundle / journal_name).read_text(encoding="utf-8")
    except OSError:
        pass
    journal_lines = list(signal.context) or [signal.message]
    _write_evidence(
        bundle / journal_name,
        _merge_journal_context(previous_journal, journal_lines),
    )
    artifact_names.append(journal_name)

    if log_dir is not None:
        for name in ("console.log", "script.log", "error.log", "crash.log"):
            source = log_dir / name
            try:
                text = _tail_text(source)
            except OSError:
                continue
            if not text and not source.exists():
                continue
            target_name = f"engine/{name}"
            _write_evidence(bundle / target_name, text or "[empty at capture time]")
            artifact_names.append(target_name)

    for name, text in process_artifacts.items():
        _write_evidence(bundle / name, text)
        artifact_names.append(name)

    service_path = bundle / "service.json"
    _atomic_write_json(service_path, dict(service))
    artifact_names.append("service.json")
    runtime["process"] = process
    runtime["core_capture"] = _core_capture_status(service)
    runtime_path = bundle / "runtime.json"
    _atomic_write_json(runtime_path, runtime)
    artifact_names.append("runtime.json")

    captured_at = str(previous.get("captured_at") or _utc_now(now))
    metadata = {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "id": incident_id,
        "occurred_at": str(previous.get("occurred_at") or signal.occurred_at),
        "captured_at": captured_at,
        "updated_at": _utc_now(now),
        "kind": kind,
        "severity": "error",
        "summary": summary,
        "suspect": suspect,
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence,
        "source": "collector",
        "confirmed": kind != "telemetry_hang_suspected",
        "pid": pid,
        "signals": sorted(
            set(
                [str(item) for item in previous.get("signals", []) if isinstance(item, str)]
                + [signal.kind]
            )
        ),
        "signal_sources": sorted(
            set(
                [
                    str(item)
                    for item in previous.get("signal_sources", [])
                    if isinstance(item, str)
                ]
                + [signal.source]
            )
        ),
        "profile": runtime,
        "artifacts": sorted(set(artifact_names)),
        "bundle": f"incidents/{incident_id}",
    }
    _atomic_write_json(metadata_path, metadata)
    return incident_id, existing is not None


def _latest_console_mtime(config_dir: Path) -> float | None:
    log_dir = _latest_log_directory(config_dir)
    if log_dir is None:
        return None
    try:
        return (log_dir / "console.log").stat().st_mtime
    except OSError:
        return None


def _stale_signal(
    *,
    service: Mapping[str, Any],
    config_dir: Path,
    state: dict[str, Any],
    now: float,
) -> IncidentSignal | None:
    if service.get("ActiveState") != "active" or service.get("SubState") != "running":
        return None
    try:
        pid = int(service.get("MainPID") or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None

    if int(state.get("observed_pid") or 0) != pid:
        state["observed_pid"] = pid
        state["observed_pid_since"] = _utc_now(now)
        return None
    observed_since = _parse_timestamp(str(state.get("observed_pid_since") or "")) or now
    if now - observed_since < MONITOR_STALE_TELEMETRY_SECONDS:
        return None

    mtime = _latest_console_mtime(config_dir)
    if mtime is None or now - mtime <= MONITOR_STALE_TELEMETRY_SECONDS:
        return None
    age = int(max(now - mtime, 0))
    return IncidentSignal(
        kind="telemetry_hang_suspected",
        occurred_at=_utc_now(mtime + MONITOR_STALE_TELEMETRY_SECONDS),
        message=(
            f"Engine telemetry stopped updating {age} seconds ago while PID {pid} "
            "remained active."
        ),
        pid=pid,
        source="monitor:stale-telemetry",
        context=(
            f"systemd state: active/running; MainPID={pid}",
            f"console.log telemetry age: {age} seconds",
        ),
    )


def read_monitor_status(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> dict[str, Any]:
    """Read the public-safe monitor heartbeat without creating any state."""
    normalized = paths.validate_instance_name(instance)
    root = paths.incidents_dir(normalized, data_root)
    status = _read_json_object(root / MONITOR_STATUS_FILENAME)
    try:
        count = sum(1 for _ in _incident_directories(root))
    except OSError:
        count = 0
    return {
        "available": bool(status),
        "last_run_at": str(status.get("last_run_at") or ""),
        "last_success_at": str(status.get("last_success_at") or ""),
        "last_signal_at": str(status.get("last_signal_at") or ""),
        "last_error": _safe_line(status.get("last_error")),
        "journal_available": status.get("journal_available"),
        "telemetry_age_seconds": status.get("telemetry_age_seconds"),
        "incident_count": count,
        "storage": str(root),
    }


def collect_incidents_once(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    runner: CommandRunner | None = None,
    now: float | None = None,
) -> IncidentMonitorResult:
    """Capture all new bounded evidence and return without changing game state."""
    normalized = paths.validate_instance_name(instance)
    timestamp = time.time() if now is None else now
    execute = runner or _run_command
    root = paths.incidents_dir(normalized, data_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    state_path = root / MONITOR_STATE_FILENAME
    status_path = root / MONITOR_STATUS_FILENAME
    state = _read_json_object(state_path)
    seen = [str(value) for value in state.get("seen", []) if isinstance(value, str)]
    seen_set = set(seen)

    unit = service_unit_name(normalized)
    service = _systemd_snapshot(unit, execute)
    records, newest_cursor, journal_error = _journal_records(
        unit,
        cursor=str(state.get("journal_cursor") or ""),
        runner=execute,
    )
    signals = _signals_from_journal(records)
    log_dir = _latest_log_directory(paths.config_dir(normalized, data_root))
    signals.extend(_signals_from_engine_logs(log_dir))
    stale = _stale_signal(
        service=service,
        config_dir=paths.config_dir(normalized, data_root),
        state=state,
        now=timestamp,
    )
    if stale is not None:
        signals.append(stale)

    captured = 0
    updated = 0
    ignored = 0
    incident_ids: list[str] = []
    for signal in signals:
        fingerprint = _signal_fingerprint(signal)
        if fingerprint in seen_set:
            ignored += 1
            continue
        incident_id, was_updated = _capture_bundle(
            root,
            signal,
            service=service,
            config_file=paths.config_file(normalized, data_root),
            log_dir=log_dir,
            now=timestamp,
        )
        incident_ids.append(incident_id)
        if was_updated:
            updated += 1
        else:
            captured += 1
        seen.append(fingerprint)
        seen_set.add(fingerprint)

    if newest_cursor:
        state["journal_cursor"] = newest_cursor
    state["seen"] = seen[-MONITOR_MAX_RECENT_FINGERPRINTS:]
    state["last_run_at"] = _utc_now(timestamp)
    if not journal_error:
        state["last_success_at"] = _utc_now(timestamp)
    if incident_ids:
        state["last_signal_at"] = _utc_now(timestamp)
    state["last_error"] = journal_error
    _atomic_write_json(state_path, state)

    console_mtime = _latest_console_mtime(paths.config_dir(normalized, data_root))
    telemetry_age = int(max(timestamp - console_mtime, 0)) if console_mtime is not None else None
    status = {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "last_run_at": state["last_run_at"],
        "last_success_at": state.get("last_success_at", ""),
        "last_signal_at": state.get("last_signal_at", ""),
        "last_error": journal_error,
        "journal_available": not bool(journal_error),
        "telemetry_age_seconds": telemetry_age,
        "service": service,
        "captured": captured,
        "updated": updated,
        "ignored": ignored,
        "storage": str(root),
        "core_capture": _core_capture_status(service),
    }
    _atomic_write_json(status_path, status)
    return IncidentMonitorResult(
        instance=normalized,
        success=not bool(journal_error),
        captured=captured,
        updated=updated,
        ignored=ignored,
        incident_ids=tuple(incident_ids),
        status_path=str(status_path),
        error=journal_error,
    )
