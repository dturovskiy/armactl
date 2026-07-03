"""Maintenance helpers for web job-store integrity."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

ACTIVE_JOB_STATUSES = ("queued", "running")
REPAIRABLE_DUPLICATE_JOB_STATUSES = ("queued",)
JOB_STATUS_CANCELLED = "cancelled"
JOB_STORE_DUPLICATE_ACTIVE_REPAIR_STEP = "Duplicate queued job cancelled"
JOB_STORE_DUPLICATE_ACTIVE_REPAIR_MESSAGE = (
    "Cancelled by web job-store maintenance; older active job kept."
)
JOB_STORE_DUPLICATE_ACTIVE_REPAIR_OUTPUT_NOTE = (
    "Job cancelled by web job-store maintenance because another queued job "
    "already existed for this kind and instance."
)
JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY = "job_store_duplicate_active_repair_count"
JOB_STORE_DUPLICATE_ACTIVE_REPAIR_AT_META_KEY = "job_store_duplicate_active_repair_at"


@dataclass(frozen=True)
class ActiveJobReference:
    """Small immutable reference used by integrity diagnostics and repair."""

    id: int
    kind: str
    instance: str
    status: str
    created_at: str


@dataclass(frozen=True)
class DuplicateActiveJobGroup:
    """Active jobs sharing the same kind/instance integrity key."""

    kind: str
    instance: str
    jobs: tuple[ActiveJobReference, ...]

    @property
    def kept_job(self) -> ActiveJobReference:
        """Return the oldest active job that maintenance keeps."""
        return self.jobs[0]

    @property
    def duplicate_jobs(self) -> tuple[ActiveJobReference, ...]:
        """Return active jobs after the oldest kept row for diagnostics/repair."""
        return self.jobs[1:]

    @property
    def active_job_ids(self) -> tuple[int, ...]:
        """Return all active job ids in deterministic repair order."""
        return tuple(job.id for job in self.jobs)

    @property
    def duplicate_job_ids(self) -> tuple[int, ...]:
        """Return duplicate active job ids in deterministic repair order."""
        return tuple(job.id for job in self.duplicate_jobs)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_job_refs(
    connection: sqlite3.Connection,
    *,
    kind: str | None = None,
    instance: str | None = None,
) -> tuple[ActiveJobReference, ...]:
    where = ["status IN (?, ?)"]
    params: list[object] = list(ACTIVE_JOB_STATUSES)
    if kind is not None:
        where.append("kind = ?")
        params.append(kind)
    if instance is not None:
        where.append("instance = ?")
        params.append(instance)

    rows = connection.execute(
        f"""
        SELECT id, kind, instance, status, created_at
        FROM web_jobs
        WHERE {' AND '.join(where)}
        ORDER BY kind ASC, instance ASC, created_at ASC, id ASC
        """,
        params,
    ).fetchall()
    return tuple(
        ActiveJobReference(
            id=int(row[0]),
            kind=str(row[1]),
            instance=str(row[2]),
            status=str(row[3]),
            created_at=str(row[4]),
        )
        for row in rows
    )


def find_duplicate_active_job_groups(
    connection: sqlite3.Connection,
    *,
    kind: str | None = None,
    instance: str | None = None,
) -> tuple[DuplicateActiveJobGroup, ...]:
    """Return duplicate active job groups without mutating the database."""
    grouped: dict[tuple[str, str], list[ActiveJobReference]] = {}
    for ref in _active_job_refs(connection, kind=kind, instance=instance):
        grouped.setdefault((ref.kind, ref.instance), []).append(ref)

    return tuple(
        DuplicateActiveJobGroup(kind=group_kind, instance=group_instance, jobs=tuple(refs))
        for (group_kind, group_instance), refs in grouped.items()
        if len(refs) > 1
    )


def _record_repair_meta(
    connection: sqlite3.Connection,
    *,
    repaired_count: int,
    repaired_at: str,
) -> None:
    values = (
        (JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY, str(repaired_count)),
        (JOB_STORE_DUPLICATE_ACTIVE_REPAIR_AT_META_KEY, repaired_at),
    )
    for key, value in values:
        connection.execute(
            """
            INSERT INTO web_schema_meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def repair_duplicate_active_jobs(
    connection: sqlite3.Connection,
    *,
    kind: str | None = None,
    instance: str | None = None,
    repaired_at: str | None = None,
) -> int:
    """Cancel duplicate queued jobs, keeping running rows operator-visible."""
    timestamp = repaired_at or _utc_now()
    repaired_count = 0
    for group in find_duplicate_active_job_groups(connection, kind=kind, instance=instance):
        for duplicate in group.duplicate_jobs:
            if duplicate.status not in REPAIRABLE_DUPLICATE_JOB_STATUSES:
                continue
            cursor = connection.execute(
                """
                UPDATE web_jobs
                SET status = ?,
                    current_step = ?,
                    result_message = ?,
                    stdout_tail = '',
                    stderr_tail = ?,
                    error_message = '',
                    error_class = '',
                    updated_at = ?,
                    finished_at = COALESCE(finished_at, ?)
                WHERE id = ?
                  AND status = ?
                """,
                (
                    JOB_STATUS_CANCELLED,
                    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_STEP,
                    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_MESSAGE,
                    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_OUTPUT_NOTE,
                    timestamp,
                    timestamp,
                    duplicate.id,
                    *REPAIRABLE_DUPLICATE_JOB_STATUSES,
                ),
            )
            if cursor.rowcount and cursor.rowcount > 0:
                repaired_count += cursor.rowcount

    if repaired_count:
        _record_repair_meta(
            connection,
            repaired_count=repaired_count,
            repaired_at=timestamp,
        )
    return repaired_count
