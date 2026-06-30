"""Tests for stored player-log sessionization foundation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from armactl import player_log_events
from armactl.web.services.player_sources import CurrentPlayer, CurrentPlayerRoster

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"


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


def _roster(*players: CurrentPlayer) -> CurrentPlayerRoster:
    return CurrentPlayerRoster(
        available=True,
        players=tuple(players),
        total_count=len(players),
        source="rcon.roster",
        status="available",
        error="",
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
