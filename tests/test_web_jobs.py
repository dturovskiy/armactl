"""Tests for the web background job metadata foundation."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Lock

import pytest

from armactl.web.jobs import (
    JOB_STATUS_ABANDONED,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    SERVER_INSTALL_JOB_KIND,
    SERVER_REPAIR_JOB_KIND,
    SERVER_UPDATE_CHECK_JOB_KIND,
    SERVER_UPDATE_JOB_KIND,
    JobDispatcher,
    JobHandlerResult,
    JobStoreError,
    JobTransitionError,
    append_job_output,
    cancel_job,
    create_job,
    create_server_job_dispatcher,
    dispatch_job,
    dispatch_server_job,
    enqueue_job,
    enqueue_server_install,
    enqueue_server_repair,
    enqueue_server_update,
    enqueue_server_update_check,
    get_job,
    has_active_worker_token,
    list_active_jobs,
    list_recent_jobs,
    mark_job_failed,
    mark_job_running,
    mark_job_succeeded,
    mark_stale_running_job_abandoned,
    refresh_job_heartbeat,
)
from armactl.web.jobs.store import MAX_JOB_OUTPUT_CHARS, TRUNCATED_JOB_OUTPUT_PREFIX
from armactl.web.runtime import ensure_web_db
from armactl.web.runtime.job_store_maintenance import (
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_AT_META_KEY,
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY,
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_MESSAGE,
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_OUTPUT_NOTE,
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_STEP,
)
from armactl.web.services.job_integrity import (
    find_duplicate_active_jobs,
    job_store_integrity_diagnostics,
)

FORBIDDEN_IMPORT_PREFIXES = ("armactl.tui", "textual")


def _sqlite_tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    return {row[0] for row in rows}


def _sqlite_indexes(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index'
            """
        ).fetchall()
    return {row[0] for row in rows}


def _sqlite_columns(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _schema_meta(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT key, value
            FROM web_schema_meta
            """
        ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "web" / "web.db"


def _insert_raw_job(
    db_path: Path,
    *,
    kind: str,
    status: str,
    created_at: str,
    instance: str = "default",
    requested_by_username: str = "owner",
) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO web_jobs (
                kind,
                status,
                requested_by_username,
                instance,
                current_step,
                stdout_tail,
                stderr_tail,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 'legacy active row', 'legacy stdout', 'legacy stderr', ?, ?)
            """,
            (kind, status, requested_by_username, instance, created_at, created_at),
        )
        job_id = cursor.lastrowid
    assert job_id is not None
    return int(job_id)


def _active_job_ids(
    db_path: Path,
    *,
    kind: str,
    instance: str = "default",
) -> list[int]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM web_jobs
            WHERE kind = ?
              AND instance = ?
              AND status IN (?, ?)
            ORDER BY id
            """,
            (kind, instance, JOB_STATUS_QUEUED, JOB_STATUS_RUNNING),
        ).fetchall()
    return [row[0] for row in rows]


def _assert_timestamp(value: str) -> None:
    assert datetime.fromisoformat(value)


def test_ensure_web_db_creates_jobs_table(tmp_path: Path):
    db_path = _db_path(tmp_path)

    ensure_web_db(db_path)

    assert "web_jobs" in _sqlite_tables(db_path)
    assert "idx_web_jobs_active_lookup" in _sqlite_indexes(db_path)
    assert "idx_web_jobs_running_lease" in _sqlite_indexes(db_path)
    assert {
        "worker_id",
        "worker_started_at",
        "worker_heartbeat_at",
        "worker_lease_expires_at",
    }.issubset(_sqlite_columns(db_path, "web_jobs"))
    ensure_web_db(db_path)
    assert _schema_meta(db_path)["schema_version"] == "15"


def test_enqueue_repairs_duplicate_queued_jobs_idempotently(tmp_path: Path):
    db_path = _db_path(tmp_path)
    ensure_web_db(db_path)
    kept = _insert_raw_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status=JOB_STATUS_QUEUED,
        created_at="2026-01-01T00:00:00+00:00",
    )
    duplicate_running = _insert_raw_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status=JOB_STATUS_RUNNING,
        created_at="2026-01-01T00:00:01+00:00",
    )
    duplicate_queued = _insert_raw_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status=JOB_STATUS_QUEUED,
        created_at="2026-01-01T00:00:02+00:00",
    )
    other_kind = _insert_raw_job(
        db_path,
        kind=SERVER_REPAIR_JOB_KIND,
        status=JOB_STATUS_RUNNING,
        created_at="2026-01-01T00:00:03+00:00",
    )

    ensure_web_db(db_path)
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [
        kept,
        duplicate_running,
        duplicate_queued,
    ]
    assert enqueue_server_install(db_path, requested_by_username="owner").id == kept

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id,
                   status,
                   current_step,
                   result_message,
                   stdout_tail,
                   stderr_tail,
                   finished_at,
                   updated_at
            FROM web_jobs
            ORDER BY id
            """
        ).fetchall()
    row_by_id = {row[0]: row for row in rows}
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [
        kept,
        duplicate_running,
    ]
    assert _active_job_ids(db_path, kind=SERVER_REPAIR_JOB_KIND) == [other_kind]

    running_row = row_by_id[duplicate_running]
    assert running_row[1] == JOB_STATUS_RUNNING
    assert running_row[2] == "legacy active row"
    assert running_row[4] == "legacy stdout"
    assert running_row[5] == "legacy stderr"
    assert running_row[6] is None

    queued_row = row_by_id[duplicate_queued]
    assert queued_row[1] == JOB_STATUS_CANCELLED
    assert queued_row[2] == JOB_STORE_DUPLICATE_ACTIVE_REPAIR_STEP
    assert queued_row[3] == JOB_STORE_DUPLICATE_ACTIVE_REPAIR_MESSAGE
    assert queued_row[4] == ""
    assert queued_row[5] == JOB_STORE_DUPLICATE_ACTIVE_REPAIR_OUTPUT_NOTE
    assert queued_row[6] is not None
    _assert_timestamp(queued_row[6])

    meta = _schema_meta(db_path)
    assert meta[JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY] == "1"
    _assert_timestamp(meta[JOB_STORE_DUPLICATE_ACTIVE_REPAIR_AT_META_KEY])
    first_snapshot = (rows, meta)

    assert enqueue_server_install(db_path, requested_by_username="owner").id == kept

    with sqlite3.connect(db_path) as connection:
        rows_after_second_run = connection.execute(
            """
            SELECT id,
                   status,
                   current_step,
                   result_message,
                   stdout_tail,
                   stderr_tail,
                   finished_at,
                   updated_at
            FROM web_jobs
            ORDER BY id
            """
        ).fetchall()
    assert (rows_after_second_run, _schema_meta(db_path)) == first_snapshot


def test_job_integrity_detects_duplicate_active_jobs_without_repairing(tmp_path: Path):
    db_path = _db_path(tmp_path)
    ensure_web_db(db_path)
    first = _insert_raw_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status=JOB_STATUS_RUNNING,
        created_at="2026-01-01T00:00:00+00:00",
    )
    second = _insert_raw_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status=JOB_STATUS_QUEUED,
        created_at="2026-01-01T00:00:01+00:00",
    )
    _insert_raw_job(
        db_path,
        kind=SERVER_REPAIR_JOB_KIND,
        status=JOB_STATUS_QUEUED,
        created_at="2026-01-01T00:00:02+00:00",
    )

    groups = find_duplicate_active_jobs(db_path)

    assert len(groups) == 1
    assert groups[0].kind == SERVER_INSTALL_JOB_KIND
    assert groups[0].instance == "default"
    assert groups[0].active_job_ids == (first, second)
    assert groups[0].duplicate_job_ids == (second,)
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [first, second]

    diagnostics = job_store_integrity_diagnostics(db_path)

    assert len(diagnostics) == 1
    assert diagnostics[0].report_type == "active_duplicate"
    assert diagnostics[0].severity == "warning"
    assert diagnostics[0].job_kind == SERVER_INSTALL_JOB_KIND
    assert diagnostics[0].instance == "default"
    assert diagnostics[0].active_job_ids == (first, second)
    assert diagnostics[0].kept_job_id == first
    assert diagnostics[0].duplicate_job_ids == (second,)
    assert not hasattr(diagnostics[0], "details")


def test_create_get_and_list_recent_jobs(tmp_path: Path):
    db_path = _db_path(tmp_path)

    first = create_job(
        db_path,
        kind="install:update",
        requested_by_username="Owner",
        current_step="Queued",
        progress_total=3,
    )
    second = create_job(db_path, kind="repair", requested_by_username="owner")

    fetched = get_job(db_path, first.id)
    recent = list_recent_jobs(db_path)
    queued = list_recent_jobs(db_path, status=JOB_STATUS_QUEUED)
    active = list_active_jobs(db_path)

    assert fetched == first
    assert first.kind == "install:update"
    assert first.status == JOB_STATUS_QUEUED
    assert first.requested_by_username == "Owner"
    assert first.instance == "default"
    assert first.current_step == "Queued"
    assert first.progress_current == 0
    assert first.progress_total == 3
    _assert_timestamp(first.created_at)
    _assert_timestamp(first.updated_at)
    assert get_job(db_path, 9999) is None
    assert [job.id for job in recent] == [second.id, first.id]
    assert [job.id for job in queued] == [second.id, first.id]
    assert [job.id for job in active] == [second.id, first.id]


def test_valid_job_status_transitions_set_timestamps(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="repair", requested_by_username="owner")

    running = mark_job_running(
        db_path,
        job.id,
        current_step="Checking files",
        progress_current=1,
        progress_total=4,
    )
    succeeded = mark_job_succeeded(
        db_path,
        job.id,
        result_message="Repair completed",
        current_step="Done",
        progress_current=4,
        progress_total=4,
    )

    assert running.status == JOB_STATUS_RUNNING
    assert running.started_at is not None
    _assert_timestamp(running.started_at)
    assert succeeded.status == JOB_STATUS_SUCCEEDED
    assert succeeded.is_terminal is True
    assert succeeded.finished_at is not None
    _assert_timestamp(succeeded.finished_at)
    assert succeeded.result_message == "Repair completed"
    assert succeeded.current_step == "Done"
    assert succeeded.progress_current == 4
    assert succeeded.progress_total == 4


def test_dispatch_job_writes_worker_lease_and_clears_active_lease_on_terminal(
    tmp_path: Path,
):
    db_path = _db_path(tmp_path)
    seen_worker_ids: list[str] = []

    def handler(context):
        seen_worker_ids.append(context.worker_id)
        running = get_job(db_path, context.job.id)
        assert running is not None
        assert running.status == JOB_STATUS_RUNNING
        assert running.worker_id == context.worker_id
        assert running.worker_started_at is not None
        assert running.worker_heartbeat_at is not None
        assert running.worker_lease_expires_at is not None
        assert has_active_worker_token(context.job.id, context.worker_id)
        context.append_output(stdout="lease heartbeat progress")
        return JobHandlerResult(result_message="done", current_step="Done")

    job = enqueue_job(db_path, kind="safe:lease", requested_by_username="owner")

    result = dispatch_job(db_path, job.id, JobDispatcher({"safe:lease": handler}))

    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert seen_worker_ids
    assert result.job.worker_id == seen_worker_ids[0]
    assert result.job.worker_started_at is not None
    assert result.job.worker_heartbeat_at is not None
    assert result.job.worker_lease_expires_at is None
    assert not has_active_worker_token(job.id, seen_worker_ids[0])
    assert "lease heartbeat progress" in result.job.stdout_tail


def test_worker_progress_refreshes_heartbeat_and_terminal_blocks_refresh(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import store as job_store

    db_path = _db_path(tmp_path)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    timestamps = iter(
        [
            base,
            base + timedelta(seconds=10),
            base + timedelta(seconds=40),
            base + timedelta(seconds=50),
            base + timedelta(seconds=60),
        ]
    )
    monkeypatch.setattr(job_store, "_utc_now_dt", lambda: next(timestamps))

    job = create_job(db_path, kind="safe:heartbeat", requested_by_username="owner")
    running = mark_job_running(
        db_path,
        job.id,
        worker_id="worker1234",
        worker_lease_seconds=60,
    )
    refreshed = append_job_output(
        db_path,
        job.id,
        stdout="progress",
        worker_id="worker1234",
        worker_lease_seconds=60,
    )

    assert running.worker_heartbeat_at == (base + timedelta(seconds=10)).isoformat()
    assert refreshed.worker_heartbeat_at == (base + timedelta(seconds=40)).isoformat()
    assert refreshed.worker_lease_expires_at == (base + timedelta(seconds=100)).isoformat()

    succeeded = mark_job_succeeded(db_path, job.id, result_message="done")

    assert succeeded.worker_lease_expires_at is None
    assert refresh_job_heartbeat(db_path, job.id, worker_id="worker1234") is None


def test_expired_running_worker_lease_is_diagnostic_only(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="safe:expired", requested_by_username="owner")
    running = mark_job_running(
        db_path,
        job.id,
        worker_id="worker1234",
        worker_lease_seconds=60,
    )
    expired_at = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (expired_at, expired_at, running.id),
        )

    diagnostics = job_store_integrity_diagnostics(db_path)
    refreshed = get_job(db_path, running.id)

    assert refreshed is not None
    assert refreshed.status == JOB_STATUS_RUNNING
    assert refreshed.worker_lease_state == "expired"
    assert [diagnostic.report_type for diagnostic in diagnostics] == [
        "running_lease_expired"
    ]
    assert diagnostics[0].job_id == running.id
    assert diagnostics[0].recovery_action == "diagnostics_only"
    assert diagnostics[0].active_worker_known is False


def test_expired_running_worker_lease_can_be_marked_abandoned(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="safe:expired", requested_by_username="owner")
    running = mark_job_running(
        db_path,
        job.id,
        worker_id="worker1234",
        worker_lease_seconds=60,
    )
    expired_at = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (expired_at, expired_at, running.id),
        )

    abandoned = mark_stale_running_job_abandoned(db_path, running.id)

    assert abandoned is not None
    assert abandoned.status == JOB_STATUS_ABANDONED
    assert abandoned.is_terminal is True
    assert abandoned.finished_at is not None
    assert abandoned.current_step == "Stale running job marked abandoned"
    assert "no worker process was killed" in abandoned.result_message
    assert abandoned.worker_lease_state == "inactive"
    assert [job.id for job in list_active_jobs(db_path)] == []


def test_unknown_running_worker_lease_can_be_marked_abandoned(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="safe:unknown", requested_by_username="owner")
    running = mark_job_running(db_path, job.id)

    abandoned = mark_stale_running_job_abandoned(db_path, running.id)

    assert abandoned is not None
    assert abandoned.status == JOB_STATUS_ABANDONED
    assert abandoned.finished_at is not None


def test_fresh_or_live_running_worker_lease_cannot_be_marked_abandoned(
    tmp_path: Path,
):
    db_path = _db_path(tmp_path)
    fresh_job = create_job(db_path, kind="safe:fresh", requested_by_username="owner")
    fresh = mark_job_running(
        db_path,
        fresh_job.id,
        worker_id="worker1234",
        worker_lease_seconds=60,
    )

    assert mark_stale_running_job_abandoned(db_path, fresh.id) is None
    assert get_job(db_path, fresh.id).status == JOB_STATUS_RUNNING

    live_job = create_job(db_path, kind="safe:live", requested_by_username="owner")
    live = mark_job_running(
        db_path,
        live_job.id,
        worker_id="worker5678",
        worker_lease_seconds=60,
    )
    expired_at = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (expired_at, expired_at, live.id),
        )

    assert (
        mark_stale_running_job_abandoned(
            db_path,
            live.id,
            active_worker_token_checker=lambda job_id, worker_id: True,
        )
        is None
    )
    assert get_job(db_path, live.id).status == JOB_STATUS_RUNNING


def test_active_dedupe_ignores_abandoned_stale_running_row(tmp_path: Path):
    db_path = _db_path(tmp_path)
    first = create_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        requested_by_username="owner",
    )
    running = mark_job_running(db_path, first.id)

    abandoned = mark_stale_running_job_abandoned(db_path, running.id)
    created = enqueue_server_install(db_path, requested_by_username="owner")

    assert abandoned is not None
    assert abandoned.status == JOB_STATUS_ABANDONED
    assert created.id != abandoned.id
    assert created.status == JOB_STATUS_QUEUED
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [created.id]


def test_fresh_running_worker_lease_is_not_repaired_or_reported(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="safe:fresh", requested_by_username="owner")
    running = mark_job_running(
        db_path,
        job.id,
        worker_id="worker1234",
        worker_lease_seconds=60,
    )
    future_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (future_at, running.id),
        )

    assert job_store_integrity_diagnostics(db_path) == ()
    assert get_job(db_path, running.id).status == JOB_STATUS_RUNNING


def test_list_active_jobs_excludes_terminal_jobs(tmp_path: Path):
    db_path = _db_path(tmp_path)
    completed = create_job(db_path, kind="safe:done", requested_by_username="owner")
    mark_job_running(db_path, completed.id)
    mark_job_succeeded(db_path, completed.id)
    queued = create_job(db_path, kind="safe:queued", requested_by_username="owner")

    assert [job.id for job in list_active_jobs(db_path)] == [queued.id]

def test_failed_and_cancelled_jobs_are_controlled_terminal_states(tmp_path: Path):
    db_path = _db_path(tmp_path)
    failing = create_job(db_path, kind="install", requested_by_username="owner")
    cancelled = create_job(db_path, kind="repair", requested_by_username="owner")

    mark_job_running(db_path, failing.id)
    failed = mark_job_failed(
        db_path,
        failing.id,
        error_message="password=hunter2 failed",
        error_class="RuntimeError",
        result_message="Install failed",
    )
    cancelled_job = cancel_job(db_path, cancelled.id, result_message="Operator cancelled")

    assert failed.status == JOB_STATUS_FAILED
    assert failed.error_class == "RuntimeError"
    assert "hunter2" not in failed.error_message
    assert "password=***" in failed.error_message
    assert failed.finished_at is not None
    assert cancelled_job.status == JOB_STATUS_CANCELLED
    assert cancelled_job.result_message == "Operator cancelled"
    assert cancelled_job.finished_at is not None

    running_cancel = create_job(db_path, kind="repair", requested_by_username="owner")
    mark_job_running(db_path, running_cancel.id)
    with pytest.raises(JobTransitionError, match="Invalid web job status transition"):
        cancel_job(db_path, running_cancel.id, result_message="too late")


def test_invalid_job_inputs_and_transitions_fail_safely(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="repair", requested_by_username="owner")

    with pytest.raises(JobStoreError, match="Job kind is invalid"):
        create_job(db_path, kind="../repair", requested_by_username="owner")
    with pytest.raises(JobStoreError, match="requested_by_username is required"):
        create_job(db_path, kind="repair", requested_by_username=" ")
    with pytest.raises(JobStoreError, match="progress_current"):
        create_job(
            db_path,
            kind="repair",
            requested_by_username="owner",
            progress_current=-1,
        )
    with pytest.raises(JobStoreError, match="Job status is invalid"):
        list_recent_jobs(db_path, status="unknown")
    with pytest.raises(JobTransitionError, match="Invalid web job status transition"):
        mark_job_succeeded(db_path, job.id)

    running = mark_job_running(db_path, job.id)
    assert running.status == JOB_STATUS_RUNNING
    succeeded = mark_job_succeeded(db_path, job.id)
    with pytest.raises(JobTransitionError, match="Terminal web jobs cannot change"):
        mark_job_failed(db_path, succeeded.id, error_message="too late")


def test_job_output_tail_is_bounded_and_redacted(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="install", requested_by_username="owner")
    mark_job_running(db_path, job.id)

    output = "A" * (MAX_JOB_OUTPUT_CHARS + 200) + " tail-marker password=hunter2"
    error_output = "ARMACTL_WEB_SESSION_SECRET=secret-value\nsecret=another-value\x00"
    updated = append_job_output(db_path, job.id, stdout=output, stderr=error_output)

    assert len(updated.stdout_tail) <= MAX_JOB_OUTPUT_CHARS
    assert updated.stdout_tail.startswith(TRUNCATED_JOB_OUTPUT_PREFIX)
    assert updated.stdout_tail.endswith("tail-marker password=***")
    assert "hunter2" not in updated.stdout_tail
    assert "secret-value" not in updated.stderr_tail
    assert "another-value" not in updated.stderr_tail
    assert "ARMACTL_WEB_SESSION_SECRET=***" in updated.stderr_tail
    assert "secret=***" in updated.stderr_tail
    assert "\x00" not in updated.stderr_tail
    assert "hunter2" not in repr(updated)
    assert "secret-value" not in repr(updated)



def test_registered_handler_moves_job_to_succeeded(tmp_path: Path):
    db_path = _db_path(tmp_path)
    calls: list[str] = []

    def handler(context):
        calls.append(context.job.status)
        context.append_output(stdout="step complete")
        return JobHandlerResult(
            result_message="Job completed.",
            current_step="Done",
            progress_current=1,
            progress_total=1,
        )

    dispatcher = JobDispatcher({"safe:test": handler})
    job = enqueue_job(db_path, kind="safe:test", requested_by_username="owner")

    result = dispatch_job(db_path, job.id, dispatcher)

    assert result.ran is True
    assert result.message == "Job succeeded."
    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert result.job.result_message == "Job completed."
    assert result.job.current_step == "Done"
    assert result.job.progress_current == 1
    assert result.job.progress_total == 1
    assert result.job.started_at is not None
    assert result.job.finished_at is not None
    assert result.job.stdout_tail == "step complete"
    assert calls == [JOB_STATUS_RUNNING]


def test_handler_exception_marks_job_failed_with_redacted_error(tmp_path: Path):
    db_path = _db_path(tmp_path)

    def handler(context):
        context.append_output(stderr="password=hunter2 before failure")
        raise RuntimeError("token=secret-token exploded")

    dispatcher = JobDispatcher({"safe:fail": handler})
    job = enqueue_job(db_path, kind="safe:fail", requested_by_username="owner")

    result = dispatch_job(db_path, job.id, dispatcher)

    assert result.ran is True
    assert result.job.status == JOB_STATUS_FAILED
    assert result.job.error_class == "RuntimeError"
    assert "secret-token" not in result.job.error_message
    assert "token=***" in result.job.error_message
    assert "hunter2" not in result.job.stderr_tail
    assert "password=***" in result.job.stderr_tail


def test_unknown_job_kind_marks_failed_controlled(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = enqueue_job(db_path, kind="safe:missing", requested_by_username="owner")

    result = dispatch_job(db_path, job.id, JobDispatcher())

    assert result.ran is True
    assert result.job.status == JOB_STATUS_FAILED
    assert result.job.error_class == "UnknownJobKind"
    assert result.job.error_message == "No registered handler for job kind."


def test_terminal_jobs_do_not_rerun(tmp_path: Path):
    db_path = _db_path(tmp_path)
    job = create_job(db_path, kind="safe:test", requested_by_username="owner")
    mark_job_running(db_path, job.id)
    finished = mark_job_succeeded(db_path, job.id, result_message="already done")

    def handler(context):
        raise AssertionError("terminal job should not run")

    result = dispatch_job(db_path, finished.id, JobDispatcher({"safe:test": handler}))

    assert result.ran is False
    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert result.job.result_message == "already done"


def test_handler_output_is_bounded_and_redacted(tmp_path: Path):
    db_path = _db_path(tmp_path)

    def handler(context):
        context.append_output(
            stdout="A" * (MAX_JOB_OUTPUT_CHARS + 100) + " password=hunter2",
            stderr="ARMACTL_WEB_SESSION_SECRET=secret-value",
        )
        return "done"

    job = enqueue_job(db_path, kind="safe:output", requested_by_username="owner")
    result = dispatch_job(db_path, job.id, JobDispatcher({"safe:output": handler}))

    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert len(result.job.stdout_tail) <= MAX_JOB_OUTPUT_CHARS
    assert result.job.stdout_tail.startswith(TRUNCATED_JOB_OUTPUT_PREFIX)
    assert "hunter2" not in result.job.stdout_tail
    assert result.job.stdout_tail.endswith("password=***")
    assert "secret-value" not in result.job.stderr_tail
    assert "ARMACTL_WEB_SESSION_SECRET=***" in result.job.stderr_tail


def test_concurrent_dispatch_attempts_claim_queued_job_once(tmp_path: Path, monkeypatch):
    from armactl.web.jobs import runner

    db_path = _db_path(tmp_path)
    calls: list[str] = []
    barrier = Barrier(2)
    read_lock = Lock()
    observed_initial_reads = 0
    original_get_job = runner.get_job

    def synced_get_job(db_path_arg, job_id: int):
        nonlocal observed_initial_reads
        job = original_get_job(db_path_arg, job_id)
        with read_lock:
            observed_initial_reads += 1
            should_wait = observed_initial_reads <= 2
        if should_wait:
            barrier.wait(timeout=5)
        return job

    def handler(context):
        calls.append(context.job.status)
        return "done"

    monkeypatch.setattr(runner, "get_job", synced_get_job)
    dispatcher = JobDispatcher({"safe:claim": handler})
    job = enqueue_job(db_path, kind="safe:claim", requested_by_username="owner")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(dispatch_job, db_path, job.id, dispatcher) for _index in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sum(result.ran for result in results) == 1
    assert calls == [JOB_STATUS_RUNNING]
    assert get_job(db_path, job.id).status == JOB_STATUS_SUCCEEDED


def test_web_jobs_import_does_not_import_tui_or_textual(
    assert_import_does_not_import_modules,
):
    assert_import_does_not_import_modules("armactl.web.jobs", FORBIDDEN_IMPORT_PREFIXES)


def test_server_job_dispatcher_registers_explicit_install_repair_update_handlers():
    dispatcher = create_server_job_dispatcher()

    assert dispatcher.registered_kinds == (
        SERVER_INSTALL_JOB_KIND,
        SERVER_REPAIR_JOB_KIND,
        SERVER_UPDATE_JOB_KIND,
        SERVER_UPDATE_CHECK_JOB_KIND,
    )


def test_server_install_handler_streams_generator_output(tmp_path: Path, monkeypatch):
    from armactl.web.jobs import server as server_jobs

    db_path = _db_path(tmp_path)
    calls: list[str] = []

    def fake_install(instance: str):
        calls.append(instance)
        yield "install step 1"
        yield "install step 2 password=hunter2"

    monkeypatch.setattr(server_jobs.installer, "run_install", fake_install)
    job = enqueue_server_install(db_path, requested_by_username="owner")

    result = dispatch_server_job(db_path, job.id)

    assert calls == ["default"]
    assert result.job.kind == SERVER_INSTALL_JOB_KIND
    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert result.job.result_message == "Server install completed."
    assert "install step 1" in result.job.stdout_tail
    assert "hunter2" not in result.job.stdout_tail
    assert "password=***" in result.job.stdout_tail


def test_server_enqueue_reuses_active_install_job(tmp_path: Path):
    db_path = _db_path(tmp_path)

    first = enqueue_server_install(db_path, requested_by_username="owner")
    second = enqueue_server_install(db_path, requested_by_username="owner")
    jobs = list_recent_jobs(db_path)

    assert second.id == first.id
    assert [job.kind for job in jobs] == [SERVER_INSTALL_JOB_KIND]


def test_server_enqueue_reuses_active_repair_job(tmp_path: Path):
    db_path = _db_path(tmp_path)

    first = enqueue_server_repair(db_path, requested_by_username="owner")
    running = mark_job_running(db_path, first.id)
    second = enqueue_server_repair(db_path, requested_by_username="owner")
    jobs = list_recent_jobs(db_path)

    assert second.id == running.id
    assert second.status == JOB_STATUS_RUNNING
    assert [job.kind for job in jobs] == [SERVER_REPAIR_JOB_KIND]


def test_server_enqueue_reuses_active_update_check_job(tmp_path: Path):
    db_path = _db_path(tmp_path)

    first = enqueue_server_update_check(db_path, requested_by_username="owner")
    running = mark_job_running(db_path, first.id)
    second = enqueue_server_update_check(db_path, requested_by_username="owner")
    jobs = list_recent_jobs(db_path)

    assert second.id == running.id
    assert second.status == JOB_STATUS_RUNNING
    assert [job.kind for job in jobs] == [SERVER_UPDATE_CHECK_JOB_KIND]


def test_server_enqueue_reuses_active_update_job(tmp_path: Path):
    db_path = _db_path(tmp_path)

    first = enqueue_server_update(db_path, requested_by_username="owner")
    running = mark_job_running(db_path, first.id)
    second = enqueue_server_update(db_path, requested_by_username="owner")
    jobs = list_recent_jobs(db_path)

    assert second.id == running.id
    assert second.status == JOB_STATUS_RUNNING
    assert [job.kind for job in jobs] == [SERVER_UPDATE_JOB_KIND]


def test_server_update_handler_streams_update_without_repair_flow(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    from armactl.web.jobs import server as server_jobs

    db_path = _db_path(tmp_path)
    install_dir = tmp_path / "default" / "server"
    install_dir.mkdir(parents=True)
    (install_dir / "ArmaReforgerServer").write_text("binary", encoding="utf-8")
    update_calls: list[tuple[str, str]] = []
    discover_calls: list[tuple[str, bool]] = []

    def fake_discover(instance: str, save: bool = False):
        discover_calls.append((instance, save))
        return SimpleNamespace(
            install_dir=str(install_dir),
            server_running=False,
        )

    def fake_update(update_install_dir, *, instance: str):
        update_calls.append((str(update_install_dir), instance))
        yield "update step"

    monkeypatch.setattr(server_jobs.discovery, "discover", fake_discover)
    monkeypatch.setattr(server_jobs.installer, "stream_server_update", fake_update)
    monkeypatch.setattr(
        server_jobs.repair,
        "run_repair",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("update must not run repair flow")
        ),
    )
    job = enqueue_server_update(db_path, requested_by_username="owner")

    result = dispatch_server_job(db_path, job.id)

    assert update_calls == [(str(install_dir), "default")]
    assert discover_calls == [("default", False), ("default", True)]
    assert result.job.kind == SERVER_UPDATE_JOB_KIND
    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert result.job.result_message == "Server update completed."
    assert "update step" in result.job.stdout_tail
    assert (install_dir / ".armactl-package-manifest.json").is_file()


def test_server_update_handler_refuses_running_server_before_streaming_update(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    from armactl.web.jobs import server as server_jobs

    db_path = _db_path(tmp_path)
    install_dir = tmp_path / "default" / "server"
    install_dir.mkdir(parents=True)
    discover_calls: list[tuple[str, bool]] = []

    def fake_discover(instance: str, save: bool = False):
        discover_calls.append((instance, save))
        return SimpleNamespace(
            install_dir=str(install_dir),
            server_running=True,
        )

    monkeypatch.setattr(server_jobs.discovery, "discover", fake_discover)
    monkeypatch.setattr(
        server_jobs.installer,
        "stream_server_update",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("running-server update must not call SteamCMD")
        ),
    )
    monkeypatch.setattr(
        server_jobs.integrity,
        "mark_install_started",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("running-server update must not mark install started")
        ),
    )
    job = enqueue_server_update(db_path, requested_by_username="owner")

    result = dispatch_server_job(db_path, job.id)

    assert discover_calls == [("default", False)]
    assert result.job.kind == SERVER_UPDATE_JOB_KIND
    assert result.job.status == JOB_STATUS_FAILED
    assert result.job.error_class == "RuntimeError"
    assert result.job.error_message == "Stop the game server before updating."
    assert "Stop the game server before updating." in result.job.stdout_tail


def test_server_update_check_handler_caches_success_without_update_backend(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    from armactl.web.jobs import server as server_jobs
    from armactl.web.services import server_versions

    db_path = _db_path(tmp_path)
    install_dir = tmp_path / "default" / "server"
    manifest_dir = install_dir / "steamapps"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "appmanifest_1874900.acf").write_text(
        '"AppState"\n{\n"buildid" "100"\n}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        server_jobs.discovery,
        "discover",
        lambda instance, save=False: SimpleNamespace(
            install_dir=str(install_dir),
            server_installed=True,
            server_running=False,
        ),
    )
    monkeypatch.setattr(
        server_jobs.installer,
        "stream_server_update",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("update check must not run server update")
        ),
    )
    monkeypatch.setattr(
        server_versions.installer,
        "fetch_steam_app_info",
        lambda app_id: '"1874900" { "depots" { "branches" { "public" { "buildid" "101" } } } }',
    )
    job = enqueue_server_update_check(db_path, requested_by_username="owner")

    result = dispatch_server_job(db_path, job.id)
    state = server_versions.load_server_version_state(
        state=SimpleNamespace(
            install_dir=str(install_dir),
            server_installed=True,
            server_running=False,
        ),
        db_path=db_path,
    )

    assert result.job.kind == SERVER_UPDATE_CHECK_JOB_KIND
    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert result.job.result_message == "Update check completed."
    assert "installed=100" in result.job.stdout_tail
    assert "latest=101" in result.job.stdout_tail
    assert state.check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE
    assert state.latest == "101"


def test_server_update_check_handler_failure_is_redacted_and_cached(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    from armactl.web.jobs import server as server_jobs
    from armactl.web.services import server_versions

    db_path = _db_path(tmp_path)
    install_dir = tmp_path / "default" / "server"
    manifest_dir = install_dir / "steamapps"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "appmanifest_1874900.acf").write_text(
        '"AppState"\n{\n"buildid" "100"\n}\n',
        encoding="utf-8",
    )
    state = SimpleNamespace(
        install_dir=str(install_dir),
        server_installed=True,
        server_running=False,
    )

    monkeypatch.setattr(server_jobs.discovery, "discover", lambda instance, save=False: state)
    monkeypatch.setattr(
        server_versions.installer,
        "fetch_steam_app_info",
        lambda app_id: (_ for _ in ()).throw(RuntimeError("token=raw-steam-secret")),
    )
    job = enqueue_server_update_check(db_path, requested_by_username="owner")

    result = dispatch_server_job(db_path, job.id)
    loaded = server_versions.load_server_version_state(state=state, db_path=db_path)

    assert result.job.status == JOB_STATUS_FAILED
    assert loaded.check_state == server_versions.SERVER_VERSION_CHECK_FAILED
    assert "raw-steam-secret" not in result.job.error_message
    assert "raw-steam-secret" not in loaded.failure_reason
    assert "token=***" in result.job.error_message
    assert "token=***" in loaded.failure_reason


def test_server_enqueue_finds_active_job_older_than_recent_limit(tmp_path: Path):
    db_path = _db_path(tmp_path)

    active = enqueue_server_install(db_path, requested_by_username="owner")
    for _index in range(101):
        _insert_raw_job(
            db_path,
            kind=SERVER_INSTALL_JOB_KIND,
            status=JOB_STATUS_SUCCEEDED,
            created_at=f"2030-01-01T00:{_index // 60:02d}:{_index % 60:02d}+00:00",
        )

    recent_ids = {job.id for job in list_recent_jobs(db_path, limit=100)}
    second = enqueue_server_install(db_path, requested_by_username="owner")

    assert active.id not in recent_ids
    assert second.id == active.id
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [active.id]


def test_server_enqueue_ignores_terminal_job_for_same_scope(tmp_path: Path):
    db_path = _db_path(tmp_path)

    terminal = create_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        requested_by_username="owner",
    )
    mark_job_running(db_path, terminal.id)
    mark_job_succeeded(db_path, terminal.id)

    new_job = enqueue_server_install(db_path, requested_by_username="owner")

    assert new_job.id != terminal.id
    assert new_job.status == JOB_STATUS_QUEUED
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [new_job.id]


def test_server_enqueue_repairs_old_duplicate_active_rows_without_creating_new_job(
    tmp_path: Path,
):
    db_path = _db_path(tmp_path)
    ensure_web_db(db_path)
    kept = _insert_raw_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status=JOB_STATUS_QUEUED,
        created_at="2026-01-01T00:00:00+00:00",
    )
    duplicate = _insert_raw_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status=JOB_STATUS_RUNNING,
        created_at="2026-01-01T00:00:01+00:00",
    )

    job = enqueue_server_install(db_path, requested_by_username="owner")

    assert job.id == kept
    assert job.status == JOB_STATUS_QUEUED
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [kept, duplicate]
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, status, result_message, stderr_tail
            FROM web_jobs
            WHERE kind = ?
            ORDER BY id
            """,
            (SERVER_INSTALL_JOB_KIND,),
        ).fetchall()
    assert [row[0] for row in rows] == [kept, duplicate]
    assert rows[1][1] == JOB_STATUS_RUNNING
    assert rows[1][2] == ""
    assert rows[1][3] == "legacy stderr"


def test_server_job_audit_failure_does_not_cancel_existing_active_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import server_job_actions
    from armactl.web.services.audit import AuditLogError

    db_path = _db_path(tmp_path)
    existing = enqueue_server_install(db_path, requested_by_username="owner")

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    def fail_worker(*args, **kwargs):
        raise AssertionError("worker should not start when audit fails")

    monkeypatch.setattr(server_job_actions, "append_audit_event", fail_audit)
    monkeypatch.setattr(
        server_job_actions.server_jobs,
        "start_server_job_worker",
        fail_worker,
    )

    with pytest.raises(server_job_actions.ServerJobAuditError):
        server_job_actions.enqueue_server_job_and_start(
            db_path,
            action="install",
            audit_log_path=tmp_path / "audit.log",
            username="owner",
            user_id=None,
        )

    refreshed = get_job(db_path, existing.id)
    assert refreshed is not None
    assert refreshed.status == JOB_STATUS_QUEUED
    assert refreshed.result_message == ""
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [existing.id]


@pytest.mark.parametrize(
    ("action", "enqueue", "job_kind"),
    [
        ("install", enqueue_server_install, SERVER_INSTALL_JOB_KIND),
        ("repair", enqueue_server_repair, SERVER_REPAIR_JOB_KIND),
        ("update", enqueue_server_update, SERVER_UPDATE_JOB_KIND),
    ],
)
def test_server_job_action_reuses_active_job_without_starting_worker(
    tmp_path: Path,
    monkeypatch,
    action,
    enqueue,
    job_kind: str,
):
    from armactl.web.services import server_job_actions

    db_path = _db_path(tmp_path)
    existing = enqueue(db_path, requested_by_username="owner")
    scheduled: list[int] = []
    monkeypatch.setattr(
        server_job_actions.server_jobs,
        "start_server_job_worker",
        lambda db_path_arg, job_id: scheduled.append(job_id),
    )

    job = server_job_actions.enqueue_server_job_and_start(
        db_path,
        action=action,
        audit_log_path=tmp_path / "audit.log",
        username="owner",
        user_id=None,
    )

    assert job.id == existing.id
    assert job.kind == job_kind
    assert scheduled == []


def test_concurrent_server_install_enqueue_attempts_create_one_active_job(
    tmp_path: Path,
):
    db_path = _db_path(tmp_path)
    ensure_web_db(db_path)
    barrier = Barrier(2)

    def enqueue() -> int:
        barrier.wait(timeout=5)
        return enqueue_server_install(db_path, requested_by_username="owner").id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(enqueue) for _index in range(2)]
        job_ids = [future.result(timeout=10) for future in futures]

    assert job_ids[0] == job_ids[1]
    assert _active_job_ids(db_path, kind=SERVER_INSTALL_JOB_KIND) == [job_ids[0]]


def test_server_repair_handler_uses_discovery_paths_and_streams_output(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    from armactl.web.jobs import server as server_jobs

    db_path = _db_path(tmp_path)
    install_dir = tmp_path / "default" / "server"
    config_path = tmp_path / "default" / "config" / "config.json"
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        server_jobs.discovery,
        "discover",
        lambda instance, save=False: SimpleNamespace(
            install_dir=str(install_dir),
            config_path=str(config_path),
        ),
    )

    def fake_repair(instance: str, repair_install_dir, repair_config_path):
        calls.append((instance, str(repair_install_dir), str(repair_config_path)))
        yield "repair step"

    monkeypatch.setattr(server_jobs.repair, "run_repair", fake_repair)
    job = enqueue_server_repair(db_path, requested_by_username="owner")

    result = dispatch_server_job(db_path, job.id)

    assert calls == [("default", str(install_dir), str(config_path))]
    assert result.job.kind == SERVER_REPAIR_JOB_KIND
    assert result.job.status == JOB_STATUS_SUCCEEDED
    assert result.job.result_message == "Server repair completed."
    assert "repair step" in result.job.stdout_tail


def test_server_job_handler_failure_marks_failed_with_redacted_error(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import server as server_jobs

    db_path = _db_path(tmp_path)

    def fake_install(instance: str):
        yield "starting install"
        raise RuntimeError("secret=raw-install-secret failed")

    monkeypatch.setattr(server_jobs.installer, "run_install", fake_install)
    job = enqueue_server_install(db_path, requested_by_username="owner")

    result = dispatch_server_job(db_path, job.id)

    assert result.job.status == JOB_STATUS_FAILED
    assert result.job.error_class == "RuntimeError"
    assert "raw-install-secret" not in result.job.error_message
    assert "secret=***" in result.job.error_message
    assert "starting install" in result.job.stdout_tail


def test_start_server_job_worker_dispatches_in_background_thread(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import server as server_jobs

    db_path = _db_path(tmp_path)
    job = enqueue_server_install(db_path, requested_by_username="owner")
    calls: list[tuple[object, int]] = []

    def fake_dispatch(db_path_arg, job_id: int):
        calls.append((db_path_arg, job_id))

    monkeypatch.setattr(server_jobs, "dispatch_server_job", fake_dispatch)

    thread = server_jobs.start_server_job_worker(db_path, job.id)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert calls == [(db_path, job.id)]


def test_server_job_module_import_does_not_import_tui_textual(
    assert_import_does_not_import_modules,
):
    assert_import_does_not_import_modules(
        "armactl.web.jobs.server",
        FORBIDDEN_IMPORT_PREFIXES,
    )
