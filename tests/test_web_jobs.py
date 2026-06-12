"""Tests for the web background job metadata foundation."""

from __future__ import annotations

import builtins
import importlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

from armactl.web.jobs import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JobDispatcher,
    JobHandlerResult,
    JobStoreError,
    JobTransitionError,
    append_job_output,
    cancel_job,
    create_job,
    dispatch_job,
    enqueue_job,
    get_job,
    list_recent_jobs,
    mark_job_failed,
    mark_job_running,
    mark_job_succeeded,
)
from armactl.web.jobs.store import MAX_JOB_OUTPUT_CHARS
from armactl.web.runtime import ensure_web_db

FORBIDDEN_IMPORT_PREFIXES = ("armactl.tui", "textual")


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if _matches_prefix(module_name, prefixes):
            sys.modules.pop(module_name)


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


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "web" / "web.db"


def _assert_timestamp(value: str) -> None:
    assert datetime.fromisoformat(value)


def test_ensure_web_db_creates_jobs_table(tmp_path: Path):
    db_path = _db_path(tmp_path)

    ensure_web_db(db_path)

    assert "web_jobs" in _sqlite_tables(db_path)


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
    assert "hunter2" not in result.job.stdout_tail
    assert result.job.stdout_tail.endswith("password=***")
    assert "secret-value" not in result.job.stderr_tail
    assert "ARMACTL_WEB_SESSION_SECRET=***" in result.job.stderr_tail

def test_web_jobs_import_does_not_import_tui_or_textual(monkeypatch):
    _forget_modules("armactl.web.jobs", "armactl.tui", "textual")
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, FORBIDDEN_IMPORT_PREFIXES):
            blocked_imports.append(name)
            raise AssertionError(f"web jobs imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.jobs")

    assert callable(module.create_job)
    assert blocked_imports == []
    assert not any(
        _matches_prefix(module_name, FORBIDDEN_IMPORT_PREFIXES) for module_name in sys.modules
    )
