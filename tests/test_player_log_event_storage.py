"""Tests for persisted sanitized player log event ingest."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from armactl import player_log_events as events
from armactl.web.services import player_registry

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def _parse(line: str, **kwargs) -> events.PlayerLogEvent:
    event = events.parse_player_log_event(line, **kwargs)
    assert event is not None
    return event


def _sqlite_tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _sqlite_indexes(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index'
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def _schema_version(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT value
            FROM player_registry_schema_meta
            WHERE key = 'schema_version'
            """
        ).fetchone()
    assert row is not None
    return str(row[0])


def _event_rows(db_path: Path) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM player_log_events
            ORDER BY event_id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def test_player_log_event_schema_lives_in_instance_players_db(tmp_path: Path) -> None:
    db_path = player_registry.player_registry_db_path("default", data_root=tmp_path)

    player_registry.ensure_player_registry_db(db_path)

    assert db_path == tmp_path / "default" / "players.db"
    assert db_path.is_file()
    assert _schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert "player_log_events" in _sqlite_tables(db_path)
    assert {
        "event_key",
        "event_type",
        "source",
        "source_ref",
        "confidence",
        "occurred_at",
        "observed_at",
        "log_timestamp",
        "time_source",
        "time_confidence",
        "collected_at",
        "player_id",
        "player_name",
        "rpl_identity",
        "connection_id",
        "be_slot",
        "victim_id",
        "instigator_id",
        "teamkill",
        "suicide",
        "ai_instigator",
        "distance_m",
    }.issubset(_sqlite_columns(db_path, "player_log_events"))
    assert not {
        "raw_line",
        "raw_log_line",
        "ip",
        "ip_address",
        "address",
    } & _sqlite_columns(db_path, "player_log_events")
    assert {
        "idx_player_log_events_occurred_at",
        "idx_player_log_events_observed_at",
        "idx_player_log_events_collected_at",
        "idx_player_log_events_type",
        "idx_player_log_events_player",
        "idx_player_log_events_victim",
        "idx_player_log_events_instigator",
        "idx_player_log_events_history_order",
        "idx_player_log_events_type_history_order",
        "idx_player_log_events_player_history_order",
        "idx_player_log_events_victim_history_order",
        "idx_player_log_events_instigator_history_order",
    }.issubset(_sqlite_indexes(db_path))


def test_ingest_auth_update_and_faction_events(tmp_path: Path) -> None:
    db_path = tmp_path / "default" / "players.db"
    parsed_events = [
        _parse(
            "BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
            observed_at="2026-01-01T12:00:01+00:00",
            raw_source_ref="journal:alpha-auth",
        ),
        _parse(
            "NETWORK : ### Updating player: PlayerId=7, Name=Alpha One, "
            f"rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
            observed_at="2026-01-01T12:00:02+00:00",
            raw_timestamp="12:00:02.000",
            raw_source_ref="journal:alpha-update",
        ),
        _parse(
            "SCRIPT : INFO: Faction: player Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) "
            "has joined faction #US_Army (US)",
            observed_at="2026-01-01T12:00:03+00:00",
            raw_source_ref="journal:alpha-faction",
        ),
    ]

    result = player_registry.ingest_player_log_events(
        db_path,
        parsed_events,
        ingested_at="2026-01-01T12:00:04+00:00",
    )

    rows = _event_rows(db_path)
    assert result.stored_count == 3
    assert result.duplicate_count == 0
    assert [row["event_type"] for row in rows] == [
        events.EVENT_TYPE_PLAYER_AUTHENTICATED,
        events.EVENT_TYPE_PLAYER_UPDATE,
        events.EVENT_TYPE_FACTION_JOIN,
    ]
    assert rows[0]["source"] == events.SOURCE_BACKEND_AUTH
    assert rows[0]["confidence"] == events.CONFIDENCE_HIGH
    assert rows[0]["source_ref"] == "journal:alpha-auth"
    assert rows[0]["player_id"] == PLAYER_ALPHA_ID
    assert rows[0]["player_name"] == "Alpha One"
    assert rows[0]["rpl_identity"] == "42"
    assert rows[0]["occurred_at"] is None
    assert rows[0]["time_source"] == events.EVENT_TIME_SOURCE_CALLER_OBSERVED_AT
    assert rows[0]["time_confidence"] == events.EVENT_TIME_CONFIDENCE_EXACT
    assert rows[0]["collected_at"] == "2026-01-01T12:00:04+00:00"
    assert rows[1]["session_player_id"] == "7"
    assert rows[1]["log_timestamp"] == "12:00:02.000"
    assert rows[2]["player_faction"] == "US"
    assert rows[2]["faction_resource"] == "US_Army"
    known = player_registry.list_known_players(db_path)
    assert [(player.reliable_id, player.current_name) for player in known] == [
        (PLAYER_ALPHA_ID, "Alpha One")
    ]


def test_ingest_disconnect_and_lifecycle_events(tmp_path: Path) -> None:
    db_path = tmp_path / "default" / "players.db"
    parsed_events = [
        _parse(
            "RPL : ServerImpl event: disconnected (identity=42), "
            "group=5, reason=timeout",
            observed_at="2026-01-01T12:10:00+00:00",
            raw_source_ref="/home/deus/logs/console.log:198.51.100.7:10",
        ),
        _parse(
            "NETWORK : Player disconnected: connectionID=conn-7",
            observed_at="2026-01-01T12:11:00+00:00",
            raw_source_ref="journal:network-disconnect",
        ),
        _parse(
            "DEFAULT : BattlEye Server: 'Player #7 Alpha token=raw-secret "
            "203.0.113.9 disconnected'",
            observed_at="2026-01-01T12:12:00+00:00",
            raw_source_ref="journal:be-disconnect",
        ),
        _parse(
            "DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started.",
            observed_at="2026-01-01T12:13:00+00:00",
            raw_source_ref="journal:shutdown",
        ),
    ]

    result = player_registry.ingest_player_log_events(db_path, parsed_events)

    rows = _event_rows(db_path)
    encoded_rows = json.dumps(rows, sort_keys=True)
    assert result.stored_count == 4
    assert [row["event_type"] for row in rows] == [
        events.EVENT_TYPE_PLAYER_DISCONNECTED,
        events.EVENT_TYPE_PLAYER_DISCONNECTED,
        events.EVENT_TYPE_PLAYER_DISCONNECTED,
        events.EVENT_TYPE_SERVER_LIFECYCLE,
    ]
    assert rows[0]["rpl_identity"] == "42"
    assert rows[1]["connection_id"] == "conn-7"
    assert rows[2]["be_slot"] == "7"
    assert rows[2]["player_name"] == "Alpha token=*** ***"
    assert "raw-secret" not in encoded_rows
    assert "203.0.113.9" not in encoded_rows
    assert "/home/deus" not in encoded_rows
    assert "198.51.100.7" not in encoded_rows


def test_disconnect_event_dedupe_keeps_distinct_correlation_without_source_ref(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "default" / "players.db"
    parsed_events = [
        events.PlayerLogEvent(
            event_type=events.EVENT_TYPE_PLAYER_DISCONNECTED,
            source=events.SOURCE_NETWORK_DISCONNECT,
            confidence=events.CONFIDENCE_MEDIUM,
            observed_at="2026-01-01T12:10:00+00:00",
            connection_id="conn-7",
        ),
        events.PlayerLogEvent(
            event_type=events.EVENT_TYPE_PLAYER_DISCONNECTED,
            source=events.SOURCE_NETWORK_DISCONNECT,
            confidence=events.CONFIDENCE_MEDIUM,
            observed_at="2026-01-01T12:10:00+00:00",
            connection_id="conn-8",
        ),
        events.PlayerLogEvent(
            event_type=events.EVENT_TYPE_PLAYER_DISCONNECTED,
            source=events.SOURCE_BATTLEYE_DISCONNECT,
            confidence=events.CONFIDENCE_LOW,
            observed_at="2026-01-01T12:11:00+00:00",
            be_slot="7",
            player_name="Alpha One",
        ),
        events.PlayerLogEvent(
            event_type=events.EVENT_TYPE_PLAYER_DISCONNECTED,
            source=events.SOURCE_BATTLEYE_DISCONNECT,
            confidence=events.CONFIDENCE_LOW,
            observed_at="2026-01-01T12:11:00+00:00",
            be_slot="8",
            player_name="Alpha One",
        ),
    ]

    result = player_registry.ingest_player_log_events(db_path, parsed_events)

    rows = _event_rows(db_path)
    assert result.stored_count == 4
    assert result.duplicate_count == 0
    assert [row["connection_id"] for row in rows[:2]] == ["conn-7", "conn-8"]
    assert [row["be_slot"] for row in rows[2:]] == ["7", "8"]


def test_duplicate_player_log_event_ingest_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "default" / "players.db"
    event = _parse(
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
        observed_at="2026-01-01T12:00:01+00:00",
        raw_source_ref="journal:alpha-auth",
    )

    first = player_registry.ingest_player_log_events(
        db_path,
        [event],
        ingested_at="2026-01-01T12:00:02+00:00",
    )
    second = player_registry.ingest_player_log_events(
        db_path,
        [event],
        ingested_at="2026-01-01T12:05:00+00:00",
    )

    assert first.stored_count == 1
    assert first.duplicate_count == 0
    assert second.stored_count == 0
    assert second.duplicate_count == 1
    assert len(_event_rows(db_path)) == 1
    player = player_registry.get_known_player(db_path, PLAYER_ALPHA_ID)
    assert player is not None
    assert player.seen_count == 1


def test_raw_log_line_and_address_like_values_are_not_stored(tmp_path: Path) -> None:
    db_path = tmp_path / "default" / "players.db"
    raw_line = (
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha 203.0.113.9"
    )
    event = _parse(
        raw_line,
        observed_at="2026-01-01T12:00:01+00:00",
        raw_source_ref="/home/test/armactl-data/default/config/logs/2026/console.log:198.51.100.7:2302",
    )

    result = player_registry.ingest_player_log_events(db_path, [event])

    rows = _event_rows(db_path)
    encoded_row = json.dumps(rows, sort_keys=True)
    assert result.stored_count == 1
    assert raw_line not in encoded_row
    assert "203.0.113.9" not in encoded_row
    assert "198.51.100.7" not in encoded_row
    assert "/home/test" not in encoded_row
    assert "armactl-data" not in encoded_row
    assert rows[0]["player_name"] == "Alpha ***"
    assert rows[0]["source_ref"] == "console.log:***"
    assert not {
        "raw_line",
        "raw_log_line",
        "ip",
        "ip_address",
        "address",
    } & _sqlite_columns(db_path, "player_log_events")


def test_ingest_combat_event_variants(tmp_path: Path) -> None:
    db_path = tmp_path / "default" / "players.db"
    parsed_events = [
        _parse(
            "SCRIPT : INFO: KILL ENEMY: Bravo Two "
            f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from FIA faction "
            "at <10 20 30> was killed by Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
            "who was at that time at <40 50 60> [64.5m away from the corpse]. "
            "With last inflicted damage type Projectile to the 'Head' hit zone",
            raw_source_ref="journal:kill",
        ),
        _parse(
            "SCRIPT : INFO: KILL SUICIDE: Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
            "at <1 2 3> killed himself! "
            "With last inflicted damage type Explosion to the 'Torso' hit zone",
            raw_source_ref="journal:suicide",
        ),
        _parse(
            "SCRIPT : INFO: KILL TK: Bravo Two "
            f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
            "at <4 5 6> was killed by Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
            "who was at that time at <4 5 7> [2.2m away from the corpse]. "
            "With last inflicted damage type Bullet to the 'LeftArm' hit zone",
            raw_source_ref="journal:teamkill",
        ),
        _parse(
            "SCRIPT : INFO: KILL OTHER_DEATH: Charlie Three "
            f"(playerID = 9 | UUID = {PLAYER_CHARLIE_ID}) from FIA faction "
            "at <7 8 9> was killed by AI",
            raw_source_ref="journal:other-death",
        ),
    ]

    result = player_registry.ingest_player_log_events(db_path, parsed_events)

    rows = {row["event_type"]: row for row in _event_rows(db_path)}
    assert result.stored_count == 4
    kill = rows[events.EVENT_TYPE_KILL]
    assert kill["victim_id"] == PLAYER_BRAVO_ID
    assert kill["victim_name"] == "Bravo Two"
    assert kill["victim_session_player_id"] == "8"
    assert kill["victim_faction"] == "FIA"
    assert kill["instigator_id"] == PLAYER_ALPHA_ID
    assert kill["instigator_name"] == "Alpha One"
    assert kill["instigator_session_player_id"] == "7"
    assert kill["instigator_faction"] == "US"
    assert kill["teamkill"] == 0
    assert kill["suicide"] == 0
    assert kill["ai_instigator"] == 0
    assert kill["damage_type"] == "Projectile"
    assert kill["hit_zone"] == "Head"
    assert kill["distance_m"] == 64.5

    suicide = rows[events.EVENT_TYPE_SUICIDE]
    assert suicide["victim_id"] == PLAYER_ALPHA_ID
    assert suicide["instigator_id"] == PLAYER_ALPHA_ID
    assert suicide["victim_faction"] == "US"
    assert suicide["instigator_faction"] == "US"
    assert suicide["teamkill"] == 0
    assert suicide["suicide"] == 1

    teamkill = rows[events.EVENT_TYPE_TEAMKILL]
    assert teamkill["victim_id"] == PLAYER_BRAVO_ID
    assert teamkill["instigator_id"] == PLAYER_ALPHA_ID
    assert teamkill["teamkill"] == 1
    assert teamkill["suicide"] == 0
    assert teamkill["distance_m"] == 2.2

    other_death = rows[events.EVENT_TYPE_OTHER_DEATH]
    assert other_death["victim_id"] == PLAYER_CHARLIE_ID
    assert other_death["victim_name"] == "Charlie Three"
    assert other_death["instigator_id"] is None
    assert other_death["teamkill"] == 0
    assert other_death["suicide"] == 0
    assert other_death["ai_instigator"] is None


def test_list_player_log_events_filters_and_sorts_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "default" / "players.db"
    parsed_events = [
        _parse(
            "BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
            observed_at="2026-01-01T12:00:01+00:00",
            raw_source_ref="journal:alpha-auth",
        ),
        _parse(
            "SCRIPT : INFO: KILL ENEMY: Bravo Two "
            f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from FIA faction "
            "at <10 20 30> was killed by Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
            "who was at that time at <40 50 60> [64.5m away from the corpse]. "
            "With last inflicted damage type Projectile to the 'Head' hit zone",
            observed_at="2026-01-01T12:00:03+00:00",
            raw_source_ref="journal:kill",
        ),
        _parse(
            "SCRIPT : INFO: KILL TK: Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
            "at <4 5 6> was killed by Charlie Three "
            f"(playerID = 9 | UUID = {PLAYER_CHARLIE_ID}) from US faction "
            "who was at that time at <4 5 7> [2.2m away from the corpse]. "
            "With last inflicted damage type Bullet to the 'LeftArm' hit zone",
            observed_at="2026-01-01T12:00:05+00:00",
            raw_source_ref="journal:teamkill",
        ),
    ]
    player_registry.ingest_player_log_events(
        db_path,
        parsed_events,
        ingested_at="2026-01-01T12:00:10+00:00",
    )

    all_rows = player_registry.list_player_log_events(db_path)
    limited_rows = player_registry.list_player_log_events(db_path, limit=2)
    teamkill_rows = player_registry.list_player_log_events(
        db_path,
        event_type=events.EVENT_TYPE_TEAMKILL,
    )
    alpha_rows = player_registry.list_player_log_events(db_path, reliable_id=PLAYER_ALPHA_ID)
    bravo_rows = player_registry.list_player_log_events(db_path, reliable_id=PLAYER_BRAVO_ID)
    charlie_rows = player_registry.list_player_log_events(db_path, reliable_id=PLAYER_CHARLIE_ID)
    name_rows = player_registry.list_player_log_events(db_path, query="Bravo")

    assert [row.event_type for row in all_rows] == [
        events.EVENT_TYPE_TEAMKILL,
        events.EVENT_TYPE_KILL,
        events.EVENT_TYPE_PLAYER_AUTHENTICATED,
    ]
    assert [row.event_type for row in limited_rows] == [
        events.EVENT_TYPE_TEAMKILL,
        events.EVENT_TYPE_KILL,
    ]
    assert [row.event_type for row in teamkill_rows] == [events.EVENT_TYPE_TEAMKILL]
    assert [row.event_type for row in alpha_rows] == [
        events.EVENT_TYPE_TEAMKILL,
        events.EVENT_TYPE_KILL,
        events.EVENT_TYPE_PLAYER_AUTHENTICATED,
    ]
    assert [row.event_type for row in bravo_rows] == [events.EVENT_TYPE_KILL]
    assert [row.event_type for row in charlie_rows] == [events.EVENT_TYPE_TEAMKILL]
    assert [row.event_type for row in name_rows] == [events.EVENT_TYPE_KILL]
    assert all_rows[0].teamkill is True
    assert all_rows[0].suicide is False
    assert all_rows[0].damage_type == "Bullet"
    assert all_rows[0].hit_zone == "LeftArm"
    assert all_rows[0].distance_m == 2.2

def test_ingest_ambiguous_timestamp_does_not_update_known_players(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "default" / "players.db"
    event = _parse(
        "12:00:01.000 BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
        raw_source_ref="journal:alpha-auth",
    )

    result = player_registry.ingest_player_log_events(
        db_path,
        [event],
        ingested_at="2026-01-02T09:00:00+00:00",
    )

    rows = _event_rows(db_path)
    assert result.stored_count == 1
    assert rows[0]["occurred_at"] is None
    assert rows[0]["observed_at"] is None
    assert rows[0]["log_timestamp"] == "12:00:01.000"
    assert rows[0]["time_source"] == events.EVENT_TIME_SOURCE_LOG_PREFIX_WITHOUT_DATE
    assert rows[0]["time_confidence"] == events.EVENT_TIME_CONFIDENCE_AMBIGUOUS
    assert rows[0]["collected_at"] == "2026-01-02T09:00:00+00:00"
    assert player_registry.list_known_players(db_path) == []


def test_ingest_treats_pre_v8_collected_log_event_as_duplicate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "default" / "players.db"
    player_registry.ensure_player_registry_db(db_path)
    line = (
        "18:31:00.000 BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One"
    )
    event = _parse(
        line,
        occurred_at="2026-07-05T18:31:00Z",
        raw_timestamp="18:31:00.000",
        time_source=events.EVENT_TIME_SOURCE_LOG_PREFIX_WITH_DATE,
        time_confidence=events.EVENT_TIME_CONFIDENCE_DERIVED,
        raw_source_ref="console.log:1",
    )
    legacy_row = player_registry._player_log_event_row(
        event,
        collected_at="2026-07-06T09:00:00+00:00",
        created_at="2026-07-06T09:00:00+00:00",
    )
    legacy_row["occurred_at"] = None
    legacy_row["log_timestamp"] = None
    legacy_row["time_source"] = events.EVENT_TIME_SOURCE_UNAVAILABLE
    legacy_row["time_confidence"] = events.EVENT_TIME_CONFIDENCE_AMBIGUOUS
    legacy_row["collected_at"] = None
    legacy_row["event_key"] = player_registry._player_log_event_key(
        legacy_row,
        dedupe_columns=player_registry._PLAYER_LOG_EVENT_LEGACY_V7_DEDUPE_COLUMNS,
    )
    with sqlite3.connect(db_path) as connection:
        columns = list(player_registry._PLAYER_LOG_EVENT_INSERT_COLUMNS)
        connection.execute(
            f"INSERT INTO player_log_events ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [legacy_row.get(column) for column in columns],
        )

    result = player_registry.ingest_player_log_events(
        db_path,
        [event],
        ingested_at="2026-07-06T10:00:00+00:00",
    )

    rows = _event_rows(db_path)
    assert result.stored_count == 0
    assert result.duplicate_count == 1
    assert len(rows) == 1
    assert rows[0]["event_key"] == legacy_row["event_key"]


def test_player_log_event_schema_migrates_v7_timestamp_contract(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "default" / "players.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE player_registry_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO player_registry_schema_meta(key, value)
            VALUES ('schema_version', '7')
            """
        )
        connection.execute(
            """
            CREATE TABLE player_log_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                source_ref TEXT,
                confidence TEXT NOT NULL,
                observed_at TEXT,
                log_timestamp TEXT,
                player_id TEXT,
                player_name TEXT,
                session_player_id TEXT,
                rpl_identity TEXT,
                connection_id TEXT,
                be_slot TEXT,
                player_faction TEXT,
                faction_resource TEXT,
                victim_id TEXT,
                victim_name TEXT,
                victim_session_player_id TEXT,
                victim_faction TEXT,
                instigator_id TEXT,
                instigator_name TEXT,
                instigator_session_player_id TEXT,
                instigator_faction TEXT,
                teamkill INTEGER,
                suicide INTEGER,
                ai_instigator INTEGER,
                damage_type TEXT,
                hit_zone TEXT,
                distance_m REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO player_log_events (
                event_key,
                event_type,
                source,
                confidence,
                player_id,
                player_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-key",
                events.EVENT_TYPE_PLAYER_AUTHENTICATED,
                events.SOURCE_BACKEND_AUTH,
                events.CONFIDENCE_HIGH,
                PLAYER_ALPHA_ID,
                "Alpha One",
                "2026-01-02T09:00:00+00:00",
            ),
        )

    player_registry.ensure_player_registry_db(db_path)

    columns = _sqlite_columns(db_path, "player_log_events")
    rows = _event_rows(db_path)
    assert _schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert {
        "occurred_at",
        "time_source",
        "time_confidence",
        "collected_at",
    } <= columns
    assert {
        "idx_player_log_events_occurred_at",
        "idx_player_log_events_collected_at",
        "idx_player_log_events_history_order",
    } <= _sqlite_indexes(db_path)
    assert rows[0]["occurred_at"] is None
    assert rows[0]["collected_at"] is None
    assert rows[0]["time_source"] == events.EVENT_TIME_SOURCE_UNAVAILABLE
    assert rows[0]["time_confidence"] == events.EVENT_TIME_CONFIDENCE_AMBIGUOUS
