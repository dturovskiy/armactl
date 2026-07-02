"""Tests for stored player-log sessionization foundation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from armactl import player_log_events
from armactl.web.services.player_sources import CurrentPlayer, CurrentPlayerRoster

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def _parse_log_event(line: str, **kwargs):
    event = player_log_events.parse_player_log_event(line, **kwargs)
    assert event is not None
    return event


def _session_rows(db_path: Path) -> list[dict[str, object]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM player_sessions ORDER BY session_id"
        ).fetchall()
    return [dict(row) for row in rows]


def _session_count(db_path: Path) -> int:
    return len(_session_rows(db_path))


def _player_rows(db_path: Path) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM players ORDER BY reliable_id"
        ).fetchall()
    return [dict(row) for row in rows]


def _table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _table_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _audit_events(data_root: Path) -> list[dict[str, object]]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    if not audit_path.exists():
        return []
    return [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]


def _current_player(
    name: str = "Alpha",
    reliable_id: str = PLAYER_ALPHA_ID,
) -> CurrentPlayer:
    return CurrentPlayer(
        display_name=name,
        reliable_id=reliable_id,
        admin_reference=reliable_id,
        source="rcon.guid",
    )


def _unreliable_current_player(name: str = "Slot Only") -> CurrentPlayer:
    return CurrentPlayer(
        display_name=name,
        reliable_id="",
        admin_reference="",
        source="rcon.roster",
    )


def _roster(*players: CurrentPlayer) -> CurrentPlayerRoster:
    return CurrentPlayerRoster(
        available=True,
        players=tuple(players),
        total_count=len(players),
        source="rcon.roster",
        status="available",
        error="",
        observed_count=len(players),
        count_source="rcon",
        roster_available=True,
        roster_configured=True,
    )


def _count_only_roster(count: int = 7) -> CurrentPlayerRoster:
    return CurrentPlayerRoster(
        available=True,
        players=(),
        total_count=count,
        source="a2s",
        status="available",
        error="",
        observed_count=count,
        count_source="a2s",
        roster_available=False,
        roster_configured=True,
    )


def test_sessionizer_opens_from_stored_auth_and_update_events(tmp_path: Path):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    events = [
        _parse_log_event(
            "BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
            observed_at="2026-06-16T12:00:00+00:00",
            raw_source_ref="journal:auth:1",
        ),
        _parse_log_event(
            "NETWORK : ### Updating player: "
            f"PlayerId=7, Name=Alpha Later, rplIdentity=42, "
            f"IdentityId={PLAYER_ALPHA_ID}",
            observed_at="2026-06-16T12:05:00+00:00",
            raw_source_ref="journal:update:2",
        ),
    ]
    player_registry.ingest_player_log_events(db_path, events)

    summary = player_sessionizer.sessionize_stored_player_log_events(db_path)
    session = player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID)

    assert summary.events_scanned == 2
    assert summary.observations_applied == 2
    assert summary.sessions_created == 1
    assert summary.sessions_updated == 1
    assert session is not None
    assert session.name_at_open == "Alpha One"
    assert session.name_last == "Alpha Later"
    assert session.open_source == player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH
    assert session.open_confidence == player_registry.PLAYER_SESSION_CONFIDENCE_HIGH
    assert session.last_seen_source == (
        player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE
    )
    assert session.last_seen_confidence == player_registry.PLAYER_SESSION_CONFIDENCE_HIGH
    assert session.rpl_identity == "42"
    assert session.session_player_id == "7"
    assert session.scanner_checkpoint_source == (
        player_sessionizer.SESSIONIZATION_CHECKPOINT_SOURCE
    )
    assert session.scanner_checkpoint_ref == "event:2"
    assert _session_count(db_path) == 1


def test_sessionizer_closes_by_reliable_rpl_identity_and_is_idempotent(
    tmp_path: Path,
):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    events = [
        _parse_log_event(
            "BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
            observed_at="2026-06-16T12:00:00+00:00",
            raw_source_ref="journal:auth:1",
        ),
        _parse_log_event(
            "RPL : ServerImpl event: disconnected (identity=42), "
            "group=5, reason=timeout",
            observed_at="2026-06-16T12:10:00+00:00",
            raw_source_ref="journal:rpl-disconnect:2",
        ),
    ]
    player_registry.ingest_player_log_events(db_path, events)

    first = player_sessionizer.sessionize_stored_player_log_events(db_path)
    rows_after_first = _session_rows(db_path)
    second = player_sessionizer.sessionize_stored_player_log_events(db_path)
    rows_after_second = _session_rows(db_path)

    assert first.events_scanned == 2
    assert first.observations_applied == 2
    assert first.sessions_created == 1
    assert first.sessions_closed == 1
    assert second.observations_applied == 0
    assert second.sessions_created == 0
    assert second.sessions_closed == 0
    assert rows_after_second == rows_after_first
    assert _session_count(db_path) == 1
    row = rows_after_first[0]
    assert row["status"] == player_registry.PLAYER_SESSION_STATUS_CLOSED
    assert row["close_observed_at"] == "2026-06-16T12:10:00+00:00"
    assert row["close_source"] == player_log_events.SOURCE_RPL_DISCONNECT
    assert row["close_source_ref"] == "journal:rpl-disconnect:2"
    assert row["close_confidence"] == player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM
    assert row["end_reason"] == player_registry.PLAYER_SESSION_END_REASON_DISCONNECT
    assert player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID) is None


def test_sessionizer_closes_by_connection_id_only_when_unambiguous(
    tmp_path: Path,
):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
        observed_at="2026-06-16T12:00:00+00:00",
        connection_id="conn-7",
    )
    event = _parse_log_event(
        "NETWORK : Player disconnected: connectionID=conn-7",
        observed_at="2026-06-16T12:10:00+00:00",
        raw_source_ref="journal:network-disconnect:3",
    )
    player_registry.ingest_player_log_events(db_path, [event])

    summary = player_sessionizer.sessionize_stored_player_log_events(db_path)
    row = _session_rows(db_path)[0]

    assert summary.sessions_closed == 1
    assert row["status"] == player_registry.PLAYER_SESSION_STATUS_CLOSED
    assert row["close_source"] == player_log_events.SOURCE_NETWORK_DISCONNECT
    assert row["end_reason"] == player_registry.PLAYER_SESSION_END_REASON_DISCONNECT
    assert player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID) is None


def test_sessionizer_does_not_close_name_slot_disconnect_without_stored_correlation(
    tmp_path: Path,
):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T12:00:00+00:00",
    )
    event = _parse_log_event(
        "DEFAULT : BattlEye Server: 'Player #7 Alpha One disconnected'",
        observed_at="2026-06-16T12:10:00+00:00",
        raw_source_ref="journal:be-disconnect:4",
    )
    player_registry.ingest_player_log_events(db_path, [event])

    summary = player_sessionizer.sessionize_stored_player_log_events(db_path)

    assert summary.sessions_closed == 0
    assert summary.observations_skipped == 1
    assert player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID) is not None
    assert _session_rows(db_path)[0]["status"] == player_registry.PLAYER_SESSION_STATUS_OPEN


def test_sessionizer_does_not_close_ambiguous_slot_disconnect(tmp_path: Path):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    for reliable_id, name in (
        (PLAYER_ALPHA_ID, "Alpha One"),
        (PLAYER_BRAVO_ID, "Bravo Two"),
    ):
        player_registry.observe_player_session(
            db_path,
            reliable_id=reliable_id,
            display_name=name,
            source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
            observed_at="2026-06-16T12:00:00+00:00",
            be_slot="7",
        )
    event = _parse_log_event(
        "DEFAULT : BattlEye Server: 'Player #7 Alpha One disconnected'",
        observed_at="2026-06-16T12:10:00+00:00",
        raw_source_ref="journal:be-disconnect:5",
    )
    player_registry.ingest_player_log_events(db_path, [event])

    summary = player_sessionizer.sessionize_stored_player_log_events(db_path)
    rows = _session_rows(db_path)

    assert summary.sessions_closed == 0
    assert summary.observations_skipped == 1
    assert {row["status"] for row in rows} == {player_registry.PLAYER_SESSION_STATUS_OPEN}


def test_sessionizer_lifecycle_marker_closes_open_sessions_as_server_boundary(
    tmp_path: Path,
):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    for reliable_id, name in (
        (PLAYER_ALPHA_ID, "Alpha One"),
        (PLAYER_BRAVO_ID, "Bravo Two"),
    ):
        player_registry.observe_player_session(
            db_path,
            reliable_id=reliable_id,
            display_name=name,
            source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
            observed_at="2026-06-16T12:00:00+00:00",
        )
    event = _parse_log_event(
        "DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started.",
        observed_at="2026-06-16T12:30:00+00:00",
        raw_source_ref="journal:shutdown:6",
    )
    player_registry.ingest_player_log_events(db_path, [event])

    first = player_sessionizer.sessionize_stored_player_log_events(db_path)
    rows_after_first = _session_rows(db_path)
    second = player_sessionizer.sessionize_stored_player_log_events(db_path)

    assert first.sessions_closed == 2
    assert first.observations_applied == 1
    assert second.sessions_closed == 0
    assert _session_rows(db_path) == rows_after_first
    assert {row["status"] for row in rows_after_first} == {
        player_registry.PLAYER_SESSION_STATUS_CLOSED
    }
    assert {row["end_reason"] for row in rows_after_first} == {
        player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY
    }
    assert {row["close_source"] for row in rows_after_first} == {
        player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE
    }


def test_faction_join_opens_inferred_presence_without_duplicate_rows(
    tmp_path: Path,
):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    event = _parse_log_event(
        "SCRIPT : INFO: Faction: player Alpha One "
        f"(playerID = player-7 | UUID = {PLAYER_ALPHA_ID}) "
        "has joined faction #US_Army (US)",
        observed_at="2026-06-16T12:03:00+00:00",
        raw_source_ref="journal:faction:3",
    )
    player_registry.ingest_player_log_events(db_path, [event])

    first = player_sessionizer.sessionize_stored_player_log_events(db_path)
    rows_after_first = _session_rows(db_path)
    second = player_sessionizer.sessionize_stored_player_log_events(db_path)
    session = player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID)

    assert first.sessions_created == 1
    assert first.sessions_updated == 0
    assert second.sessions_created == 0
    assert second.sessions_updated == 0
    assert second.observations_skipped == 1
    assert _session_rows(db_path) == rows_after_first
    assert _session_count(db_path) == 1
    assert session is not None
    assert session.open_source == player_registry.PLAYER_SESSION_SOURCE_SCRIPT_FACTION_JOIN
    assert session.open_confidence == player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM
    assert session.session_player_id == "player-7"
    assert session.faction == "US_Army"
    assert session.side == "US"


def test_repeated_sessionizer_run_is_idempotent_enough(tmp_path: Path):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    events = [
        _parse_log_event(
            "BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
            observed_at="2026-06-16T12:00:00+00:00",
            raw_source_ref="journal:auth:1",
        ),
        _parse_log_event(
            "SCRIPT : INFO: Faction: player Alpha One "
            f"(playerID = player-7 | UUID = {PLAYER_ALPHA_ID}) "
            "has joined faction #US_Army (US)",
            observed_at="2026-06-16T12:03:00+00:00",
            raw_source_ref="journal:faction:2",
        ),
    ]
    player_registry.ingest_player_log_events(db_path, events)

    first = player_sessionizer.sessionize_stored_player_log_events(db_path)
    rows_after_first = _session_rows(db_path)
    players_after_first = _player_rows(db_path)
    second = player_sessionizer.sessionize_stored_player_log_events(db_path)

    assert first.sessions_created == 1
    assert first.sessions_updated == 1
    assert second.observations_applied == 0
    assert second.sessions_created == 0
    assert second.sessions_updated == 0
    assert second.observations_skipped == 2
    assert _session_rows(db_path) == rows_after_first
    assert _player_rows(db_path) == players_after_first


def test_sessionizer_ignores_unreliable_missing_and_count_only_events(
    tmp_path: Path,
):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    events = [
        player_log_events.PlayerLogEvent(
            event_type=player_log_events.EVENT_TYPE_PLAYER_AUTHENTICATED,
            source=player_log_events.SOURCE_BACKEND_AUTH,
            confidence=player_log_events.CONFIDENCE_HIGH,
            observed_at="2026-06-16T12:00:00+00:00",
            player_id="slot-7",
            player_name="Slot Only",
            raw_source_ref="journal:slot:1",
        ),
        player_log_events.PlayerLogEvent(
            event_type="player_count_sample",
            source="a2s.player_count",
            confidence="low",
            observed_at="2026-06-16T12:01:00+00:00",
            player_id=PLAYER_ALPHA_ID,
            player_name="Count Should Not Open",
            raw_source_ref="a2s:count:2",
        ),
    ]
    player_registry.ingest_player_log_events(db_path, events)

    summary = player_sessionizer.sessionize_stored_player_log_events(db_path)

    assert summary.events_scanned == 2
    assert summary.events_ignored == 1
    assert summary.observations_ignored == 1
    assert summary.sessions_created == 0
    assert summary.sessions_updated == 0
    assert _session_count(db_path) == 0


def test_combat_events_are_presence_only_without_stat_claims(tmp_path: Path):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    event = _parse_log_event(
        "SCRIPT : INFO: KILL ENEMY: Alpha One "
        f"(playerID = player-7 | UUID = {PLAYER_ALPHA_ID}) "
        "from US faction at <0 0 0> was killed by Bravo Two "
        f"(playerID = player-8 | UUID = {PLAYER_BRAVO_ID}) "
        "from USSR faction. With last inflicted damage type Bullet "
        "to the 'Head' hit zone",
        observed_at="2026-06-16T12:04:00+00:00",
        raw_source_ref="journal:combat:4",
    )
    player_registry.ingest_player_log_events(db_path, [event])

    summary = player_sessionizer.sessionize_stored_player_log_events(db_path)
    rows = _session_rows(db_path)

    assert summary.sessions_created == 2
    assert summary.sessions_updated == 0
    assert {row["reliable_id"] for row in rows} == {PLAYER_ALPHA_ID, PLAYER_BRAVO_ID}
    assert {row["open_confidence"] for row in rows} == {
        player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM
    }
    encoded_rows = json.dumps(rows, sort_keys=True)
    for stat_claim in ("kill_count", "death_count", "kd", "teamkill_count"):
        assert stat_claim not in encoded_rows.casefold()


def test_current_roster_refresh_does_not_create_sessions(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Live Alpha", PLAYER_ALPHA_ID)),
    )

    result = player_actions.refresh_current_players("default", data_root=tmp_path)

    assert result.success is True
    assert result.stored_count == 1
    assert _session_count(tmp_path / "default" / "players.db") == 0


def test_current_roster_refresh_does_not_close_existing_sessions(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_registry, player_sources

    db_path = tmp_path / "default" / "players.db"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T12:00:00+00:00",
    )
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(),
    )

    result = player_actions.refresh_current_players("default", data_root=tmp_path)
    session = player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID)

    assert result.success is True
    assert result.stored_count == 0
    assert result.ignored_count == 0
    assert session is not None
    assert session.status == player_registry.PLAYER_SESSION_STATUS_OPEN
    assert _session_count(db_path) == 1


def test_live_session_scanner_reliable_roster_opens_and_updates_session(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import (
        player_live_session_scanner,
        player_registry,
        player_sources,
    )

    rosters = iter(
        (
            _roster(_current_player("Live Alpha", PLAYER_ALPHA_ID)),
            _roster(_current_player("Live Alpha Later", PLAYER_ALPHA_ID)),
        )
    )
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: next(rosters),
    )
    db_path = tmp_path / "default" / "players.db"

    first = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-16T12:00:00+00:00",
    )
    second = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-16T12:05:00+00:00",
    )
    session = player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID)

    assert first.success is True
    assert first.observed_count == 1
    assert first.reliable_rows_seen == 1
    assert first.sessions_created == 1
    assert first.sessions_updated == 0
    assert second.success is True
    assert second.sessions_created == 0
    assert second.sessions_updated == 1
    assert _session_count(db_path) == 1
    assert session is not None
    assert session.name_at_open == "Live Alpha"
    assert session.name_last == "Live Alpha Later"
    assert session.open_source == player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER
    assert session.last_seen_source == player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER
    assert session.open_confidence == player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM
    assert session.last_seen_confidence == player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM
    assert session.last_seen_at == "2026-06-16T12:05:00+00:00"
    assert session.scanner_checkpoint_source == (
        player_live_session_scanner.LIVE_SESSION_SCANNER_CHECKPOINT_SOURCE
    )
    assert session.scanner_checkpoint_ref == (
        player_live_session_scanner.LIVE_SESSION_SCANNER_SOURCE_REF
    )
    assert session.scanner_checkpoint_at == "2026-06-16T12:05:00+00:00"


def test_live_session_scanner_ignores_unreliable_roster_rows(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_live_session_scanner, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_unreliable_current_player("Slot Only")),
    )

    summary = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
    )

    assert summary.success is True
    assert summary.roster_rows_seen == 1
    assert summary.reliable_rows_seen == 0
    assert summary.unreliable_rows_ignored == 1
    assert summary.sessions_created == 0
    assert not (tmp_path / "default" / "players.db").exists()


def test_live_session_scanner_does_not_create_sessions_from_count_only_a2s(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_live_session_scanner, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _count_only_roster(7),
    )

    summary = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
    )

    assert summary.success is True
    assert summary.observed_count == 7
    assert summary.roster_rows_seen == 0
    assert summary.reliable_rows_seen == 0
    assert summary.sessions_created == 0
    assert _session_count(tmp_path / "default" / "players.db") == 0
    assert not (tmp_path / "default" / "players.db").exists()


def test_live_session_scanner_source_failure_does_not_close_sessions(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import (
        player_live_session_scanner,
        player_registry,
        player_sources,
    )

    db_path = tmp_path / "default" / "players.db"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T12:00:00+00:00",
    )

    def fail_roster(instance):
        raise RuntimeError(
            "failed token=raw-roster-secret from 198.51.100.9 "
            "using /home/deus/private.log"
        )

    monkeypatch.setattr(player_sources, "load_current_player_roster", fail_roster)

    summary = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
    )
    session = player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID)

    assert summary.success is False
    assert summary.source_failures == 1
    assert summary.sessions_created == 0
    assert summary.sessions_updated == 0
    assert session is not None
    assert session.status == player_registry.PLAYER_SESSION_STATUS_OPEN
    assert _session_count(db_path) == 1


def test_live_session_scanner_sanitizes_forbidden_values_and_columns(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_live_session_scanner, player_sources

    raw_path = "/home/deus/armactl-data/default/config/logs/run/console.log"
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: CurrentPlayerRoster(
            available=True,
            players=(
                CurrentPlayer(
                    display_name="Alpha token=raw-player-secret 198.51.100.9",
                    reliable_id=PLAYER_ALPHA_ID,
                    admin_reference=PLAYER_ALPHA_ID,
                    source=f"{raw_path} token=raw-source-secret",
                ),
            ),
            total_count=1,
            source=f"rcon.roster {raw_path}",
            status="available",
            error="",
            observed_count=1,
            count_source="rcon",
            roster_available=True,
            roster_configured=True,
        ),
    )

    summary = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-16T12:00:00+00:00",
    )
    db_path = tmp_path / "default" / "players.db"
    encoded_sessions = json.dumps(_session_rows(db_path), sort_keys=True)

    assert summary.sessions_created == 1
    assert "Alpha token=*** ***" in encoded_sessions
    assert not {"ip", "address", "raw_line", "raw_path"} & _table_columns(
        db_path,
        "player_sessions",
    )
    for forbidden in (
        raw_path,
        "/home/deus",
        "armactl-data",
        "raw-player-secret",
        "raw-source-secret",
        "198.51.100.9",
    ):
        assert forbidden not in encoded_sessions


def test_live_session_scan_job_dedupes_and_audits_counts_only(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import get_job, list_active_jobs, player_sessions
    from armactl.web.services import (
        player_live_session_scan,
        player_sources,
    )

    db_path = tmp_path / "web" / "web.db"
    raw_path = "/home/deus/armactl-data/default/config/logs/run/console.log"
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: CurrentPlayerRoster(
            available=True,
            players=(
                CurrentPlayer(
                    display_name="Alpha token=raw-player-secret 198.51.100.9",
                    reliable_id=PLAYER_ALPHA_ID,
                    admin_reference=PLAYER_ALPHA_ID,
                    source=f"{raw_path} token=raw-source-secret",
                ),
            ),
            total_count=1,
            source=f"rcon.roster {raw_path}",
            status="available",
            error="",
            observed_count=1,
            count_source="rcon",
            roster_available=True,
            roster_configured=True,
        ),
    )
    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_sessions,
        "start_player_live_session_scan_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )

    first = player_live_session_scan.request_player_live_session_scan_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )
    second = player_live_session_scan.request_player_live_session_scan_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )

    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    assert started_jobs == [first.job.id]
    assert [job.id for job in list_active_jobs(db_path)] == [first.job.id]

    dispatch = player_sessions.dispatch_player_live_session_scan_job(
        db_path,
        first.job.id,
    )
    job = get_job(db_path, first.job.id)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(
        encoding="utf-8"
    )
    events = _audit_events(tmp_path)

    assert dispatch.ran is True
    assert job is not None
    assert job.status == "succeeded"
    assert "observed_count=1" in job.stdout_tail
    assert "reliable_rows_seen=1" in job.stdout_tail
    assert "sessions_created=1" in job.stdout_tail
    assert [event["details"]["phase"] for event in events] == [
        "intent",
        "intent",
        "outcome",
    ]
    outcome = events[-1]
    assert outcome["action"] == player_sessions.PLAYER_LIVE_SESSION_SCAN_ACTION
    assert outcome["target"] == player_sessions.PLAYER_LIVE_SESSION_SCAN_JOB_KIND
    assert outcome["details"]["sessions_created"] == "1"
    assert outcome["details"]["source_failures"] == "0"
    for rendered in (job.stdout_tail, audit_text):
        assert "Alpha" not in rendered
        assert PLAYER_ALPHA_ID not in rendered
        assert raw_path not in rendered
        assert "raw-player-secret" not in rendered
        assert "raw-source-secret" not in rendered
        assert "198.51.100.9" not in rendered
        for forbidden_key in ("ip", "address", "raw_line", "raw_path"):
            assert forbidden_key not in rendered.casefold()


def test_sessionization_job_dedupes_and_audits_counts_only(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import get_job, list_active_jobs, player_sessions
    from armactl.web.services import (
        player_log_sessionization,
        player_registry,
    )

    db_path = tmp_path / "web" / "web.db"
    registry_db_path = tmp_path / "default" / "players.db"
    raw_path = "/home/deus/armactl-data/default/config/logs/run/console.log"
    player_registry.ingest_player_log_events(
        registry_db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} "
                "name=Alpha token=raw-player-secret 198.51.100.9",
                observed_at="2026-06-16T12:00:00+00:00",
                raw_source_ref=f"{raw_path}:198.51.100.8:1",
            )
        ],
    )
    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_sessions,
        "start_player_log_sessionization_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )

    first = player_log_sessionization.request_player_log_sessionization_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )
    second = player_log_sessionization.request_player_log_sessionization_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )

    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    assert started_jobs == [first.job.id]
    assert [job.id for job in list_active_jobs(db_path)] == [first.job.id]

    dispatch = player_sessions.dispatch_player_log_sessionization_job(
        db_path,
        first.job.id,
    )
    job = get_job(db_path, first.job.id)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(
        encoding="utf-8"
    )
    events = _audit_events(tmp_path)

    assert dispatch.ran is True
    assert job is not None
    assert job.status == "succeeded"
    assert "events_scanned=1" in job.stdout_tail
    assert "sessions_created=1" in job.stdout_tail
    assert [event["details"]["phase"] for event in events] == [
        "intent",
        "intent",
        "outcome",
    ]
    outcome = events[-1]
    assert outcome["action"] == player_sessions.PLAYER_LOG_SESSIONIZATION_ACTION
    assert outcome["target"] == player_sessions.PLAYER_LOG_SESSIONIZATION_JOB_KIND
    assert outcome["details"]["events_scanned"] == "1"
    assert outcome["details"]["sessions_created"] == "1"
    for rendered in (job.stdout_tail, audit_text):
        assert "Alpha" not in rendered
        assert PLAYER_ALPHA_ID not in rendered
        assert raw_path not in rendered
        assert "raw-player-secret" not in rendered
        assert "198.51.100.8" not in rendered
        assert "198.51.100.9" not in rendered


def test_sessionizer_does_not_store_raw_ip_path_or_secret_values(tmp_path: Path):
    from armactl.web.services import player_registry, player_sessionizer

    db_path = tmp_path / "default" / "players.db"
    raw_path = "/home/deus/armactl-data/default/config/logs/run/console.log"
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} "
                "name=Alpha token=raw-player-secret 198.51.100.9",
                observed_at="2026-06-16T12:00:00+00:00",
                raw_source_ref=f"{raw_path}:198.51.100.8:1",
            )
        ],
    )

    player_sessionizer.sessionize_stored_player_log_events(db_path)
    encoded_sessions = json.dumps(_session_rows(db_path), sort_keys=True)

    assert "Alpha token=*** ***" in encoded_sessions
    for forbidden in (
        raw_path,
        "/home/deus",
        "armactl-data",
        "raw-player-secret",
        "198.51.100.8",
        "198.51.100.9",
    ):
        assert forbidden not in encoded_sessions



def test_stale_close_closes_only_overdue_open_sessions_and_sanitizes_evidence(
    tmp_path: Path,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T10:00:00+00:00",
    )
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_BRAVO_ID,
        display_name="Bravo Two",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T09:00:00+00:00",
    )
    player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_BRAVO_ID,
        close_observed_at="2026-06-16T09:30:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
        source_ref="journal:shutdown:before-stale",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_LOW,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
    )
    closed_before = _session_rows(db_path)[1].copy()
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_CHARLIE_ID,
        display_name="Charlie Three",
        source=player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
        observed_at="2026-06-16T12:30:00+00:00",
    )

    result = player_registry.close_stale_open_player_sessions(
        db_path,
        last_seen_before="2026-06-16T12:00:00+00:00",
        close_observed_at="2026-06-16T13:00:00+00:00",
        source="/home/deus/private/scanner.log token=raw-source-secret",
        source_ref=(
            "/home/deus/armactl-data/default/config/logs/run/"
            "console.log:198.51.100.77:token=raw-ref-secret"
        ),
        confidence="not-a-confidence",
    )
    rows = _session_rows(db_path)
    by_id = {str(row["reliable_id"]): row for row in rows}
    encoded_rows = json.dumps(rows, sort_keys=True)

    assert result.open_sessions_scanned == 2
    assert result.sessions_overdue == 1
    assert result.sessions_closed == 1
    assert result.sessions_skipped == 1
    assert by_id[PLAYER_ALPHA_ID]["status"] == player_registry.PLAYER_SESSION_STATUS_CLOSED
    assert by_id[PLAYER_ALPHA_ID]["end_reason"] == (
        player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT
    )
    assert by_id[PLAYER_ALPHA_ID]["close_observed_at"] == "2026-06-16T13:00:00+00:00"
    assert by_id[PLAYER_ALPHA_ID]["close_source"] == "scanner.log token=***"
    assert by_id[PLAYER_ALPHA_ID]["close_source_ref"] == "console.log:***:token=***"
    assert by_id[PLAYER_ALPHA_ID]["close_confidence"] == (
        player_registry.PLAYER_SESSION_CONFIDENCE_LOW
    )
    assert by_id[PLAYER_CHARLIE_ID]["status"] == player_registry.PLAYER_SESSION_STATUS_OPEN
    assert by_id[PLAYER_BRAVO_ID] == closed_before
    assert not {"ip", "address", "raw_line", "raw_path"} & _table_columns(
        db_path,
        "player_sessions",
    )
    for forbidden in (
        "/home/deus",
        "armactl-data",
        "raw-source-secret",
        "raw-ref-secret",
        "198.51.100.77",
    ):
        assert forbidden not in encoded_rows


def test_session_retention_cleanup_preserves_identity_registry_and_events(
    tmp_path: Path,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    for reliable_id, name, opened_at, closed_at in (
        (
            PLAYER_ALPHA_ID,
            "Alpha One",
            "2026-02-28T12:00:00+00:00",
            "2026-03-01T12:00:00+00:00",
        ),
        (
            PLAYER_BRAVO_ID,
            "Bravo Two",
            "2026-06-14T12:00:00+00:00",
            "2026-06-14T12:30:00+00:00",
        ),
    ):
        player_registry.observe_player_session(
            db_path,
            reliable_id=reliable_id,
            display_name=name,
            source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
            observed_at=opened_at,
        )
        player_registry.close_player_session(
            db_path,
            reliable_id=reliable_id,
            close_observed_at=closed_at,
            source=player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
            end_reason=player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
        )
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_CHARLIE_ID,
        display_name="Charlie Three",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-02-28T12:00:00+00:00",
    )
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
                observed_at="2026-02-28T12:00:00+00:00",
                raw_source_ref="journal:auth:retention",
            )
        ],
    )

    result = player_registry.cleanup_player_sessions_by_retention(
        db_path,
        closed_before="2026-06-01T00:00:00+00:00",
    )
    rows = _session_rows(db_path)

    assert result.sessions_scanned == 1
    assert result.sessions_deleted == 1
    assert result.sessions_skipped == 0
    assert {row["reliable_id"] for row in rows} == {PLAYER_BRAVO_ID, PLAYER_CHARLIE_ID}
    by_status = {row["reliable_id"]: row["status"] for row in rows}
    assert by_status[PLAYER_BRAVO_ID] == player_registry.PLAYER_SESSION_STATUS_CLOSED
    assert by_status[PLAYER_CHARLIE_ID] == player_registry.PLAYER_SESSION_STATUS_OPEN
    assert _table_count(db_path, "players") == 3
    assert _table_count(db_path, "player_names") == 3
    assert _table_count(db_path, "player_log_events") == 1


def test_session_maintenance_job_dedupes_and_audits_counts_only(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import get_job, list_active_jobs, player_sessions
    from armactl.web.services import (
        player_registry,
        player_session_maintenance,
    )

    db_path = tmp_path / "web" / "web.db"
    registry_db_path = tmp_path / "default" / "players.db"
    raw_path = "/home/deus/armactl-data/default/config/logs/run/console.log"
    player_registry.observe_player_session(
        registry_db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha token=raw-player-secret 198.51.100.9",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        source_ref=f"{raw_path}:198.51.100.8:1",
        observed_at="2026-06-14T12:00:00+00:00",
    )
    player_registry.observe_player_session(
        registry_db_path,
        reliable_id=PLAYER_BRAVO_ID,
        display_name="Bravo token=raw-player-secret 198.51.100.10",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        source_ref=f"{raw_path}:198.51.100.11:2",
        observed_at="2026-03-01T12:00:00+00:00",
    )
    player_registry.close_player_session(
        registry_db_path,
        reliable_id=PLAYER_BRAVO_ID,
        close_observed_at="2026-03-01T12:30:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
    )
    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_sessions,
        "start_player_session_maintenance_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )
    monkeypatch.setattr(
        player_sessions,
        "_utc_now",
        lambda: datetime(2026, 6, 16, 13, 0, tzinfo=timezone.utc),
    )

    first = player_session_maintenance.request_player_session_maintenance_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )
    second = player_session_maintenance.request_player_session_maintenance_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )

    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    assert started_jobs == [first.job.id]
    assert [job.id for job in list_active_jobs(db_path)] == [first.job.id]

    dispatch = player_sessions.dispatch_player_session_maintenance_job(
        db_path,
        first.job.id,
    )
    job = get_job(db_path, first.job.id)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(
        encoding="utf-8"
    )
    events = _audit_events(tmp_path)
    rows = _session_rows(registry_db_path)

    assert dispatch.ran is True
    assert job is not None
    assert job.status == "succeeded"
    assert "stale_sessions_closed=1" in job.stdout_tail
    assert "retention_sessions_deleted=1" in job.stdout_tail
    assert {row["reliable_id"] for row in rows} == {PLAYER_ALPHA_ID}
    assert rows[0]["end_reason"] == player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT
    assert [event["details"]["phase"] for event in events] == [
        "intent",
        "intent",
        "outcome",
    ]
    outcome = events[-1]
    assert outcome["action"] == player_sessions.PLAYER_SESSION_MAINTENANCE_ACTION
    assert outcome["target"] == player_sessions.PLAYER_SESSION_MAINTENANCE_JOB_KIND
    assert outcome["details"]["stale_sessions_closed"] == "1"
    assert outcome["details"]["retention_sessions_deleted"] == "1"
    for rendered in (job.stdout_tail, audit_text):
        assert "Alpha" not in rendered
        assert "Bravo" not in rendered
        assert PLAYER_ALPHA_ID not in rendered
        assert PLAYER_BRAVO_ID not in rendered
        assert raw_path not in rendered
        assert "raw-player-secret" not in rendered
        assert "198.51.100.8" not in rendered
        assert "198.51.100.9" not in rendered
        assert "198.51.100.10" not in rendered
        assert "198.51.100.11" not in rendered
        for forbidden_key in ("ip", "address", "raw_line", "raw_path"):
            assert forbidden_key not in rendered.casefold()
