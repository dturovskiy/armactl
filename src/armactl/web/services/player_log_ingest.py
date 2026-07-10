from __future__ import annotations

import hashlib
import os
import re
import stat as stat_module
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from armactl.web.services import player_registry
from armactl.web.services.player_identity import safe_player_text

PLAYER_LOG_INGEST_FINGERPRINT_BYTES = 4096
_IPV4_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_BRACKETED_IPV6_ADDRESS_RE = re.compile(r"\[[0-9A-Fa-f:.]{2,}\](?::\d{1,5})?")


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
    summary,
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
