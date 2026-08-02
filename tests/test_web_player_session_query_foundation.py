"""Regression coverage for Slice 6b stored-session query foundations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from armactl.web.services import (
    player_registry,
    player_session_details,
)

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"
PLAYER_DELTA_ID = "44444444-4444-4444-8444-444444444444"
PLAYER_ECHO_ID = "55555555-5555-4555-8555-555555555555"


def _open_session(
    db_path: Path,
    reliable_id: str = PLAYER_ALPHA_ID,
    *,
    name: str = "Alpha",
    opened_at: str = "2026-07-10T12:00:00+00:00",
    **kwargs,
) -> player_registry.PlayerSessionRecord:
    result = player_registry.observe_player_session(
        db_path,
        reliable_id=reliable_id,
        display_name=name,
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at=opened_at,
        **kwargs,
    )
    assert result.session is not None
    return result.session


def _close_session(
    db_path: Path,
    reliable_id: str = PLAYER_ALPHA_ID,
    *,
    closed_at: str = "2026-07-10T12:10:00+00:00",
    source: str = player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    end_reason: str = player_registry.PLAYER_SESSION_END_REASON_IMPORT_WINDOW,
) -> player_registry.PlayerSessionRecord:
    result = player_registry.close_player_session(
        db_path,
        reliable_id=reliable_id,
        close_observed_at=closed_at,
        source=source,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        end_reason=end_reason,
    )
    assert result.session is not None
    return result.session


def _record_alias(
    db_path: Path,
    reliable_id: str,
    alias: str,
    observed_at: str,
) -> None:
    player_registry.record_current_players_snapshot(
        db_path,
        [
            player_registry.PlayerObservation(
                reliable_id=reliable_id,
                display_name=alias,
                source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
            )
        ],
        observed_at=observed_at,
    )


def test_query_only_lookup_never_creates_migrates_or_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    missing_path = tmp_path / "missing" / "players.db"
    missing = player_registry.get_player_session_readonly(missing_path, 1)
    assert missing.status == player_registry.PLAYER_SESSION_READ_STATUS_UNAVAILABLE
    assert not missing_path.exists()

    db_path = tmp_path / "default" / "players.db"
    session = _open_session(db_path)
    before_bytes = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns

    def _unexpected_ensure(_db_path: Path) -> Path:
        raise AssertionError("query-only read called schema ensure")

    monkeypatch.setattr(
        player_registry,
        "ensure_player_registry_db",
        _unexpected_ensure,
    )
    connection = player_registry._connect_existing_readonly(db_path)
    assert connection is not None
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden_write(value TEXT)")
    finally:
        connection.close()

    result = player_registry.get_player_session_readonly(
        db_path,
        session.session_id,
    )
    assert result.status == player_registry.PLAYER_SESSION_READ_STATUS_OK
    assert result.session == session
    query = player_registry.query_player_sessions(db_path)
    detail = player_session_details.load_player_session_detail(
        db_path,
        session.session_id,
    )
    assert query.status == player_registry.PLAYER_SESSION_QUERY_STATUS_OK
    assert detail.status == player_session_details.PLAYER_SESSION_DETAIL_STATUS_OK
    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime


def test_missing_legacy_and_invalid_session_reads_are_controlled(tmp_path: Path):
    legacy_path = tmp_path / "legacy" / "players.db"
    legacy_path.parent.mkdir(parents=True)
    with sqlite3.connect(legacy_path) as connection:
        connection.execute(
            "CREATE TABLE player_sessions("
            "session_id INTEGER PRIMARY KEY, reliable_id TEXT NOT NULL)"
        )
    before = legacy_path.read_bytes()

    legacy = player_registry.get_player_session_readonly(legacy_path, 1)
    legacy_search = player_registry.query_player_sessions(legacy_path)
    assert legacy.status == player_registry.PLAYER_SESSION_READ_STATUS_UNAVAILABLE
    assert legacy_search.status == player_registry.PLAYER_SESSION_QUERY_STATUS_UNAVAILABLE
    assert legacy_path.read_bytes() == before

    db_path = tmp_path / "default" / "players.db"
    session = _open_session(db_path)
    assert player_registry.get_player_session_readonly(
        db_path,
        0,
    ).status == player_registry.PLAYER_SESSION_READ_STATUS_INVALID_ID
    assert player_registry.get_player_session_readonly(
        db_path,
        "not-an-id",
    ).status == player_registry.PLAYER_SESSION_READ_STATUS_INVALID_ID
    assert player_registry.get_player_session_readonly(
        db_path,
        session.session_id + 100,
    ).status == player_registry.PLAYER_SESSION_READ_STATUS_NOT_FOUND
    exact = player_registry.get_player_session_readonly(
        db_path,
        session.session_id,
    )
    assert exact.status == player_registry.PLAYER_SESSION_READ_STATUS_OK
    assert exact.session is not None
    assert exact.session.session_id == session.session_id


def test_alias_search_preserves_identity_and_deduplicates_rows(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    alpha = _open_session(db_path, PLAYER_ALPHA_ID, name="Alpha Base")
    bravo = _open_session(
        db_path,
        PLAYER_BRAVO_ID,
        name="Bravo Base",
        opened_at="2026-07-10T12:01:00+00:00",
    )
    _record_alias(
        db_path,
        PLAYER_ALPHA_ID,
        "Shared Night Fox",
        "2026-07-10T12:02:00+00:00",
    )
    _record_alias(
        db_path,
        PLAYER_ALPHA_ID,
        "Shared Night Fox Prime",
        "2026-07-10T12:03:00+00:00",
    )
    _record_alias(
        db_path,
        PLAYER_BRAVO_ID,
        "Shared Night Fox",
        "2026-07-10T12:04:00+00:00",
    )

    result = player_registry.query_player_sessions(
        db_path,
        query="Shared Night",
        limit=10,
    )
    assert result.status == player_registry.PLAYER_SESSION_QUERY_STATUS_OK
    assert {item.session_id for item in result.sessions} == {
        alpha.session_id,
        bravo.session_id,
    }
    assert len(result.sessions) == 2
    assert {item.reliable_id for item in result.sessions} == {
        PLAYER_ALPHA_ID,
        PLAYER_BRAVO_ID,
    }

    existing_api = player_registry.list_player_sessions(
        db_path,
        query="Shared Night",
    )
    assert {item.session_id for item in existing_api} == {
        alpha.session_id,
        bravo.session_id,
    }

    bounded = player_registry.query_player_sessions(db_path, limit=9999)
    assert bounded.limit == player_registry.MAX_PLAYER_SESSION_LIST_LIMIT
    one = player_registry.query_player_sessions(db_path, limit=0)
    assert one.limit == 1
    assert len(one.sessions) == 1
    truncated_query = player_registry.query_player_sessions(
        db_path,
        query=("x" * player_registry.MAX_PLAYER_SESSION_QUERY_LENGTH)
        + "Shared Night",
    )
    assert truncated_query.sessions == ()


def test_keyset_pages_are_stable_without_duplicates_or_skips(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    rows = (
        (PLAYER_ALPHA_ID, "Alpha", "12:00:00", "12:10:00"),
        (PLAYER_BRAVO_ID, "Bravo", "12:01:00", "12:10:00"),
        (PLAYER_CHARLIE_ID, "Charlie", "12:02:00", "12:09:00"),
        (PLAYER_DELTA_ID, "Delta", "12:03:00", "12:08:00"),
        (PLAYER_ECHO_ID, "Echo", "12:04:00", "12:07:00"),
    )
    for reliable_id, name, opened, closed in rows:
        _open_session(
            db_path,
            reliable_id,
            name=name,
            opened_at=f"2026-07-10T{opened}+00:00",
        )
        _close_session(
            db_path,
            reliable_id,
            closed_at=f"2026-07-10T{closed}+00:00",
        )

    all_ids: list[int] = []
    before_time: str | object = ""
    before_session_id: int | object = ""
    while True:
        page = player_registry.query_player_sessions(
            db_path,
            limit=2,
            before_time=before_time,
            before_session_id=before_session_id,
        )
        assert page.status == player_registry.PLAYER_SESSION_QUERY_STATUS_OK
        all_ids.extend(session.session_id for session in page.sessions)
        if page.next_cursor is None:
            break
        before_time = page.next_cursor.evidence_time
        before_session_id = page.next_cursor.session_id

    expected = player_registry.query_player_sessions(db_path, limit=10)
    assert all_ids == [session.session_id for session in expected.sessions]
    assert len(all_ids) == len(set(all_ids)) == len(rows)
    assert all_ids[:2] == sorted(all_ids[:2], reverse=True)

    for before_time, before_session_id in (
        ("bad", 1),
        ("2026-07-10T12:00:00+00:00", ""),
        ("", 1),
        ("2026-07-10T12:00:00", 1),
        ("2026-07-10T12:00:00+00:00", -1),
    ):
        malformed = player_registry.query_player_sessions(
            db_path,
            before_time=before_time,
            before_session_id=before_session_id,
        )
        assert malformed.status == (
            player_registry.PLAYER_SESSION_QUERY_STATUS_INVALID_CURSOR
        )
        assert malformed.sessions == ()


def test_keyset_query_keeps_existing_exact_filters(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    _open_session(db_path, PLAYER_ALPHA_ID, name="Alpha First")
    closed = _close_session(
        db_path,
        PLAYER_ALPHA_ID,
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    )
    _open_session(
        db_path,
        PLAYER_BRAVO_ID,
        name="Bravo",
        opened_at="2026-07-10T12:12:00+00:00",
    )

    result = player_registry.query_player_sessions(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        status=player_registry.PLAYER_SESSION_STATUS_CLOSED,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    )
    assert result.status == player_registry.PLAYER_SESSION_QUERY_STATUS_OK
    assert [session.session_id for session in result.sessions] == [
        closed.session_id
    ]


def test_session_query_time_bounds_and_invalid_filters_fail_closed(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    older = _open_session(
        db_path,
        PLAYER_ALPHA_ID,
        name="Older",
        opened_at="2026-07-10T10:00:00+00:00",
    )
    _close_session(
        db_path,
        PLAYER_ALPHA_ID,
        closed_at="2026-07-10T10:30:00+00:00",
    )
    inside = _open_session(
        db_path,
        PLAYER_BRAVO_ID,
        name="Inside",
        opened_at="2026-07-10T12:00:00+00:00",
    )
    _close_session(
        db_path,
        PLAYER_BRAVO_ID,
        closed_at="2026-07-10T12:30:00+00:00",
    )
    newer = _open_session(
        db_path,
        PLAYER_CHARLIE_ID,
        name="Newer",
        opened_at="2026-07-10T14:00:00+00:00",
    )

    bounded = player_session_details.search_player_sessions(
        db_path,
        from_time="2026-07-10T11:00:00+00:00",
        to_time="2026-07-10T13:00:00+00:00",
    )
    assert bounded.status == player_registry.PLAYER_SESSION_QUERY_STATUS_OK
    assert [item.session_id for item in bounded.items] == [inside.session_id]
    assert older.session_id not in {item.session_id for item in bounded.items}
    assert newer.session_id not in {item.session_id for item in bounded.items}

    invalid_filters = (
        {"reliable_id": "not-a-reliable-id"},
        {"status": "anything"},
        {"end_reason": "anything"},
        {"source": "private/path.log"},
        {"from_time": "not-a-time"},
        {"to_time": "2026-07-10T12:00:00"},
        {
            "from_time": "2026-07-10T13:00:00+00:00",
            "to_time": "2026-07-10T12:00:00+00:00",
        },
    )
    for filters in invalid_filters:
        result = player_registry.query_player_sessions(db_path, **filters)
        assert result.status == (
            player_registry.PLAYER_SESSION_QUERY_STATUS_INVALID_FILTER
        )
        assert result.sessions == ()


def test_session_event_query_validates_window_and_cursor_without_writes(
    tmp_path: Path,
):
    missing_path = tmp_path / "missing" / "players.db"
    missing = player_registry.query_player_session_events(
        missing_path,
        reliable_id=PLAYER_ALPHA_ID,
        window_started_at="2026-07-10T12:00:00+00:00",
        window_ended_at="2026-07-10T12:10:00+00:00",
    )
    assert missing.status == player_registry.PLAYER_SESSION_EVENT_QUERY_STATUS_UNAVAILABLE
    assert not missing_path.exists()

    db_path = tmp_path / "default" / "players.db"
    _open_session(db_path)
    for kwargs in (
        {"reliable_id": "bad"},
        {"window_started_at": "bad"},
        {"window_ended_at": "2026-07-10T12:10:00"},
        {
            "window_started_at": "2026-07-10T12:11:00+00:00",
            "window_ended_at": "2026-07-10T12:10:00+00:00",
        },
    ):
        values = {
            "reliable_id": PLAYER_ALPHA_ID,
            "window_started_at": "2026-07-10T12:00:00+00:00",
            "window_ended_at": "2026-07-10T12:10:00+00:00",
            **kwargs,
        }
        invalid = player_registry.query_player_session_events(db_path, **values)
        assert invalid.status == (
            player_registry.PLAYER_SESSION_EVENT_QUERY_STATUS_INVALID_WINDOW
        )

    invalid_cursor = player_registry.query_player_session_events(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        window_started_at="2026-07-10T12:00:00+00:00",
        window_ended_at="2026-07-10T12:10:00+00:00",
        before_time="2026-07-10T12:05:00+00:00",
    )
    assert invalid_cursor.status == (
        player_registry.PLAYER_SESSION_EVENT_QUERY_STATUS_INVALID_CURSOR
    )
