"""Read-only job-store integrity diagnostics for the web panel."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl.web.jobs.runner import has_active_worker_token
from armactl.web.runtime.job_store_maintenance import (
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_AT_META_KEY,
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY,
    DuplicateActiveJobGroup,
    find_duplicate_active_job_groups,
)


@dataclass(frozen=True)
class JobStoreRepairReport:
    """Summary of the last duplicate-active repair recorded by DB maintenance."""

    repaired_count: int
    repaired_at: str


@dataclass(frozen=True)
class JobStoreIntegrityDiagnostic:
    """Structured operator-visible job-store integrity diagnostic."""

    report_type: str
    severity: str
    job_kind: str = ""
    instance: str = ""
    active_job_ids: tuple[int, ...] = ()
    kept_job_id: int | None = None
    duplicate_job_ids: tuple[int, ...] = ()
    repaired_count: int = 0
    repaired_at: str = ""
    job_id: int | None = None
    lease_state: str = ""
    worker_heartbeat_at: str = ""
    worker_lease_expires_at: str = ""
    active_worker_known: bool = False
    recovery_action: str = ""


def find_duplicate_active_jobs(db_path: Path) -> tuple[DuplicateActiveJobGroup, ...]:
    """Find current duplicate queued/running jobs without repairing them."""
    db_file = Path(db_path)
    if not db_file.exists():
        return ()
    with sqlite3.connect(db_file) as connection:
        return find_duplicate_active_job_groups(connection)


def get_duplicate_active_repair_report(db_path: Path) -> JobStoreRepairReport | None:
    """Return the last duplicate-active maintenance report, if one was recorded."""
    db_file = Path(db_path)
    if not db_file.exists():
        return None
    with sqlite3.connect(db_file) as connection:
        rows = connection.execute(
            """
            SELECT key, value
            FROM web_schema_meta
            WHERE key IN (?, ?)
            """,
            (
                JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY,
                JOB_STORE_DUPLICATE_ACTIVE_REPAIR_AT_META_KEY,
            ),
        ).fetchall()
    values = {str(row[0]): str(row[1]) for row in rows}
    count_text = values.get(JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY)
    repaired_at = values.get(JOB_STORE_DUPLICATE_ACTIVE_REPAIR_AT_META_KEY, "")
    try:
        repaired_count = int(count_text or "0")
    except ValueError:
        return None
    if repaired_count <= 0 or not repaired_at:
        return None
    return JobStoreRepairReport(repaired_count=repaired_count, repaired_at=repaired_at)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _running_lease_diagnostics(db_path: Path) -> tuple[JobStoreIntegrityDiagnostic, ...]:
    db_file = Path(db_path)
    if not db_file.exists():
        return ()
    try:
        with sqlite3.connect(db_file) as connection:
            rows = connection.execute(
                """
                SELECT id,
                       kind,
                       instance,
                       worker_id,
                       worker_heartbeat_at,
                       worker_lease_expires_at
                FROM web_jobs
                WHERE status = 'running'
                ORDER BY updated_at DESC, id DESC
                LIMIT 50
                """
            ).fetchall()
    except sqlite3.Error:
        return ()

    now = datetime.now(timezone.utc)
    diagnostics: list[JobStoreIntegrityDiagnostic] = []
    for row in rows:
        expires_at = _parse_timestamp(row[5])
        if expires_at is None or expires_at > now:
            continue
        worker_id = str(row[3] or "")
        diagnostics.append(
            JobStoreIntegrityDiagnostic(
                report_type="running_lease_expired",
                severity="warning",
                job_id=int(row[0]),
                job_kind=str(row[1]),
                instance=str(row[2]),
                lease_state="expired",
                worker_heartbeat_at=str(row[4] or ""),
                worker_lease_expires_at=str(row[5] or ""),
                active_worker_known=(
                    bool(worker_id) and has_active_worker_token(int(row[0]), worker_id)
                ),
                recovery_action="diagnostics_only",
            )
        )
    return tuple(diagnostics)


def job_store_integrity_diagnostics(db_path: Path) -> tuple[JobStoreIntegrityDiagnostic, ...]:
    """Return structured job-store integrity diagnostics for `/jobs`."""
    diagnostics: list[JobStoreIntegrityDiagnostic] = []
    for group in find_duplicate_active_jobs(db_path):
        diagnostics.append(
            JobStoreIntegrityDiagnostic(
                report_type="active_duplicate",
                severity="warning",
                job_kind=group.kind,
                instance=group.instance,
                active_job_ids=group.active_job_ids,
                kept_job_id=group.kept_job.id,
                duplicate_job_ids=group.duplicate_job_ids,
            )
        )

    diagnostics.extend(_running_lease_diagnostics(db_path))

    repair_report = get_duplicate_active_repair_report(db_path)
    if repair_report is not None:
        diagnostics.append(
            JobStoreIntegrityDiagnostic(
                report_type="maintenance_report",
                severity="info",
                repaired_count=repair_report.repaired_count,
                repaired_at=repair_report.repaired_at,
            )
        )
    return tuple(diagnostics)
