from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat as stat_module
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from armactl import paths, player_log_collector
from armactl.player_log_collector import PlayerLogCollectionSummary
from armactl.web.services import player_registry
from armactl.web.services.player_identity import safe_player_text

PLAYER_LOG_INGEST_SCOPE: Final = "instance_config_profile_console_logs"
DEFAULT_MAX_LOG_FILES: Final = 32
PLAYER_LOG_INGEST_LOCK_FILENAME: Final = ".player-log-ingest.lock"
PLAYER_LOG_INGEST_FINGERPRINT_BYTES = 4096
_CONTROLLED_PARTIAL_SKIP_ERROR_CODES = frozenset({"file_too_large", "missing_file"})
_IPV4_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_BRACKETED_IPV6_ADDRESS_RE = re.compile(r"\[[0-9A-Fa-f:.]{2,}\](?::\d{1,5})?")


class _PlayerLogIngestBusyError(RuntimeError):
    """Internal signal that another process owns the ingest scope lock."""


@dataclass(frozen=True)
class PlayerLogIngestFileSnapshot:
    path: Path = field(repr=False, compare=False)
    source_key: str
    source_label: str
    size_bytes: int
    mtime_ns: int
    fingerprint: str


@dataclass(frozen=True)
class PlayerLogIngestPlan:
    scope: str
    files_considered: int
    files_to_scan: tuple[Path, ...]
    snapshots_to_scan: tuple[PlayerLogIngestFileSnapshot, ...]
    skipped_reason_counts: dict[str, int]
    checkpoint_reset_reason_counts: dict[str, int]

    @property
    def files_selected_for_scan(self) -> int:
        return len(self.files_to_scan)

    @property
    def skipped_files(self) -> int:
        return sum(self.skipped_reason_counts.values())


@dataclass(frozen=True)
class PlayerLogIngestResult:
    """Counts-only result from one synchronous allowlisted ingest pass."""

    instance: str
    scope: str = PLAYER_LOG_INGEST_SCOPE
    outcome: str = "completed"
    failure_code: str = ""
    files_considered: int = 0
    files_selected_for_scan: int = 0
    files_requested: int = 0
    files_scanned: int = 0
    files_skipped: int = 0
    scanned_lines: int = 0
    parsed_events: int = 0
    stored_events: int = 0
    duplicate_events: int = 0
    unmatched_lines: int = 0
    skipped_lines: int = 0
    error_count: int = 0
    skipped_reason_counts: Mapping[str, int] = field(default_factory=dict)
    checkpoint_reset_reason_counts: Mapping[str, int] = field(default_factory=dict)
    checkpoint_updated: bool = False
    freshness_status: str = player_registry.PLAYER_LOG_INGEST_STATUS_UNAVAILABLE
    freshness_at: str = ""
    collection_success: bool = False

    @property
    def completed(self) -> bool:
        return self.outcome == "completed"

    @property
    def busy(self) -> bool:
        return self.outcome == "busy"

    @property
    def success(self) -> bool:
        return self.completed and self.collection_success

    @property
    def exit_code(self) -> int:
        return 0 if self.success else 1

    @property
    def skipped_reasons(self) -> str:
        return format_reason_counts(self.skipped_reason_counts)

    @property
    def checkpoint_reset_reasons(self) -> str:
        return format_reason_counts(self.checkpoint_reset_reason_counts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "scope": self.scope,
            "outcome": self.outcome,
            "failure_code": self.failure_code,
            "files_considered": self.files_considered,
            "files_selected_for_scan": self.files_selected_for_scan,
            "files_requested": self.files_requested,
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "scanned_lines": self.scanned_lines,
            "parsed_events": self.parsed_events,
            "stored_events": self.stored_events,
            "duplicate_events": self.duplicate_events,
            "unmatched_lines": self.unmatched_lines,
            "skipped_lines": self.skipped_lines,
            "error_count": self.error_count,
            "skipped_reasons": self.skipped_reasons,
            "checkpoint_reset_reasons": self.checkpoint_reset_reasons,
            "checkpoint_updated": self.checkpoint_updated,
            "freshness_status": self.freshness_status,
            "freshness_at": self.freshness_at,
            "collection_success": self.collection_success,
            "success": self.success,
        }


@dataclass(frozen=True)
class PlayerLogIngestStatus:
    """Safe read-only freshness/checkpoint status for one instance."""

    instance: str
    state: str
    reason: str
    scope: str = PLAYER_LOG_INGEST_SCOPE
    freshness_status: str = player_registry.PLAYER_LOG_INGEST_STATUS_UNAVAILABLE
    last_run_at: str = ""
    last_success_at: str = ""
    updated_at: str = ""
    scanned_files: int = 0
    parsed_events: int = 0
    stored_events: int = 0
    skipped_files: int = 0
    skipped_reasons: str = ""
    checkpoint_updated: bool = False
    checkpoint_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "state": self.state,
            "reason": self.reason,
            "scope": self.scope,
            "freshness_status": self.freshness_status,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "updated_at": self.updated_at,
            "scanned_files": self.scanned_files,
            "parsed_events": self.parsed_events,
            "stored_events": self.stored_events,
            "skipped_files": self.skipped_files,
            "skipped_reasons": self.skipped_reasons,
            "checkpoint_updated": self.checkpoint_updated,
            "checkpoint_count": self.checkpoint_count,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validated_instance(instance: object) -> str:
    return paths.validate_instance_name(
        str(instance or paths.DEFAULT_INSTANCE_NAME)
    )


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _is_allowlisted_console_log(candidate: Path, logs_root: Path) -> bool:
    if candidate.name != "console.log":
        return False
    try:
        relative = candidate.relative_to(logs_root)
    except ValueError:
        return False
    if len(relative.parts) != 2:
        return False

    try:
        root_resolved = logs_root.resolve(strict=True)
        candidate_resolved = candidate.resolve(strict=True)
        candidate_resolved.relative_to(root_resolved)
        stat_result = candidate_resolved.stat()
    except (OSError, ValueError):
        return False
    return stat_module.S_ISREG(stat_result.st_mode)


def resolve_allowlisted_player_log_paths(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    max_files: int = DEFAULT_MAX_LOG_FILES,
) -> tuple[Path, ...]:
    """Return only instance config-profile console logs from the fixed allowlist."""
    if max_files < 1:
        return ()
    normalized_instance = _validated_instance(instance)
    logs_root = paths.config_dir(normalized_instance, data_root) / "logs"
    try:
        candidates = tuple(logs_root.glob("*/console.log"))
    except OSError:
        return ()

    allowlisted = tuple(
        candidate
        for candidate in candidates
        if _is_allowlisted_console_log(candidate, logs_root)
    )
    sorted_logs = sorted(
        allowlisted,
        key=lambda candidate: (_safe_mtime(candidate), candidate.name),
        reverse=True,
    )
    return tuple(sorted_logs[:max_files])


def player_log_ingest_lock_path(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> Path:
    return (
        paths.instance_root(_validated_instance(instance), data_root)
        / PLAYER_LOG_INGEST_LOCK_FILENAME
    )


@contextmanager
def acquire_player_log_ingest_lock(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
):
    """Acquire the shared process-scoped ingest lock without waiting."""
    lock_path = player_log_ingest_lock_path(instance, data_root=data_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise _PlayerLogIngestBusyError(
                "Player log ingest is already running."
            ) from error
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def plan_player_log_ingest(
    db_path: Path,
    log_paths: Iterable[os.PathLike[str] | str],
    *,
    scope: str,
) -> PlayerLogIngestPlan:
    normalized_scope = _safe_scope(scope)
    requested_paths = tuple(Path(path) for path in log_paths)
    checkpoints = {
        checkpoint.source_key: checkpoint
        for checkpoint in player_registry.list_player_log_ingest_checkpoints(
            db_path,
            scope=normalized_scope,
        )
    }
    skipped: Counter[str] = Counter()
    resets: Counter[str] = Counter()
    files_to_scan: list[Path] = []
    snapshots_to_scan: list[PlayerLogIngestFileSnapshot] = []

    for log_path in requested_paths:
        snapshot, skip_reason = snapshot_player_log_file(log_path)
        if snapshot is None:
            skipped[skip_reason or "unavailable"] += 1
            continue

        previous = checkpoints.get(snapshot.source_key)
        if previous is not None and _matches_checkpoint(snapshot, previous):
            skipped["unchanged"] += 1
            continue

        reset_reason = _checkpoint_reset_reason(snapshot, previous)
        if reset_reason:
            resets[reset_reason] += 1
        files_to_scan.append(snapshot.path)
        snapshots_to_scan.append(snapshot)

    return PlayerLogIngestPlan(
        scope=normalized_scope,
        files_considered=len(requested_paths),
        files_to_scan=tuple(files_to_scan),
        snapshots_to_scan=tuple(snapshots_to_scan),
        skipped_reason_counts=dict(sorted(skipped.items())),
        checkpoint_reset_reason_counts=dict(sorted(resets.items())),
    )


def snapshot_player_log_file(
    log_path: os.PathLike[str] | str,
) -> tuple[PlayerLogIngestFileSnapshot | None, str]:
    path = Path(log_path)
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return None, "missing_file"
    except OSError:
        return None, "stat_failed"

    if not stat_module.S_ISREG(stat_result.st_mode):
        return None, "not_file"

    try:
        fingerprint = _fingerprint_file(path, stat_result.st_size)
    except FileNotFoundError:
        return None, "missing_file"
    except OSError:
        return None, "read_failed"

    return (
        PlayerLogIngestFileSnapshot(
            path=path,
            source_key=_source_key(path),
            source_label=_source_label(path),
            size_bytes=max(0, int(stat_result.st_size)),
            mtime_ns=_mtime_ns(stat_result),
            fingerprint=fingerprint,
        ),
        "",
    )


def checkpoint_records_for_collection_summary(
    plan: PlayerLogIngestPlan,
    summary: PlayerLogCollectionSummary,
    *,
    updated_at: str,
) -> tuple[player_registry.PlayerLogIngestCheckpoint, ...]:
    records: list[player_registry.PlayerLogIngestCheckpoint] = []
    for snapshot, file_summary in zip(plan.snapshots_to_scan, summary.files):
        if file_summary.status != "scanned" or file_summary.errors:
            continue
        records.append(
            player_registry.PlayerLogIngestCheckpoint(
                scope=plan.scope,
                source_key=snapshot.source_key,
                source_label=snapshot.source_label,
                size_bytes=snapshot.size_bytes,
                mtime_ns=snapshot.mtime_ns,
                fingerprint=snapshot.fingerprint,
                status="scanned",
                last_scanned_at=updated_at,
                updated_at=updated_at,
            )
        )
    return tuple(records)


def _collector_skip_reason_counts(summary: PlayerLogCollectionSummary | None) -> dict[str, int]:
    if summary is None or not summary.errors:
        return {}
    return dict(sorted(Counter(error.code for error in summary.errors).items()))


def _collection_success_for_summary(summary: PlayerLogCollectionSummary) -> bool:
    if summary.error_count == 0:
        return True
    if summary.files_scanned < 1:
        return False
    return set(_collector_skip_reason_counts(summary)).issubset(
        _CONTROLLED_PARTIAL_SKIP_ERROR_CODES
    )


def _freshness_status(
    summary: PlayerLogCollectionSummary,
    plan: PlayerLogIngestPlan,
    *,
    collection_success: bool,
) -> str:
    if not collection_success:
        return player_registry.PLAYER_LOG_INGEST_STATUS_FAILED
    if plan.files_considered == 0:
        return player_registry.PLAYER_LOG_INGEST_STATUS_NO_LOGS
    skipped_counts = combine_reason_counts(
        plan.skipped_reason_counts,
        _collector_skip_reason_counts(summary),
    )
    if summary.error_count or any(
        reason != "unchanged" and count > 0 for reason, count in skipped_counts.items()
    ):
        return player_registry.PLAYER_LOG_INGEST_STATUS_PARTIAL
    return player_registry.PLAYER_LOG_INGEST_STATUS_FRESH


def _result_from_summary(
    instance: str,
    plan: PlayerLogIngestPlan,
    summary: PlayerLogCollectionSummary,
    *,
    checkpoint_updated: bool,
    freshness_status: str,
    freshness_at: str,
    collection_success: bool,
) -> PlayerLogIngestResult:
    return PlayerLogIngestResult(
        instance=instance,
        scope=plan.scope,
        files_considered=plan.files_considered,
        files_selected_for_scan=plan.files_selected_for_scan,
        files_requested=summary.files_requested,
        files_scanned=summary.files_scanned,
        files_skipped=summary.files_skipped + plan.skipped_files,
        scanned_lines=summary.lines_scanned,
        parsed_events=summary.matched_events,
        stored_events=summary.stored_events,
        duplicate_events=summary.duplicate_events,
        unmatched_lines=summary.unmatched_lines,
        skipped_lines=summary.skipped_lines,
        error_count=summary.error_count,
        skipped_reason_counts=combine_reason_counts(
            plan.skipped_reason_counts,
            _collector_skip_reason_counts(summary),
        ),
        checkpoint_reset_reason_counts=plan.checkpoint_reset_reason_counts,
        checkpoint_updated=checkpoint_updated,
        freshness_status=freshness_status,
        freshness_at=freshness_at,
        collection_success=collection_success,
    )


def _failed_result(
    instance: str,
    *,
    plan: PlayerLogIngestPlan | None,
    summary: PlayerLogCollectionSummary | None,
    run_at: str,
    failure_code: str,
) -> PlayerLogIngestResult:
    if plan is None:
        return PlayerLogIngestResult(
            instance=instance,
            outcome="failed",
            failure_code=_safe_reason(failure_code),
            freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
            freshness_at=run_at,
        )
    if summary is None:
        return PlayerLogIngestResult(
            instance=instance,
            scope=plan.scope,
            outcome="failed",
            failure_code=_safe_reason(failure_code),
            files_considered=plan.files_considered,
            files_selected_for_scan=plan.files_selected_for_scan,
            files_skipped=plan.skipped_files,
            skipped_reason_counts=plan.skipped_reason_counts,
            checkpoint_reset_reason_counts=plan.checkpoint_reset_reason_counts,
            freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
            freshness_at=run_at,
        )
    result = _result_from_summary(
        instance,
        plan,
        summary,
        checkpoint_updated=False,
        freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
        freshness_at=run_at,
        collection_success=False,
    )
    return replace(
        result,
        outcome="failed",
        failure_code=_safe_reason(failure_code),
    )


def _record_failed_freshness(
    registry_db_path: Path,
    *,
    plan: PlayerLogIngestPlan | None,
    summary: PlayerLogCollectionSummary | None,
    run_at: str,
) -> None:
    skipped_reason_counts = combine_reason_counts(
        plan.skipped_reason_counts if plan is not None else {},
        _collector_skip_reason_counts(summary),
    )
    try:
        player_registry.record_player_log_ingest_freshness(
            registry_db_path,
            scope=plan.scope if plan is not None else PLAYER_LOG_INGEST_SCOPE,
            status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
            last_run_at=run_at,
            scanned_files=summary.files_scanned if summary is not None else 0,
            parsed_events=summary.matched_events if summary is not None else 0,
            stored_events=summary.stored_events if summary is not None else 0,
            skipped_files=(summary.files_skipped if summary is not None else 0)
            + (plan.skipped_files if plan is not None else 0),
            skipped_reasons=format_reason_counts(skipped_reason_counts),
            checkpoint_updated=False,
        )
    except Exception:
        return


def run_player_log_ingest_once(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    max_files: int = DEFAULT_MAX_LOG_FILES,
    max_bytes: int = player_log_collector.DEFAULT_MAX_FILE_BYTES,
    max_lines: int = player_log_collector.DEFAULT_MAX_FILE_LINES,
) -> PlayerLogIngestResult:
    """Synchronously run one complete allowlisted player-log ingest pass."""
    run_started_at = _utc_now()
    try:
        normalized_instance = _validated_instance(instance)
    except paths.InvalidInstanceNameError:
        return PlayerLogIngestResult(
            instance="invalid",
            outcome="failed",
            failure_code="invalid_instance",
            freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
            freshness_at=run_started_at,
        )
    registry_db_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    plan: PlayerLogIngestPlan | None = None
    summary: PlayerLogCollectionSummary | None = None

    try:
        with acquire_player_log_ingest_lock(normalized_instance, data_root=data_root):
            log_paths = resolve_allowlisted_player_log_paths(
                normalized_instance,
                data_root=data_root,
                max_files=max_files,
            )
            plan = plan_player_log_ingest(
                registry_db_path,
                log_paths,
                scope=PLAYER_LOG_INGEST_SCOPE,
            )
            try:
                summary = player_log_collector.collect_player_log_events(
                    plan.files_to_scan,
                    registry_db_path,
                    dry_run=False,
                    max_bytes=max_bytes,
                    max_lines=max_lines,
                )
            except Exception:
                _record_failed_freshness(
                    registry_db_path,
                    plan=plan,
                    summary=None,
                    run_at=run_started_at,
                )
                return _failed_result(
                    normalized_instance,
                    plan=plan,
                    summary=None,
                    run_at=run_started_at,
                    failure_code="collector_failed",
                )

            freshness_at = _utc_now()
            try:
                checkpoint_records = checkpoint_records_for_collection_summary(
                    plan,
                    summary,
                    updated_at=freshness_at,
                )
                checkpoint_updated = bool(
                    player_registry.upsert_player_log_ingest_checkpoints(
                        registry_db_path,
                        checkpoint_records,
                    )
                )
                collection_success = _collection_success_for_summary(summary)
                freshness_status = _freshness_status(
                    summary,
                    plan,
                    collection_success=collection_success,
                )
                skipped_reason_counts = combine_reason_counts(
                    plan.skipped_reason_counts,
                    _collector_skip_reason_counts(summary),
                )
                player_registry.record_player_log_ingest_freshness(
                    registry_db_path,
                    scope=plan.scope,
                    status=freshness_status,
                    last_run_at=freshness_at,
                    scanned_files=summary.files_scanned,
                    parsed_events=summary.matched_events,
                    stored_events=summary.stored_events,
                    skipped_files=summary.files_skipped + plan.skipped_files,
                    skipped_reasons=format_reason_counts(skipped_reason_counts),
                    checkpoint_updated=checkpoint_updated,
                )
            except Exception:
                _record_failed_freshness(
                    registry_db_path,
                    plan=plan,
                    summary=summary,
                    run_at=freshness_at,
                )
                return _failed_result(
                    normalized_instance,
                    plan=plan,
                    summary=summary,
                    run_at=freshness_at,
                    failure_code="storage_failed",
                )

            return _result_from_summary(
                normalized_instance,
                plan,
                summary,
                checkpoint_updated=checkpoint_updated,
                freshness_status=freshness_status,
                freshness_at=freshness_at,
                collection_success=collection_success,
            )
    except _PlayerLogIngestBusyError:
        return PlayerLogIngestResult(
            instance=normalized_instance,
            outcome="busy",
            failure_code="already_running",
        )
    except Exception:
        _record_failed_freshness(
            registry_db_path,
            plan=plan,
            summary=summary,
            run_at=run_started_at,
        )
        return _failed_result(
            normalized_instance,
            plan=plan,
            summary=summary,
            run_at=run_started_at,
            failure_code="ingest_failed",
        )


def read_player_log_ingest_status(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> PlayerLogIngestStatus:
    """Read existing players.db freshness/checkpoints without creating or migrating it."""
    try:
        normalized_instance = _validated_instance(instance)
    except paths.InvalidInstanceNameError:
        return PlayerLogIngestStatus(
            instance="invalid",
            state="invalid",
            reason="invalid_instance",
        )
    db_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    try:
        db_exists = db_path.is_file()
    except OSError:
        db_exists = False
    if not db_exists:
        return PlayerLogIngestStatus(
            instance=normalized_instance,
            state="empty",
            reason="players_db_missing",
        )

    freshness = player_registry.get_player_log_ingest_freshness(
        db_path,
        scope=PLAYER_LOG_INGEST_SCOPE,
    )
    checkpoints = player_registry.list_player_log_ingest_checkpoints(
        db_path,
        scope=PLAYER_LOG_INGEST_SCOPE,
    )
    if (
        freshness.status == player_registry.PLAYER_LOG_INGEST_STATUS_UNAVAILABLE
        and not checkpoints
    ):
        state = "empty"
        reason = "ingest_state_missing"
    else:
        state = "available"
        reason = "ok"
    return PlayerLogIngestStatus(
        instance=normalized_instance,
        state=state,
        reason=reason,
        freshness_status=freshness.status,
        last_run_at=freshness.last_run_at,
        last_success_at=freshness.last_success_at,
        updated_at=freshness.updated_at,
        scanned_files=freshness.scanned_files,
        parsed_events=freshness.parsed_events,
        stored_events=freshness.stored_events,
        skipped_files=freshness.skipped_files,
        skipped_reasons=freshness.skipped_reasons,
        checkpoint_updated=freshness.checkpoint_updated,
        checkpoint_count=len(checkpoints),
    )


def combine_reason_counts(*items: Mapping[str, int] | None) -> dict[str, int]:
    combined: Counter[str] = Counter()
    for item in items:
        if not item:
            continue
        for reason, count in item.items():
            safe_reason = _safe_reason(reason)
            try:
                safe_count = int(count)
            except (TypeError, ValueError):
                safe_count = 0
            if safe_reason and safe_count > 0:
                combined[safe_reason] += safe_count
    return dict(sorted(combined.items()))


def format_reason_counts(reason_counts: Mapping[str, int] | None) -> str:
    return ",".join(
        f"{_safe_reason(reason)}={int(count)}"
        for reason, count in sorted((reason_counts or {}).items())
        if _safe_reason(reason) and int(count) > 0
    )


def _matches_checkpoint(
    snapshot: PlayerLogIngestFileSnapshot,
    checkpoint: player_registry.PlayerLogIngestCheckpoint,
) -> bool:
    return (
        snapshot.size_bytes == checkpoint.size_bytes
        and snapshot.mtime_ns == checkpoint.mtime_ns
        and snapshot.fingerprint == checkpoint.fingerprint
    )


def _checkpoint_reset_reason(
    snapshot: PlayerLogIngestFileSnapshot,
    checkpoint: player_registry.PlayerLogIngestCheckpoint | None,
) -> str:
    if checkpoint is None:
        return ""
    if snapshot.size_bytes < checkpoint.size_bytes:
        return "truncated"
    if (
        snapshot.size_bytes <= checkpoint.size_bytes
        and snapshot.fingerprint != checkpoint.fingerprint
    ):
        return "rotated"
    return ""


def _fingerprint_file(path: Path, size_bytes: int) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        hasher.update(handle.read(PLAYER_LOG_INGEST_FINGERPRINT_BYTES))
        if size_bytes > PLAYER_LOG_INGEST_FINGERPRINT_BYTES:
            tail_start = max(size_bytes - PLAYER_LOG_INGEST_FINGERPRINT_BYTES, 0)
            handle.seek(tail_start)
            hasher.update(handle.read(PLAYER_LOG_INGEST_FINGERPRINT_BYTES))
    hasher.update(str(max(0, int(size_bytes))).encode("ascii"))
    return hasher.hexdigest()


def _source_key(path: Path) -> str:
    try:
        resolved = str(path.resolve(strict=False))
    except OSError:
        resolved = str(path.absolute())
    digest = hashlib.sha256(resolved.encode("utf-8", "replace")).hexdigest()
    return f"sha256:{digest}"


def _source_label(path: Path) -> str:
    text = safe_player_text(path.name or "log", max_length=120)
    text = text.replace("/", "_").replace("\\", "_")
    text = _IPV4_ADDRESS_RE.sub("***", text)
    text = _BRACKETED_IPV6_ADDRESS_RE.sub("***", text).strip()
    return text or "log"


def _safe_scope(value: object) -> str:
    text = safe_player_text(value, max_length=120)
    text = text.replace("/", "_").replace("\\", "_").strip()
    return text or "player_logs"


def _safe_reason(value: object) -> str:
    text = safe_player_text(value, max_length=80).strip().lower()
    return re.sub(r"[^a-z0-9_-]+", "_", text).strip("_")


def _mtime_ns(stat_result: os.stat_result) -> int:
    value = getattr(stat_result, "st_mtime_ns", None)
    if value is not None:
        return max(0, int(value))
    return max(0, int(float(stat_result.st_mtime) * 1_000_000_000))
