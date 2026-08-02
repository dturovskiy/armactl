"""Regression coverage for Slice 6b shared stats and sanitized detail DTOs."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from armactl import player_log_events
from armactl.web.services import (
    player_current_enrichment,
    player_registry,
    player_session_details,
    player_session_stats,
)

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def _event(
    event_type: str,
    occurred_at: str,
    **kwargs,
) -> player_log_events.PlayerLogEvent:
    source = (
        player_log_events.SOURCE_SCRIPT_FACTION_JOIN
        if event_type == player_log_events.EVENT_TYPE_FACTION_JOIN
        else player_log_events.SOURCE_SCRIPT_KILL
    )
    return player_log_events.PlayerLogEvent(
        event_type=event_type,
        source=source,
        confidence=player_log_events.CONFIDENCE_HIGH,
        occurred_at=occurred_at,
        observed_at=occurred_at,
        time_source=player_log_events.EVENT_TIME_SOURCE_CALLER_OCCURRED_AT,
        time_confidence=kwargs.pop(
            "time_confidence",
            player_log_events.EVENT_TIME_CONFIDENCE_EXACT,
        ),
        raw_source_ref=kwargs.pop("raw_source_ref", "/private/server.log:42"),
        **kwargs,
    )


def _open_session(
    db_path: Path,
    *,
    opened_at: str = "2026-07-10T12:00:00+00:00",
    **kwargs,
) -> player_registry.PlayerSessionRecord:
    result = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at=opened_at,
        **kwargs,
    )
    assert result.session is not None
    return result.session


def _close_session(
    db_path: Path,
    *,
    closed_at: str,
    end_reason: str = player_registry.PLAYER_SESSION_END_REASON_DISCONNECT,
) -> player_registry.PlayerSessionRecord:
    result = player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at=closed_at,
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        end_reason=end_reason,
    )
    assert result.session is not None
    return result.session


def _record_freshness(
    db_path: Path,
    covered_through: str,
    *,
    covered_from: str = "2026-07-10T11:55:00+00:00",
    status: str = player_registry.PLAYER_LOG_INGEST_STATUS_FRESH,
) -> None:
    scope = player_session_stats.CURRENT_STATS_INGEST_SCOPE
    player_registry.upsert_player_log_ingest_checkpoints(
        db_path,
        [
            player_registry.PlayerLogIngestCheckpoint(
                scope=scope,
                source_key="fixture-log",
                source_label="allowlisted console log",
                size_bytes=128,
                mtime_ns=1,
                fingerprint="fixture",
                status="scanned",
                last_scanned_at=covered_through,
                updated_at=covered_through,
                next_offset=128,
                coverage_started_at=covered_from,
            )
        ],
    )
    player_registry.record_player_log_ingest_freshness(
        db_path,
        scope=scope,
        status=status,
        last_run_at=covered_through,
        scanned_files=1,
        parsed_events=0,
        stored_events=0,
        skipped_files=0,
        checkpoint_updated=True,
        coverage_started_at=covered_from,
    )


def test_detail_result_controls_missing_invalid_and_legacy_storage(tmp_path: Path):
    missing_path = tmp_path / "missing" / "players.db"
    missing = player_session_details.load_player_session_detail(missing_path, 1)
    assert missing.status == player_session_details.PLAYER_SESSION_DETAIL_STATUS_UNAVAILABLE
    assert missing.detail is None
    assert not missing_path.exists()

    db_path = tmp_path / "default" / "players.db"
    session = _open_session(db_path)
    invalid = player_session_details.load_player_session_detail(db_path, "bad")
    not_found = player_session_details.load_player_session_detail(
        db_path,
        session.session_id + 100,
    )
    assert invalid.status == player_session_details.PLAYER_SESSION_DETAIL_STATUS_INVALID_ID
    assert not_found.status == player_session_details.PLAYER_SESSION_DETAIL_STATUS_NOT_FOUND
    assert invalid.detail is None
    assert not_found.detail is None

    legacy_path = tmp_path / "legacy" / "players.db"
    legacy_path.parent.mkdir(parents=True)
    with sqlite3.connect(legacy_path) as connection:
        connection.execute(
            "CREATE TABLE player_sessions("
            "session_id INTEGER PRIMARY KEY, reliable_id TEXT NOT NULL)"
        )
    legacy = player_session_details.load_player_session_detail(legacy_path, 1)
    assert legacy.status == (
        player_session_details.PLAYER_SESSION_DETAIL_STATUS_UNAVAILABLE
    )
    assert legacy.detail is None


def test_open_detail_stats_match_current_enrichment_rules(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    session = _open_session(db_path)
    events = [
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:01:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_ALPHA_ID,
            instigator_faction="US",
        ),
        _event(
            player_log_events.EVENT_TYPE_TEAMKILL,
            "2026-07-10T12:02:00+00:00",
            victim_id=PLAYER_CHARLIE_ID,
            instigator_id=PLAYER_ALPHA_ID,
            instigator_faction="US",
            teamkill=True,
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:03:00+00:00",
            victim_id=PLAYER_ALPHA_ID,
            victim_faction="US",
            instigator_id=PLAYER_BRAVO_ID,
        ),
        _event(
            player_log_events.EVENT_TYPE_SUICIDE,
            "2026-07-10T12:04:00+00:00",
            victim_id=PLAYER_ALPHA_ID,
            victim_faction="US",
            suicide=True,
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:05:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_ALPHA_ID,
            ai_instigator=True,
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:06:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_ALPHA_ID,
            time_confidence=player_log_events.EVENT_TIME_CONFIDENCE_AMBIGUOUS,
        ),
        _event(
            player_log_events.EVENT_TYPE_FACTION_JOIN,
            "2026-07-10T12:07:00+00:00",
            player_id=PLAYER_ALPHA_ID,
            player_faction="US_Army",
        ),
    ]
    player_registry.ingest_player_log_events(
        db_path,
        events,
        ingested_at="2026-07-10T12:08:00+00:00",
    )
    _record_freshness(db_path, "2026-07-10T12:08:00+00:00")

    now_at = "2026-07-10T12:08:30+00:00"
    detail_result = player_session_details.load_player_session_detail(
        db_path,
        session.session_id,
        now_at=now_at,
    )
    current = player_current_enrichment.load_current_player_enrichment(
        data_root=tmp_path,
        reliable_ids=[PLAYER_ALPHA_ID],
        now_at=now_at,
    )[PLAYER_ALPHA_ID]

    assert detail_result.status == player_session_details.PLAYER_SESSION_DETAIL_STATUS_OK
    assert detail_result.detail is not None
    stats = detail_result.detail.stats
    assert stats.stats_available is True
    assert (stats.kills, stats.deaths, stats.teamkills, stats.faction) == (
        current.kills,
        current.deaths,
        current.teamkills,
        current.faction,
    ) == (1, 2, 1, "US_Army")
    assert stats.window_started_at == current.stats_window_started_at
    assert stats.window_ended_at == current.stats_window_ended_at


def test_closed_detail_stats_require_coverage_through_close(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    _open_session(db_path)
    player_registry.ingest_player_log_events(
        db_path,
        [
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:02:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:05:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
        ],
        ingested_at="2026-07-10T12:06:00+00:00",
    )
    closed = _close_session(db_path, closed_at="2026-07-10T12:04:00+00:00")
    _record_freshness(db_path, "2026-07-10T12:06:00+00:00")

    available = player_session_details.load_player_session_detail(
        db_path,
        closed.session_id,
        now_at="2026-07-10T12:06:30+00:00",
    )
    assert available.detail is not None
    assert available.detail.stats.stats_available is True
    assert available.detail.stats.kills == 1
    assert available.detail.stats.window_ended_at == "2026-07-10T12:04:00+00:00"
    assert available.detail.stats.covered_through == "2026-07-10T12:06:00+00:00"

    _record_freshness(db_path, "2026-07-10T12:03:59+00:00")
    insufficient = player_session_details.load_player_session_detail(
        db_path,
        closed.session_id,
        now_at="2026-07-10T12:04:00+00:00",
    )
    assert insufficient.detail is not None
    stats = insufficient.detail.stats
    assert stats.stats_available is False
    assert stats.kills is None
    assert stats.deaths is None
    assert stats.teamkills is None
    assert stats.stats_unavailable_reason == (
        player_session_stats.CURRENT_STATS_SESSION_COVERAGE_REASON
    )


@pytest.mark.parametrize(
    ("status", "now_at", "expected_reason"),
    [
        (
            player_registry.PLAYER_LOG_INGEST_STATUS_PARTIAL,
            "2026-07-10T12:01:00+00:00",
            player_session_stats.CURRENT_STATS_FRESHNESS_INCOMPLETE_REASON,
        ),
        (
            player_registry.PLAYER_LOG_INGEST_STATUS_NO_LOGS,
            "2026-07-10T12:01:00+00:00",
            player_session_stats.CURRENT_STATS_FRESHNESS_INCOMPLETE_REASON,
        ),
        (
            player_registry.PLAYER_LOG_INGEST_STATUS_FRESH,
            "2026-07-10T12:07:00+00:00",
            player_session_stats.CURRENT_STATS_FRESHNESS_STALE_REASON,
        ),
    ],
)
def test_incomplete_or_stale_coverage_never_returns_fake_zeroes(
    tmp_path: Path,
    status: str,
    now_at: str,
    expected_reason: str,
):
    db_path = tmp_path / status / "players.db"
    session = _open_session(db_path)
    _record_freshness(
        db_path,
        "2026-07-10T12:01:00+00:00",
        status=status,
    )

    result = player_session_details.load_player_session_detail(
        db_path,
        session.session_id,
        now_at=now_at,
    )
    assert result.detail is not None
    stats = result.detail.stats
    assert stats.stats_available is False
    assert stats.kills is None
    assert stats.deaths is None
    assert stats.teamkills is None
    assert stats.stats_unavailable_reason == expected_reason


@pytest.mark.parametrize("conflict", ["server_run", "lifecycle"])
def test_server_run_or_lifecycle_conflict_makes_stats_unavailable(
    tmp_path: Path,
    conflict: str,
):
    db_path = tmp_path / conflict / "players.db"
    session = _open_session(db_path)
    if conflict == "server_run":
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE player_sessions SET server_run_key = ? WHERE session_id = ?",
                ("boundary:999", session.session_id),
            )
    else:
        player_registry.record_player_session_lifecycle_boundary(
            db_path,
            boundary_at="2026-07-10T12:03:00+00:00",
            source=player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
        )
    _record_freshness(db_path, "2026-07-10T12:04:00+00:00")

    result = player_session_details.load_player_session_detail(
        db_path,
        session.session_id,
        now_at="2026-07-10T12:04:30+00:00",
    )
    assert result.detail is not None
    assert result.detail.stats.stats_available is False
    assert result.detail.stats.kills is None
    assert result.detail.stats.stats_unavailable_reason == (
        player_session_stats.CURRENT_STATS_SESSION_UNPROVEN_REASON
    )


def test_reconnect_merged_closed_window_uses_stored_decision(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    first = _open_session(db_path)
    _close_session(db_path, closed_at="2026-07-10T12:02:00+00:00")
    reopened = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha Again",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-07-10T12:03:00+00:00",
        reconnect_grace_seconds=600,
    )
    assert reopened.reconnected is True
    assert reopened.session is not None
    assert reopened.session.session_id == first.session_id
    closed = _close_session(db_path, closed_at="2026-07-10T12:05:00+00:00")
    _record_freshness(db_path, "2026-07-10T12:06:00+00:00")

    result = player_session_details.load_player_session_detail(
        db_path,
        closed.session_id,
        now_at="2026-07-10T12:06:30+00:00",
    )
    assert result.detail is not None
    assert result.detail.stats.stats_available is True
    assert result.detail.stats.reconnect_merged is True
    assert result.detail.stats.window_started_at == first.open_observed_at
    assert result.detail.reconnect_merge_count == 1


def test_detail_and_search_dtos_exclude_sensitive_internal_fields(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    session = _open_session(
        db_path,
        source_ref="/private/console.log:42?token=secret",
        rpl_identity="rpl-secret",
        connection_id="connection-secret",
        session_player_id="session-player-secret",
        be_slot="99",
    )
    _record_freshness(db_path, "2026-07-10T12:01:00+00:00")

    read_result = player_registry.get_player_session_readonly(
        db_path,
        session.session_id,
    )
    query_result = player_registry.query_player_sessions(db_path)
    detail_result = player_session_details.load_player_session_detail(
        db_path,
        session.session_id,
        now_at="2026-07-10T12:01:30+00:00",
    )
    search_result = player_session_details.search_player_sessions(db_path)

    assert detail_result.detail is not None
    forbidden_fields = {
        "open_source_ref",
        "last_seen_source_ref",
        "close_source_ref",
        "last_gameplay_source_ref",
        "server_run_key",
        "play_session_id",
        "rpl_identity",
        "connection_id",
        "session_player_id",
        "be_slot",
        "public_player_id",
        "raw_log_line",
        "raw_rcon",
    }
    assert forbidden_fields.isdisjoint(asdict(detail_result.detail))
    assert forbidden_fields.isdisjoint(asdict(search_result.items[0]))

    rendered = " ".join(
        (
            repr(read_result),
            repr(query_result),
            repr(detail_result),
            repr(search_result),
        )
    )
    for sensitive in (
        "/private/",
        "token=secret",
        "rpl-secret",
        "connection-secret",
        "session-player-secret",
        "boundary:",
    ):
        assert sensitive not in rendered


def test_session_timeline_is_bounded_keyset_safe_and_session_scoped(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    session = _open_session(db_path)
    events = [
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:01:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            victim_name="Bravo",
            instigator_id=PLAYER_ALPHA_ID,
            instigator_name="Alpha",
            raw_source_ref="/private/one.log:1?token=secret",
        ),
        _event(
            player_log_events.EVENT_TYPE_FACTION_JOIN,
            "2026-07-10T12:02:00+00:00",
            player_id=PLAYER_ALPHA_ID,
            player_name="Alpha",
            player_faction="US",
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:03:00+00:00",
            victim_id=PLAYER_ALPHA_ID,
            victim_name="Alpha",
            instigator_id=PLAYER_BRAVO_ID,
            instigator_name="Bravo",
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:04:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_CHARLIE_ID,
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:05:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_ALPHA_ID,
            time_confidence=player_log_events.EVENT_TIME_CONFIDENCE_AMBIGUOUS,
        ),
    ]
    player_registry.ingest_player_log_events(
        db_path,
        events,
        ingested_at="2026-07-10T12:06:00+00:00",
    )
    _record_freshness(db_path, "2026-07-10T12:06:00+00:00")

    page_one = player_session_details.load_player_session_timeline(
        db_path,
        session.session_id,
        limit=2,
        now_at="2026-07-10T12:06:30+00:00",
    )
    assert page_one.status == player_session_details.PLAYER_SESSION_DETAIL_STATUS_OK
    assert [item.occurred_at for item in page_one.items] == [
        "2026-07-10T12:03:00+00:00",
        "2026-07-10T12:02:00+00:00",
    ]
    assert page_one.next_cursor is not None

    page_two = player_session_details.load_player_session_timeline(
        db_path,
        session.session_id,
        limit=2,
        before_time=page_one.next_cursor.event_time,
        before_event_id=page_one.next_cursor.event_id,
        now_at="2026-07-10T12:06:30+00:00",
    )
    assert [item.occurred_at for item in page_two.items] == [
        "2026-07-10T12:01:00+00:00"
    ]
    assert page_two.next_cursor is None

    rendered = repr((page_one, page_two)) + repr(
        tuple(asdict(item) for item in (*page_one.items, *page_two.items))
    )
    for sensitive in (
        PLAYER_ALPHA_ID,
        PLAYER_BRAVO_ID,
        PLAYER_CHARLIE_ID,
        "/private/",
        "token=secret",
        "source_ref",
        "connection_id",
        "rpl_identity",
    ):
        assert sensitive not in rendered


def test_malformed_freshness_timestamp_is_not_forwarded_to_detail_dto(
    tmp_path: Path,
):
    db_path = tmp_path / "default" / "players.db"
    session = _open_session(db_path)
    _record_freshness(db_path, "2026-07-10T12:01:00+00:00")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE player_log_ingest_freshness SET last_success_at = ?",
            ("not-a-time token=secret",),
        )

    result = player_session_details.load_player_session_detail(
        db_path,
        session.session_id,
        now_at="2026-07-10T12:01:30+00:00",
    )
    assert result.detail is not None
    assert result.detail.stats.stats_available is False
    assert result.detail.stats.covered_through is None
    assert "token=secret" not in repr(result)
