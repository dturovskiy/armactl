"""Tests for SQLite-backed web login throttling."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from armactl.web.auth.rate_limit import (
    MAX_FAILED_ATTEMPTS,
    RETENTION_SECONDS,
    WINDOW_SECONDS,
    check_login_allowed,
    clear_login_failures,
    make_login_rate_limit_key,
    record_login_failure,
)
from armactl.web.runtime import ensure_web_db

SECRET = "test-session-secret-for-rate-limit"
CLIENT_IP = "203.0.113.44"
USERNAME = " Owner "


def _db_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    return db_path


def _rate_limit_rows(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM web_login_rate_limits").fetchall()
    return list(rows)


def test_failures_accumulate_and_lockout_after_threshold(tmp_path: Path):
    db_path = _db_path(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    initial = check_login_allowed(db_path, SECRET, CLIENT_IP, USERNAME, now=now)
    assert initial.allowed is True
    assert initial.failure_count == 0

    for attempt in range(1, MAX_FAILED_ATTEMPTS):
        status = record_login_failure(db_path, SECRET, CLIENT_IP, USERNAME, now=now)
        assert status.allowed is True
        assert status.failure_count == attempt

    locked = record_login_failure(db_path, SECRET, CLIENT_IP, USERNAME, now=now)
    checked = check_login_allowed(db_path, SECRET, CLIENT_IP, USERNAME, now=now)

    assert locked.allowed is False
    assert locked.failure_count == MAX_FAILED_ATTEMPTS
    assert locked.retry_after_seconds > 0
    assert checked.allowed is False
    assert checked.retry_after_seconds > 0


def test_success_clears_throttle_state(tmp_path: Path):
    db_path = _db_path(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    record_login_failure(db_path, SECRET, CLIENT_IP, USERNAME, now=now)
    clear_login_failures(db_path, SECRET, CLIENT_IP, USERNAME)

    assert _rate_limit_rows(db_path) == []
    assert check_login_allowed(db_path, SECRET, CLIENT_IP, USERNAME, now=now).allowed is True


def test_expired_window_resets_failure_count(tmp_path: Path):
    db_path = _db_path(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    later = now + timedelta(seconds=WINDOW_SECONDS + 1)

    first = record_login_failure(db_path, SECRET, CLIENT_IP, USERNAME, now=now)
    reset = record_login_failure(db_path, SECRET, CLIENT_IP, USERNAME, now=later)

    assert first.failure_count == 1
    assert reset.allowed is True
    assert reset.failure_count == 1
    rows = _rate_limit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["failure_count"] == 1


def test_database_stores_digest_only_not_raw_ip_or_username(tmp_path: Path):
    db_path = _db_path(tmp_path)
    raw_ip = "198.51.100.77"
    raw_username = "OwnerName"
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    status = record_login_failure(db_path, SECRET, raw_ip, raw_username, now=now)
    rows = _rate_limit_rows(db_path)
    stored_text = "|".join(str(value) for row in rows for value in tuple(row))

    assert len(rows) == 1
    assert rows[0]["key_digest"] == status.key_digest
    assert raw_ip not in stored_text
    assert raw_username not in stored_text
    assert raw_username.casefold() not in stored_text


def test_stale_rate_limit_rows_are_pruned_on_new_failure(tmp_path: Path):
    db_path = _db_path(tmp_path)
    now = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    stale_time = now - timedelta(seconds=RETENTION_SECONDS + 1)

    record_login_failure(db_path, SECRET, CLIENT_IP, "old-owner", now=stale_time)
    assert len(_rate_limit_rows(db_path)) == 1

    fresh = record_login_failure(db_path, SECRET, CLIENT_IP, "new-owner", now=now)
    rows = _rate_limit_rows(db_path)

    assert len(rows) == 1
    assert rows[0]["key_digest"] == fresh.key_digest


def test_rate_limit_key_uses_existing_username_normalization():
    assert make_login_rate_limit_key(SECRET, CLIENT_IP, " Owner ") == make_login_rate_limit_key(
        SECRET,
        CLIENT_IP,
        "OWNER",
    )
