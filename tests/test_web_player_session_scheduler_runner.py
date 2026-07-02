"""Tests for the explicit player-session scheduler runner foundation."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from armactl.cli import main
from armactl.web.jobs import create_job, list_recent_jobs, mark_job_running
from armactl.web.runtime import ensure_web_db
from armactl.web.runtime.db import WEB_SCHEMA_VERSION
from armactl.web.services import player_session_scheduler_policy as policy
from armactl.web.services import player_session_scheduler_runner as runner

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
STATE_TABLE = "web_player_session_scheduler_state"


def _web_db(tmp_path: Path) -> Path:
    return tmp_path / "web" / "web.db"


def _table_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _job_count(db_path: Path) -> int:
    return _table_count(db_path, "web_jobs")


def _scheduler_db_text(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"SELECT * FROM {STATE_TABLE}").fetchall()
    return " ".join(str(value) for row in rows for value in row)


def _state_by_kind(db_path: Path) -> dict[str, runner.PlayerSessionSchedulerState]:
    return {
        state.job_kind: state
        for state in runner.list_player_session_scheduler_state(db_path)
    }


def _decision_by_kind(
    result: runner.PlayerSessionSchedulerRunResult,
) -> dict[str, runner.PlayerSessionSchedulerJobResult]:
    return {job.job_kind: job for job in result.jobs}


def test_scheduler_state_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = _web_db(tmp_path)

    ensure_web_db(db_path)
    ensure_web_db(db_path)

    assert _table_columns(db_path, STATE_TABLE) == {
        "instance",
        "job_kind",
        "last_attempt_at",
        "last_success_at",
        "last_failure_at",
        "next_due_at",
        "failure_count",
        "updated_at",
    }
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM web_schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row == (WEB_SCHEMA_VERSION,)
    assert runner.list_player_session_scheduler_state(db_path) == ()
    assert set(policy.automatic_job_policy_by_kind()) == set(
        policy.AUTOMATIC_SESSION_JOB_KINDS
    )


def test_run_once_enqueues_only_due_allowed_jobs_and_respects_min_intervals(
    tmp_path: Path,
) -> None:
    db_path = _web_db(tmp_path)

    result = runner.run_player_session_scheduler_once(db_path, now=NOW)
    jobs = list_recent_jobs(db_path)
    states = _state_by_kind(db_path)

    assert result.success is True
    assert result.due_count == 3
    assert result.enqueued_count == 3
    assert result.active_count == 0
    assert result.failed_count == 0
    assert {job.kind for job in jobs} == set(policy.AUTOMATIC_SESSION_JOB_KINDS)
    assert len(jobs) == len(policy.AUTOMATIC_SESSION_JOB_KINDS)
    assert all(job.requested_by_username == runner.SCHEDULER_USERNAME for job in jobs)
    assert set(states) == set(policy.AUTOMATIC_SESSION_JOB_KINDS)
    for job_policy in policy.AUTOMATIC_SESSION_JOB_POLICIES:
        state = states[job_policy.job_kind]
        assert state.last_attempt_at == NOW.isoformat()
        assert state.last_success_at == NOW.isoformat()
        assert state.last_failure_at == ""
        assert state.next_due_at == (NOW + job_policy.minimum_interval).isoformat()
        assert state.failure_count == 0

    second = runner.run_player_session_scheduler_once(
        db_path,
        now=NOW + timedelta(seconds=1),
    )

    assert {job.outcome for job in second.jobs} == {"not_due"}
    assert len(list_recent_jobs(db_path)) == len(policy.AUTOMATIC_SESSION_JOB_KINDS)
    assert _table_count(db_path, "web_current_roster_cache") == 0
    assert _table_count(db_path, "web_current_roster_cache_players") == 0


@pytest.mark.parametrize("running", [False, True])
def test_active_queued_or_running_job_prevents_duplicate_enqueue(
    tmp_path: Path,
    running: bool,
) -> None:
    db_path = _web_db(tmp_path)
    existing = create_job(
        db_path,
        kind="players:scan-live-sessions",
        requested_by_username="operator",
    )
    if running:
        mark_job_running(db_path, existing.id)

    result = runner.run_player_session_scheduler_once(db_path, now=NOW)
    decisions = _decision_by_kind(result)
    scan_jobs = [
        job for job in list_recent_jobs(db_path) if job.kind == "players:scan-live-sessions"
    ]

    assert decisions["players:scan-live-sessions"].outcome == "active"
    assert decisions["players:scan-live-sessions"].created is False
    assert decisions["players:scan-live-sessions"].job_id == existing.id
    assert len(scan_jobs) == 1
    assert result.enqueued_count == 2
    assert result.active_count == 1


def test_failure_backoff_advances_and_caps_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _web_db(tmp_path)
    job_kind = "players:scan-live-sessions"
    job_policy = policy.automatic_job_policy_by_kind()[job_kind]

    def fail_enqueue(_db_path: Path, _instance: str):
        raise RuntimeError(
            "failed with token=raw-secret 198.51.100.9 /home/deus/private.log"
        )

    monkeypatch.setitem(runner._ENQUEUE_BY_KIND, job_kind, fail_enqueue)

    first = runner.run_player_session_scheduler_once(db_path, now=NOW)
    first_decision = _decision_by_kind(first)[job_kind]
    first_state = runner.get_player_session_scheduler_state(db_path, job_kind=job_kind)

    assert first.success is False
    assert first_decision.outcome == "failed"
    assert first_decision.failure_count == 1
    assert first_state is not None
    assert first_state.last_attempt_at == NOW.isoformat()
    assert first_state.last_failure_at == NOW.isoformat()
    assert first_state.last_success_at == ""
    assert first_state.next_due_at == (NOW + job_policy.initial_failure_backoff).isoformat()

    second_now = NOW + job_policy.initial_failure_backoff
    second = runner.run_player_session_scheduler_once(db_path, now=second_now)
    second_decision = _decision_by_kind(second)[job_kind]

    assert second_decision.outcome == "failed"
    assert second_decision.failure_count == 2
    assert second_decision.next_due_at == (
        second_now + job_policy.initial_failure_backoff * 2
    ).isoformat()

    capped_now = NOW + timedelta(hours=2)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"""
            UPDATE {STATE_TABLE}
            SET next_due_at = ?, failure_count = ?
            WHERE instance = 'default' AND job_kind = ?
            """,
            (capped_now.isoformat(), runner.MAX_STORED_FAILURE_COUNT - 1, job_kind),
        )

    capped = runner.run_player_session_scheduler_once(db_path, now=capped_now)
    capped_decision = _decision_by_kind(capped)[job_kind]

    assert capped_decision.outcome == "failed"
    assert capped_decision.failure_count == runner.MAX_STORED_FAILURE_COUNT
    assert capped_decision.next_due_at == (
        capped_now + job_policy.maximum_failure_backoff
    ).isoformat()


def test_scheduler_state_stores_no_raw_sensitive_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _web_db(tmp_path)

    def fail_enqueue(_db_path: Path, _instance: str):
        raise RuntimeError(
            "raw line token=raw-secret 198.51.100.9 /home/deus/private.log raw_rcon_row"
        )

    monkeypatch.setitem(
        runner._ENQUEUE_BY_KIND,
        "players:scan-live-sessions",
        fail_enqueue,
    )

    runner.run_player_session_scheduler_once(db_path, now=NOW)

    columns = {column.lower() for column in _table_columns(db_path, STATE_TABLE)}
    for forbidden_column_fragment in (
        "raw",
        "output",
        "path",
        "ip",
        "rcon",
        "secret",
        "source_ref",
        "public_player_id",
    ):
        assert all(forbidden_column_fragment not in column for column in columns)

    stored_text = _scheduler_db_text(db_path)
    for forbidden in (
        "raw-secret",
        "198.51.100.9",
        "/home/deus/private.log",
        "raw_rcon_row",
        "token=",
    ):
        assert forbidden not in stored_text


def test_players_sessions_scheduler_status_cli_missing_and_empty_state_is_read_only(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing"

    missing = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "status",
            "--data-root",
            str(missing_root),
        ],
    )

    assert missing.exit_code == 0
    assert "Player session scheduler status (read-only)." in missing.output
    assert "empty (web_db_missing)" in missing.output
    assert "no service, timer, or daemon is installed/enabled" in missing.output
    for job_kind in policy.AUTOMATIC_SESSION_JOB_KINDS:
        assert job_kind in missing.output
    assert not (missing_root / "web" / "web.db").exists()
    assert not (missing_root / "default" / "players.db").exists()

    empty_root = tmp_path / "empty"
    empty_db_path = _web_db(empty_root)
    ensure_web_db(empty_db_path)

    empty = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "status",
            "--data-root",
            str(empty_root),
        ],
    )

    assert empty.exit_code == 0
    assert "empty (no_rows)" in empty.output
    assert "State rows:     0" in empty.output
    assert _job_count(empty_db_path) == 0
    assert not (empty_root / "default" / "players.db").exists()


def test_players_sessions_scheduler_status_cli_after_run_once_does_not_enqueue(
    tmp_path: Path,
) -> None:
    run_result = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "run",
            "--once",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert run_result.exit_code == 0

    db_path = _web_db(tmp_path)
    before_count = _job_count(db_path)
    status_result = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "status",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert status_result.exit_code == 0
    assert "available (ok)" in status_result.output
    assert f"State rows:     {len(policy.AUTOMATIC_SESSION_JOB_KINDS)}" in status_result.output
    for job_kind in policy.AUTOMATIC_SESSION_JOB_KINDS:
        assert job_kind in status_result.output
    assert "not due" in status_result.output
    assert _job_count(db_path) == before_count

    second_status = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "status",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert second_status.exit_code == 0
    assert _job_count(db_path) == before_count


def test_players_sessions_scheduler_status_cli_outputs_only_safe_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _web_db(tmp_path)

    def fail_enqueue(_db_path: Path, _instance: str):
        raise RuntimeError(
            "raw line token=raw-secret 198.51.100.9 /home/deus/private.log raw_rcon_row"
        )

    monkeypatch.setitem(
        runner._ENQUEUE_BY_KIND,
        "players:scan-live-sessions",
        fail_enqueue,
    )
    runner.run_player_session_scheduler_once(db_path, now=NOW)

    text_result = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "status",
            "--data-root",
            str(tmp_path),
        ],
    )
    json_result = CliRunner().invoke(
        main,
        [
            "--json-output",
            "players",
            "sessions",
            "scheduler",
            "status",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert text_result.exit_code == 0
    assert json_result.exit_code == 0
    for output in (text_result.output, json_result.output):
        for forbidden in (
            "raw-secret",
            "198.51.100.9",
            "/home/deus/private.log",
            "raw_rcon_row",
            "token=",
            "source_ref",
            "public_player_id",
            "job_id",
            "stdout",
            "stderr",
            "error_message",
        ):
            assert forbidden not in output


def test_players_sessions_scheduler_run_once_cli_is_explicit_opt_in(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "run",
            "--once",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Player session scheduler checked due jobs." in result.output
    assert "explicit --once only" in result.output
    assert "no service, timer, or daemon is installed/enabled" in result.output
    assert {job.kind for job in list_recent_jobs(_web_db(tmp_path))} == set(
        policy.AUTOMATIC_SESSION_JOB_KINDS
    )

    no_once_root = tmp_path / "no-once"
    no_once = CliRunner().invoke(
        main,
        [
            "players",
            "sessions",
            "scheduler",
            "run",
            "--data-root",
            str(no_once_root),
        ],
    )

    assert no_once.exit_code == 1
    assert "Only --once is supported" in no_once.output
    assert not (no_once_root / "web" / "web.db").exists()
