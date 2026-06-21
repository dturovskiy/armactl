"""SQLite store for web background job metadata."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.jobs.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobRecord,
)
from armactl.web.runtime.db import ensure_web_db
from armactl.web.runtime.job_store_maintenance import (
    repair_duplicate_active_jobs as _repair_duplicate_active_jobs,
)

MAX_JOB_KIND_LENGTH = 80
MAX_JOB_ACTOR_LENGTH = 120
MAX_JOB_INSTANCE_LENGTH = 120
MAX_JOB_STEP_LENGTH = 200
MAX_JOB_MESSAGE_LENGTH = 1000
MAX_JOB_ERROR_CLASS_LENGTH = 200
MAX_JOB_OUTPUT_CHARS = 8000
DEFAULT_RECENT_JOB_LIMIT = 20
MAX_RECENT_JOB_LIMIT = 100

_KIND_RE = re.compile(r"^[a-z][a-z0-9:_-]{0,79}$")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b((?:ARMACTL_WEB_SESSION_SECRET|session_secret|secret|api_key)\s*[=:]\s*)([^\s,;]+)"
)


class JobStoreError(RuntimeError):
    """Raised when web job metadata cannot be safely stored or read."""


class JobNotFoundError(JobStoreError):
    """Raised when a requested web job does not exist."""


class JobTransitionError(JobStoreError):
    """Raised when a web job status transition is invalid."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    try:
        ensure_web_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
    except (OSError, sqlite3.Error) as exc:
        raise JobStoreError("Failed to open web jobs database.") from exc

    connection.row_factory = sqlite3.Row
    return connection


def _record_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        requested_by_user_id=row["requested_by_user_id"],
        requested_by_username=row["requested_by_username"],
        instance=row["instance"],
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        current_step=row["current_step"],
        result_message=row["result_message"],
        stdout_tail=row["stdout_tail"],
        stderr_tail=row["stderr_tail"],
        error_message=row["error_message"],
        error_class=row["error_class"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _sanitize_job_text(value: object) -> str:
    text = redact_sensitive_text(value)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1***", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        char if char in {"\n", "\t"} or ord(char) >= 32 else " " for char in text
    )
    return text.strip()


def _safe_job_text(value: object, *, max_length: int) -> str:
    text = _sanitize_job_text(value)
    if len(text) > max_length:
        return text[:max_length]
    return text


def _safe_tail(existing: str, chunk: object, *, max_length: int = MAX_JOB_OUTPUT_CHARS) -> str:
    safe_chunk = _sanitize_job_text(chunk)
    if len(safe_chunk) > max_length:
        safe_chunk = safe_chunk[-max_length:]
    if not safe_chunk:
        combined = existing
    elif existing:
        combined = f"{existing}\n{safe_chunk}"
    else:
        combined = safe_chunk
    if len(combined) > max_length:
        return combined[-max_length:]
    return combined


def _normalize_kind(kind: str) -> str:
    if not isinstance(kind, str):
        raise JobStoreError("Job kind is required.")
    normalized = kind.strip().lower()
    if not _KIND_RE.fullmatch(normalized):
        raise JobStoreError("Job kind is invalid.")
    return normalized


def _normalize_non_empty_text(
    value: object,
    field_name: str,
    *,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise JobStoreError(f"{field_name} is required.")
    normalized = _safe_job_text(value, max_length=max_length)
    if not normalized:
        raise JobStoreError(f"{field_name} is required.")
    return normalized


def _normalize_optional_text(value: object, *, max_length: int) -> str:
    return _safe_job_text(value, max_length=max_length)


def _normalize_non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise JobStoreError(f"{field_name} must be a non-negative integer.")
    return value


def _normalize_requested_by_user_id(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise JobStoreError("requested_by_user_id must be a positive integer.")
    return value


def _normalize_status(status: str) -> str:
    if not isinstance(status, str):
        raise JobStoreError("Job status is required.")
    normalized = status.strip().lower()
    if normalized not in JOB_STATUSES:
        raise JobStoreError("Job status is invalid.")
    return normalized


def _normalize_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise JobStoreError("Job list limit must be a positive integer.")
    return min(limit, MAX_RECENT_JOB_LIMIT)


def _normalize_job_id(job_id: object) -> int:
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise JobStoreError("job_id must be a positive integer.")
    return job_id


def _fetch_job(connection: sqlite3.Connection, job_id: int) -> JobRecord:
    row = connection.execute(
        """
        SELECT id,
               kind,
               status,
               requested_by_user_id,
               requested_by_username,
               instance,
               progress_current,
               progress_total,
               current_step,
               result_message,
               stdout_tail,
               stderr_tail,
               error_message,
               error_class,
               created_at,
               updated_at,
               started_at,
               finished_at
        FROM web_jobs
        WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise JobNotFoundError("Web job was not found.")
    return _record_from_row(row)


def _fetch_active_job(
    connection: sqlite3.Connection,
    *,
    kind: str,
    instance: str,
) -> JobRecord | None:
    row = connection.execute(
        """
        SELECT id,
               kind,
               status,
               requested_by_user_id,
               requested_by_username,
               instance,
               progress_current,
               progress_total,
               current_step,
               result_message,
               stdout_tail,
               stderr_tail,
               error_message,
               error_class,
               created_at,
               updated_at,
               started_at,
               finished_at
        FROM web_jobs
        WHERE kind = ?
          AND instance = ?
          AND status IN (?, ?)
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        (kind, instance, JOB_STATUS_QUEUED, JOB_STATUS_RUNNING),
    ).fetchone()
    if row is None:
        return None
    return _record_from_row(row)


def _ensure_transition(current_status: str, new_status: str) -> None:
    if current_status in TERMINAL_JOB_STATUSES:
        raise JobTransitionError("Terminal web jobs cannot change status.")
    if current_status == JOB_STATUS_QUEUED and new_status in {
        JOB_STATUS_RUNNING,
        JOB_STATUS_CANCELLED,
    }:
        return
    if current_status == JOB_STATUS_RUNNING and new_status in {
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLED,
    }:
        return
    raise JobTransitionError("Invalid web job status transition.")


def _normalize_create_job_inputs(
    *,
    kind: str,
    requested_by_username: str,
    requested_by_user_id: int | None,
    instance: str,
    current_step: str,
    progress_current: int,
    progress_total: int,
) -> tuple[
    str,
    str,
    int | None,
    str,
    str,
    int,
    int,
]:
    normalized_kind = _normalize_kind(kind)
    normalized_username = _normalize_non_empty_text(
        requested_by_username,
        "requested_by_username",
        max_length=MAX_JOB_ACTOR_LENGTH,
    )
    normalized_user_id = _normalize_requested_by_user_id(requested_by_user_id)
    normalized_instance = _normalize_non_empty_text(
        instance,
        "instance",
        max_length=MAX_JOB_INSTANCE_LENGTH,
    )
    normalized_step = _normalize_optional_text(current_step, max_length=MAX_JOB_STEP_LENGTH)
    normalized_progress_current = _normalize_non_negative_int(
        progress_current,
        "progress_current",
    )
    normalized_progress_total = _normalize_non_negative_int(progress_total, "progress_total")
    return (
        normalized_kind,
        normalized_username,
        normalized_user_id,
        normalized_instance,
        normalized_step,
        normalized_progress_current,
        normalized_progress_total,
    )


def create_job(
    db_path: Path,
    *,
    kind: str,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    current_step: str = "",
    progress_current: int = 0,
    progress_total: int = 0,
) -> JobRecord:
    """Create queued metadata for a future background job."""
    (
        normalized_kind,
        normalized_username,
        normalized_user_id,
        normalized_instance,
        normalized_step,
        normalized_progress_current,
        normalized_progress_total,
    ) = _normalize_create_job_inputs(
        kind=kind,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step=current_step,
        progress_current=progress_current,
        progress_total=progress_total,
    )
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO web_jobs (
                    kind,
                    status,
                    requested_by_user_id,
                    requested_by_username,
                    instance,
                    progress_current,
                    progress_total,
                    current_step,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_kind,
                    JOB_STATUS_QUEUED,
                    normalized_user_id,
                    normalized_username,
                    normalized_instance,
                    normalized_progress_current,
                    normalized_progress_total,
                    normalized_step,
                    now,
                    now,
                ),
            )
            job_id = cursor.lastrowid
            if job_id is None:
                raise JobStoreError("Failed to create web job.")
            return _fetch_job(connection, job_id)
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to create web job.") from exc


def get_or_create_active_job(
    db_path: Path,
    *,
    kind: str,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    current_step: str = "",
    progress_current: int = 0,
    progress_total: int = 0,
) -> tuple[JobRecord, bool]:
    """Return an active kind/instance job, or atomically create a queued one."""
    (
        normalized_kind,
        normalized_username,
        normalized_user_id,
        normalized_instance,
        normalized_step,
        normalized_progress_current,
        normalized_progress_total,
    ) = _normalize_create_job_inputs(
        kind=kind,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step=current_step,
        progress_current=progress_current,
        progress_total=progress_total,
    )
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _repair_duplicate_active_jobs(
                connection,
                kind=normalized_kind,
                instance=normalized_instance,
            )
            active = _fetch_active_job(
                connection,
                kind=normalized_kind,
                instance=normalized_instance,
            )
            if active is not None:
                return active, False

            cursor = connection.execute(
                """
                INSERT INTO web_jobs (
                    kind,
                    status,
                    requested_by_user_id,
                    requested_by_username,
                    instance,
                    progress_current,
                    progress_total,
                    current_step,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_kind,
                    JOB_STATUS_QUEUED,
                    normalized_user_id,
                    normalized_username,
                    normalized_instance,
                    normalized_progress_current,
                    normalized_progress_total,
                    normalized_step,
                    now,
                    now,
                ),
            )
            job_id = cursor.lastrowid
            if job_id is None:
                raise JobStoreError("Failed to create web job.")
            return _fetch_job(connection, job_id), True
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to create web job.") from exc

def get_active_job(
    db_path: Path,
    *,
    kind: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> JobRecord | None:
    """Return the oldest active kind/instance job, if one exists."""
    normalized_kind = _normalize_kind(kind)
    normalized_instance = _normalize_non_empty_text(
        instance,
        "instance",
        max_length=MAX_JOB_INSTANCE_LENGTH,
    )

    try:
        with _connect(db_path) as connection:
            return _fetch_active_job(
                connection,
                kind=normalized_kind,
                instance=normalized_instance,
            )
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to read active web job.") from exc



def get_job(db_path: Path, job_id: int) -> JobRecord | None:
    """Return one web job by id, if it exists."""
    normalized_job_id = _normalize_job_id(job_id)
    try:
        with _connect(db_path) as connection:
            try:
                return _fetch_job(connection, normalized_job_id)
            except JobNotFoundError:
                return None
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to read web job.") from exc


def list_recent_jobs(
    db_path: Path,
    *,
    limit: int = DEFAULT_RECENT_JOB_LIMIT,
    status: str | None = None,
) -> list[JobRecord]:
    """Return recent jobs, newest first."""
    normalized_limit = _normalize_limit(limit)
    normalized_status = _normalize_status(status) if status is not None else None
    where = ""
    params: list[object] = []
    if normalized_status is not None:
        where = "WHERE status = ?"
        params.append(normalized_status)
    params.append(normalized_limit)

    try:
        with _connect(db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT id,
                       kind,
                       status,
                       requested_by_user_id,
                       requested_by_username,
                       instance,
                       progress_current,
                       progress_total,
                       current_step,
                       result_message,
                       stdout_tail,
                       stderr_tail,
                       error_message,
                       error_class,
                       created_at,
                       updated_at,
                       started_at,
                       finished_at
                FROM web_jobs
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to list web jobs.") from exc

    return [_record_from_row(row) for row in rows]


def mark_job_running(
    db_path: Path,
    job_id: int,
    *,
    current_step: str = "",
    progress_current: int = 0,
    progress_total: int = 0,
) -> JobRecord:
    """Mark a queued job as running."""
    normalized_job_id = _normalize_job_id(job_id)
    normalized_step = _normalize_optional_text(current_step, max_length=MAX_JOB_STEP_LENGTH)
    normalized_progress_current = _normalize_non_negative_int(
        progress_current,
        "progress_current",
    )
    normalized_progress_total = _normalize_non_negative_int(progress_total, "progress_total")
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE web_jobs
                SET status = ?,
                    current_step = ?,
                    progress_current = ?,
                    progress_total = ?,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                """,
                (
                    JOB_STATUS_RUNNING,
                    normalized_step,
                    normalized_progress_current,
                    normalized_progress_total,
                    now,
                    now,
                    normalized_job_id,
                    JOB_STATUS_QUEUED,
                ),
            )
            if cursor.rowcount != 1:
                raise JobTransitionError("Job is no longer queued.")
            return _fetch_job(connection, normalized_job_id)
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to update web job.") from exc


def append_job_output(
    db_path: Path,
    job_id: int,
    *,
    stdout: str = "",
    stderr: str = "",
) -> JobRecord:
    """Append bounded, redacted stdout/stderr tail text to a job."""
    normalized_job_id = _normalize_job_id(job_id)
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            job = _fetch_job(connection, normalized_job_id)
            stdout_tail = _safe_tail(job.stdout_tail, stdout)
            stderr_tail = _safe_tail(job.stderr_tail, stderr)
            connection.execute(
                """
                UPDATE web_jobs
                SET stdout_tail = ?,
                    stderr_tail = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (stdout_tail, stderr_tail, now, normalized_job_id),
            )
            return _fetch_job(connection, normalized_job_id)
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to append web job output.") from exc


def mark_job_succeeded(
    db_path: Path,
    job_id: int,
    *,
    result_message: str = "",
    current_step: str = "",
    progress_current: int | None = None,
    progress_total: int | None = None,
) -> JobRecord:
    """Mark a running job as succeeded."""
    normalized_job_id = _normalize_job_id(job_id)
    normalized_message = _normalize_optional_text(result_message, max_length=MAX_JOB_MESSAGE_LENGTH)
    normalized_step = _normalize_optional_text(current_step, max_length=MAX_JOB_STEP_LENGTH)
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            job = _fetch_job(connection, normalized_job_id)
            _ensure_transition(job.status, JOB_STATUS_SUCCEEDED)
            normalized_progress_current = (
                job.progress_current
                if progress_current is None
                else _normalize_non_negative_int(progress_current, "progress_current")
            )
            normalized_progress_total = (
                job.progress_total
                if progress_total is None
                else _normalize_non_negative_int(progress_total, "progress_total")
            )
            connection.execute(
                """
                UPDATE web_jobs
                SET status = ?,
                    current_step = ?,
                    progress_current = ?,
                    progress_total = ?,
                    result_message = ?,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    JOB_STATUS_SUCCEEDED,
                    normalized_step,
                    normalized_progress_current,
                    normalized_progress_total,
                    normalized_message,
                    now,
                    now,
                    normalized_job_id,
                ),
            )
            return _fetch_job(connection, normalized_job_id)
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to finish web job.") from exc


def mark_job_failed(
    db_path: Path,
    job_id: int,
    *,
    error_message: str,
    error_class: str = "",
    result_message: str = "",
) -> JobRecord:
    """Mark a running job as failed with redacted error metadata."""
    normalized_job_id = _normalize_job_id(job_id)
    normalized_error = _normalize_non_empty_text(
        error_message,
        "error_message",
        max_length=MAX_JOB_MESSAGE_LENGTH,
    )
    normalized_error_class = _normalize_optional_text(
        error_class,
        max_length=MAX_JOB_ERROR_CLASS_LENGTH,
    )
    normalized_result = _normalize_optional_text(result_message, max_length=MAX_JOB_MESSAGE_LENGTH)
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            job = _fetch_job(connection, normalized_job_id)
            _ensure_transition(job.status, JOB_STATUS_FAILED)
            connection.execute(
                """
                UPDATE web_jobs
                SET status = ?,
                    result_message = ?,
                    error_message = ?,
                    error_class = ?,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    JOB_STATUS_FAILED,
                    normalized_result,
                    normalized_error,
                    normalized_error_class,
                    now,
                    now,
                    normalized_job_id,
                ),
            )
            return _fetch_job(connection, normalized_job_id)
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to fail web job.") from exc


def mark_job_cancelled(
    db_path: Path,
    job_id: int,
    *,
    result_message: str = "Cancelled.",
) -> JobRecord:
    """Mark a queued or running job as cancelled."""
    normalized_job_id = _normalize_job_id(job_id)
    normalized_message = _normalize_optional_text(result_message, max_length=MAX_JOB_MESSAGE_LENGTH)
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            job = _fetch_job(connection, normalized_job_id)
            _ensure_transition(job.status, JOB_STATUS_CANCELLED)
            connection.execute(
                """
                UPDATE web_jobs
                SET status = ?,
                    result_message = ?,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (JOB_STATUS_CANCELLED, normalized_message, now, now, normalized_job_id),
            )
            return _fetch_job(connection, normalized_job_id)
    except sqlite3.Error as exc:
        raise JobStoreError("Failed to cancel web job.") from exc


cancel_job = mark_job_cancelled
