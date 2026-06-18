"""Read-only job-store integrity diagnostics for the web panel."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

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
