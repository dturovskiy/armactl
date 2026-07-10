"""Tests for session-scoped current-player stats enrichment."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from armactl import player_log_events
from armactl.web.services import player_current_enrichment, player_registry

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def _event(event_type: str, occurred_at: str, **kwargs) -> player_log_events.PlayerLogEvent:
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
        raw_source_ref=kwargs.pop(
            "raw_source_ref",
            f"/private/server.log:{occurred_at} token=do-not-render",
        ),
        **kwargs,
    )


def _open_session(db_path: Path, opened_at: str = "2026-07-10T12:00:00+00:00"):
    return player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at=opened_at,
    )


def _record_fresh_ingest(db_path: Path, covered_through: str) -> None:
    scope = player_current_enrichment.CURRENT_STATS_INGEST_SCOPE
    player_registry.upsert_player_log_ingest_checkpoints(
        db_path,
        [
            player_registry.PlayerLogIngestCheckpoint(
                scope=scope,
                source_key="fixture-console-log",
                source_label="allowlisted console log",
                size_bytes=128,
                mtime_ns=1,
                fingerprint="fixture-fingerprint",
                status="scanned",
                last_scanned_at=covered_through,
                updated_at=covered_through,
            )
        ],
    )
    player_registry.record_player_log_ingest_freshness(
        db_path,
        scope=scope,
        status=player_registry.PLAYER_LOG_INGEST_STATUS_FRESH,
        last_run_at=covered_through,
        scanned_files=1,
        parsed_events=0,
        stored_events=0,
        skipped_files=0,
        checkpoint_updated=True,
    )


def _load(
    tmp_path: Path,
    *,
    now_at: str = "2026-07-10T12:15:30+00:00",
):
    return player_current_enrichment.load_current_player_enrichment(
        data_root=tmp_path,
        reliable_ids=[PLAYER_ALPHA_ID],
        now_at=now_at,
    )[PLAYER_ALPHA_ID]


def test_current_stats_aggregate_only_stable_events_inside_fresh_play_session(
    tmp_path: Path,
):
    db_path = tmp_path / "default" / "players.db"
    _open_session(db_path)
    events = [
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T11:59:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_ALPHA_ID,
            instigator_faction="US",
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:05:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            victim_faction="USSR",
            instigator_id=PLAYER_ALPHA_ID,
            instigator_faction="US",
        ),
        _event(
            player_log_events.EVENT_TYPE_TEAMKILL,
            "2026-07-10T12:06:00+00:00",
            victim_id=PLAYER_CHARLIE_ID,
            victim_faction="US",
            instigator_id=PLAYER_ALPHA_ID,
            instigator_faction="US",
            teamkill=True,
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:07:00+00:00",
            victim_id=PLAYER_ALPHA_ID,
            victim_faction="US",
            instigator_id=PLAYER_BRAVO_ID,
            instigator_faction="USSR",
        ),
        _event(
            player_log_events.EVENT_TYPE_SUICIDE,
            "2026-07-10T12:08:00+00:00",
            victim_id=PLAYER_ALPHA_ID,
            victim_faction="US",
            suicide=True,
        ),
        _event(
            player_log_events.EVENT_TYPE_OTHER_DEATH,
            "2026-07-10T12:09:00+00:00",
            victim_id=PLAYER_ALPHA_ID,
            victim_faction="US",
        ),
        _event(
            player_log_events.EVENT_TYPE_TEAMKILL,
            "2026-07-10T12:10:00+00:00",
            victim_id=PLAYER_ALPHA_ID,
            victim_faction="US",
            instigator_id=PLAYER_BRAVO_ID,
            instigator_faction="US",
            teamkill=True,
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:11:00+00:00",
            victim_id=PLAYER_CHARLIE_ID,
            instigator_id=PLAYER_ALPHA_ID,
            instigator_faction="US",
            ai_instigator=True,
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:12:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_ALPHA_ID,
            time_confidence=player_log_events.EVENT_TIME_CONFIDENCE_AMBIGUOUS,
        ),
        _event(
            player_log_events.EVENT_TYPE_FACTION_JOIN,
            "2026-07-10T12:14:00+00:00",
            player_id=PLAYER_ALPHA_ID,
            player_faction="US_Army",
        ),
        _event(
            player_log_events.EVENT_TYPE_KILL,
            "2026-07-10T12:16:00+00:00",
            victim_id=PLAYER_BRAVO_ID,
            instigator_id=PLAYER_ALPHA_ID,
        ),
    ]
    player_registry.ingest_player_log_events(
        db_path,
        events,
        ingested_at="2026-07-10T12:15:00+00:00",
    )
    _record_fresh_ingest(db_path, "2026-07-10T12:15:00+00:00")

    enrichment = _load(tmp_path)

    assert enrichment.stats_available is True
    assert enrichment.kills == 1
    assert enrichment.teamkills == 1
    assert enrichment.deaths == 4
    assert enrichment.faction == "US_Army"
    assert enrichment.stats_window_started_at == "2026-07-10T12:00:00+00:00"
    assert enrichment.stats_window_ended_at == "2026-07-10T12:15:00+00:00"
    assert enrichment.stats_freshness_status == "fresh"
    assert enrichment.stats_source_label == (
        player_current_enrichment.CURRENT_STATS_SOURCE_LABEL
    )


def test_fresh_proven_session_may_show_true_zeroes(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    _open_session(db_path)
    _record_fresh_ingest(db_path, "2026-07-10T12:02:00+00:00")

    enrichment = _load(tmp_path, now_at="2026-07-10T12:02:30+00:00")

    assert enrichment.stats_available is True
    assert enrichment.kills == 0
    assert enrichment.deaths == 0
    assert enrichment.teamkills == 0
    assert enrichment.faction is None


def test_reconnect_within_grace_keeps_original_stats_window(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened = _open_session(db_path)
    assert opened.session is not None
    player_registry.ingest_player_log_events(
        db_path,
        [
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:03:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:09:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
        ],
        ingested_at="2026-07-10T12:10:00+00:00",
    )
    player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-07-10T12:05:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_DISCONNECT,
    )
    reconnected = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-07-10T12:08:00+00:00",
        reconnect_grace_seconds=600,
    )
    assert reconnected.reconnected is True
    assert reconnected.session is not None
    assert reconnected.session.play_session_id == opened.session.play_session_id
    _record_fresh_ingest(db_path, "2026-07-10T12:10:00+00:00")

    enrichment = _load(tmp_path, now_at="2026-07-10T12:10:30+00:00")

    assert enrichment.stats_available is True
    assert enrichment.kills == 2
    assert enrichment.stats_reconnect_merged is True
    assert enrichment.stats_window_started_at == "2026-07-10T12:00:00+00:00"


def test_reconnect_after_grace_starts_new_stats_window(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    first = _open_session(db_path)
    assert first.session is not None
    player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-07-10T12:05:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_DISCONNECT,
    )
    second = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-07-10T12:20:01+00:00",
        reconnect_grace_seconds=600,
    )
    assert second.created is True
    assert second.session is not None
    assert second.session.play_session_id != first.session.play_session_id
    player_registry.ingest_player_log_events(
        db_path,
        [
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:03:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:21:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
        ],
        ingested_at="2026-07-10T12:22:00+00:00",
    )
    _record_fresh_ingest(db_path, "2026-07-10T12:22:00+00:00")

    enrichment = _load(tmp_path, now_at="2026-07-10T12:22:30+00:00")

    assert enrichment.stats_available is True
    assert enrichment.kills == 1
    assert enrichment.stats_reconnect_merged is False
    assert enrichment.stats_window_started_at == "2026-07-10T12:20:01+00:00"


def test_lifecycle_boundary_prevents_stats_carryover(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    first = _open_session(db_path)
    assert first.session is not None
    player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-07-10T12:05:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
    )
    second = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-07-10T12:06:00+00:00",
    )
    assert second.session is not None
    assert second.session.play_session_id != first.session.play_session_id
    player_registry.ingest_player_log_events(
        db_path,
        [
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:03:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
            _event(
                player_log_events.EVENT_TYPE_KILL,
                "2026-07-10T12:07:00+00:00",
                victim_id=PLAYER_BRAVO_ID,
                instigator_id=PLAYER_ALPHA_ID,
            ),
        ],
        ingested_at="2026-07-10T12:08:00+00:00",
    )
    _record_fresh_ingest(db_path, "2026-07-10T12:08:00+00:00")

    enrichment = _load(tmp_path, now_at="2026-07-10T12:08:30+00:00")

    assert enrichment.stats_available is True
    assert enrichment.kills == 1
    assert enrichment.stats_window_started_at == "2026-07-10T12:06:00+00:00"


def test_stale_or_missing_proof_never_returns_fake_zeroes(tmp_path: Path):
    missing = player_current_enrichment.load_current_player_enrichment(
        data_root=tmp_path,
        reliable_ids=[PLAYER_ALPHA_ID],
        now_at="2026-07-10T12:00:00+00:00",
    )[PLAYER_ALPHA_ID]
    assert missing.stats_available is False
    assert missing.kills is None
    assert missing.deaths is None
    assert missing.teamkills is None
    assert missing.stats_unavailable_reason == (
        player_current_enrichment.CURRENT_STATS_NO_DATABASE_REASON
    )
    assert player_current_enrichment.load_current_player_enrichment(
        data_root=tmp_path,
        reliable_ids=[""],
        now_at="2026-07-10T12:00:00+00:00",
    ) == {}

    db_path = tmp_path / "default" / "players.db"
    _open_session(db_path)
    _record_fresh_ingest(db_path, "2026-07-10T12:01:00+00:00")
    stale = _load(tmp_path, now_at="2026-07-10T12:07:00+00:00")
    assert stale.stats_available is False
    assert stale.kills is None
    assert stale.deaths is None
    assert stale.teamkills is None
    assert stale.stats_freshness_status == "stale"
    assert stale.stats_unavailable_reason == (
        player_current_enrichment.CURRENT_STATS_FRESHNESS_STALE_REASON
    )


def test_no_session_or_checkpoint_stays_unavailable(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    player_registry.ensure_player_registry_db(db_path)
    _record_fresh_ingest(db_path, "2026-07-10T12:00:00+00:00")
    no_session = _load(tmp_path, now_at="2026-07-10T12:00:30+00:00")
    assert no_session.stats_available is False
    assert no_session.stats_unavailable_reason == (
        player_current_enrichment.CURRENT_STATS_NO_SESSION_REASON
    )

    _open_session(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM player_log_ingest_checkpoints")
    no_checkpoint = _load(tmp_path, now_at="2026-07-10T12:00:30+00:00")
    assert no_checkpoint.stats_available is False
    assert no_checkpoint.stats_unavailable_reason == (
        player_current_enrichment.CURRENT_STATS_CHECKPOINT_UNAVAILABLE_REASON
    )
