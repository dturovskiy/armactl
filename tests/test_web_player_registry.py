"""Tests for persistent web player registry foundation."""

from __future__ import annotations

import json
import re
import sqlite3
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.permissions import PLAYERS_VIEW
from armactl.web.auth.setup import setup_owner_user
from armactl.web.services.player_registry import PlayerObservation
from armactl.web.services.player_sources import CurrentPlayer, CurrentPlayerRoster

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"
FORBIDDEN_PLAYER_SESSION_COLUMNS = {"ip", "ip_address", "address", "raw_line", "raw_path"}
PLAYER_HISTORY_INDEXES = {
    "idx_player_log_events_history_order",
    "idx_player_log_events_type_history_order",
    "idx_player_log_events_player_history_order",
    "idx_player_log_events_victim_history_order",
    "idx_player_log_events_instigator_history_order",
}


def _client(app):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(app)


def _form_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _login(client, username: str, password: str):
    form_response = client.get("/login")
    csrf_token = _form_token(form_response.text)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf_token},
        follow_redirects=False,
    )


def _reliable_player(
    name: str = "Alpha",
    reliable_id: str = "ABCDEF1234567890",
) -> PlayerObservation:
    return PlayerObservation(
        reliable_id=reliable_id,
        display_name=name,
        source="rcon.guid",
    )


def _unreliable_player(name: str = "Slot Only") -> PlayerObservation:
    return PlayerObservation(
        reliable_id="",
        display_name=name,
        source="rcon.roster",
    )


def _current_player(
    name: str = "Alpha",
    reliable_id: str = "ABCDEF1234567890",
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


def _parse_log_event(line: str, **kwargs):
    from armactl import player_log_events

    event = player_log_events.parse_player_log_event(line, **kwargs)
    assert event is not None
    return event


def _registry_schema_version(db_path: Path) -> str:
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


def _sqlite_schema_entries(db_path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _player_session_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM player_sessions"
        ).fetchone()
    assert row is not None
    return int(row[0])


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def _authed_client(tmp_path: Path, monkeypatch, *, db_path: Path | None = None):
    from armactl.web.app import create_app
    from armactl.web.services import player_registry

    setup_owner_user(tmp_path, "owner", "owner players password")
    if db_path is not None:
        monkeypatch.setattr(
            player_registry,
            "player_registry_db_path",
            lambda instance, data_root=None: db_path,
        )
    app = create_app(data_root=tmp_path)
    client = _client(app)
    login_response = _login(client, "owner", "owner players password")
    assert login_response.status_code == 303
    return client


def _players_csrf_token(client) -> str:
    response = client.get("/players/known", follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def test_registry_db_creation_has_no_ip_columns(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    player_registry.ensure_player_registry_db(db_path)

    assert db_path.is_file()
    columns = {
        column
        for table in _sqlite_tables(db_path)
        for column in _sqlite_columns(db_path, table)
    }
    assert not FORBIDDEN_PLAYER_SESSION_COLUMNS & columns
    assert "token" not in columns
    assert "password" not in columns


def test_registry_db_creation_has_schema_metadata(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    player_registry.ensure_player_registry_db(db_path)

    assert "player_registry_schema_meta" in _sqlite_tables(db_path)
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert PLAYER_HISTORY_INDEXES <= _sqlite_indexes(db_path)
    assert "idx_player_names_name" in _sqlite_indexes(db_path)
    assert "player_sessions" in _sqlite_tables(db_path)
    assert "idx_player_sessions_one_open_per_reliable_id" in _sqlite_indexes(db_path)
    assert "player_session_live_scan_windows" in _sqlite_tables(db_path)
    assert "idx_player_session_live_scan_windows_source" in _sqlite_indexes(db_path)


def test_registry_db_migrates_v4_history_indexes_idempotently(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        for index in PLAYER_HISTORY_INDEXES:
            connection.execute(f"DROP INDEX IF EXISTS {index}")
        connection.execute(
            "UPDATE player_registry_schema_meta SET value = ? WHERE key = ?",
            ("4", "schema_version"),
        )

    assert not (PLAYER_HISTORY_INDEXES & _sqlite_indexes(db_path))

    player_registry.ensure_player_registry_db(db_path)
    first_schema = _sqlite_schema_entries(db_path)

    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert PLAYER_HISTORY_INDEXES <= _sqlite_indexes(db_path)

    player_registry.ensure_player_registry_db(db_path)
    second_schema = _sqlite_schema_entries(db_path)
    assert second_schema == first_schema


def test_registry_db_migrates_v5_sessions_schema_to_stale_timeout(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ensure_player_registry_db(db_path)
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T10:00:00+00:00",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            UPDATE player_registry_schema_meta
            SET value = ?
            WHERE key = ?
            """,
            ("5", "schema_version"),
        )

    player_registry.ensure_player_registry_db(db_path)
    result = player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-06-16T11:00:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
    )

    assert result.closed is True
    assert result.session is not None
    assert result.session.end_reason == (
        player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT
    )
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION


def test_registry_db_migrates_v6_live_scan_windows_schema(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DROP TABLE player_session_live_scan_windows")
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
            ("table", "player_sessions"),
        ).fetchone()
        assert row is not None
        legacy_sql = str(row[0]).replace(
            chr(39) + "stale_absence" + chr(39) + ", ",
            "",
        )
        assert "stale_absence" not in legacy_sql
        connection.execute("DROP TABLE player_sessions")
        connection.execute(legacy_sql)
        connection.execute(
            """
            UPDATE player_registry_schema_meta
            SET value = ?
            WHERE key = ?
            """,
            ("6", "schema_version"),
        )

    player_registry.ensure_player_registry_db(db_path)
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T10:00:00+00:00",
    )
    result = player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-06-16T11:00:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    )

    assert result.closed is True
    assert result.session is not None
    assert result.session.end_reason == (
        player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE
    )
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert "player_session_live_scan_windows" in _sqlite_tables(db_path)
    assert "idx_player_session_live_scan_windows_reliable_id" in _sqlite_indexes(
        db_path,
    )
    assert "idx_player_session_live_scan_windows_source" in _sqlite_indexes(db_path)


def test_registry_db_rebuilds_legacy_v5_session_end_reason_check(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO players(
                reliable_id,
                current_name,
                first_seen_at,
                last_seen_at,
                seen_count,
                last_source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                PLAYER_ALPHA_ID,
                "Alpha",
                "2026-06-16T10:00:00+00:00",
                "2026-06-16T10:00:00+00:00",
                1,
                "test",
            ),
        )
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
            ("table", "player_sessions"),
        ).fetchone()
        assert row is not None
        legacy_sql = str(row[0]).replace(
            chr(39) + "stale_timeout" + chr(39) + ", ",
            "",
        )
        assert "stale_timeout" not in legacy_sql
        connection.execute("DROP TABLE player_sessions")
        connection.execute(legacy_sql)
        insert_session_sql = (
            "INSERT INTO player_sessions("
            "reliable_id, name_at_open, name_last, open_observed_at, "
            "last_seen_at, status, open_source, last_seen_source, "
            "open_confidence, last_seen_confidence, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        connection.execute(
            insert_session_sql,
            (
                PLAYER_ALPHA_ID,
                "Alpha",
                "Alpha",
                "2026-06-16T10:00:00+00:00",
                "2026-06-16T10:00:00+00:00",
                player_registry.PLAYER_SESSION_STATUS_OPEN,
                player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
                player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
                player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
                player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
                "2026-06-16T10:00:00+00:00",
                "2026-06-16T10:00:00+00:00",
            ),
        )
        connection.execute(
            "UPDATE player_registry_schema_meta SET value = ? WHERE key = ?",
            ("5", "schema_version"),
        )

    player_registry.ensure_player_registry_db(db_path)
    result = player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-06-16T11:00:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
    )

    assert result.closed is True
    assert result.session is not None
    assert result.session.end_reason == (
        player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT
    )
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION


def test_registry_db_migrates_v2_to_player_sessions_schema(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE player_sessions")
        connection.execute(
            """
            UPDATE player_registry_schema_meta
            SET value = '2'
            WHERE key = 'schema_version'
            """
        )
        connection.execute(
            """
            INSERT INTO players(
                reliable_id,
                current_name,
                first_seen_at,
                last_seen_at,
                seen_count,
                last_source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                PLAYER_ALPHA_ID,
                "Alpha",
                "2026-06-16T12:00:00+00:00",
                "2026-06-16T12:00:00+00:00",
                1,
                "test",
            ),
        )
    assert "player_sessions" not in _sqlite_tables(db_path)

    player_registry.ensure_player_registry_db(db_path)

    columns = _sqlite_columns(db_path, "player_sessions")
    assert {
        "session_id",
        "reliable_id",
        "name_at_open",
        "name_last",
        "open_observed_at",
        "last_seen_at",
        "close_observed_at",
        "status",
        "open_source",
        "open_source_ref",
        "last_seen_source",
        "last_seen_source_ref",
        "close_source",
        "close_source_ref",
        "open_confidence",
        "last_seen_confidence",
        "close_confidence",
        "end_reason",
        "rpl_identity",
        "connection_id",
        "session_player_id",
        "be_slot",
        "faction",
        "side",
        "scanner_checkpoint_source",
        "scanner_checkpoint_ref",
        "scanner_checkpoint_at",
        "created_at",
        "updated_at",
    } <= columns
    assert not FORBIDDEN_PLAYER_SESSION_COLUMNS & columns
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert {
        "idx_player_sessions_reliable_id",
        "idx_player_sessions_status",
        "idx_player_sessions_open_observed_at",
        "idx_player_sessions_last_seen_at",
        "idx_player_sessions_close_observed_at",
        "idx_player_sessions_open_source",
        "idx_player_sessions_last_seen_source",
        "idx_player_sessions_close_source",
        "idx_player_sessions_scanner_checkpoint",
        "idx_player_sessions_one_open_per_reliable_id",
    } <= _sqlite_indexes(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        insert_session_sql = """
            INSERT INTO player_sessions(
                reliable_id,
                name_at_open,
                name_last,
                open_observed_at,
                last_seen_at,
                status,
                open_source,
                last_seen_source,
                open_confidence,
                last_seen_confidence,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        connection.execute(
            insert_session_sql,
            (
                PLAYER_ALPHA_ID,
                "Alpha",
                "Alpha",
                "2026-06-16T12:00:00+00:00",
                "2026-06-16T12:00:00+00:00",
                player_registry.PLAYER_SESSION_STATUS_OPEN,
                player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
                player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
                player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
                player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
                "2026-06-16T12:00:00+00:00",
                "2026-06-16T12:00:00+00:00",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                insert_session_sql,
                (
                    PLAYER_ALPHA_ID,
                    "Alpha Again",
                    "Alpha Again",
                    "2026-06-16T12:01:00+00:00",
                    "2026-06-16T12:01:00+00:00",
                    player_registry.PLAYER_SESSION_STATUS_OPEN,
                    player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
                    player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
                    player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
                    player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
                    "2026-06-16T12:01:00+00:00",
                    "2026-06-16T12:01:00+00:00",
                ),
            )
        connection.execute(
            insert_session_sql,
            (
                PLAYER_ALPHA_ID,
                "Alpha Closed",
                "Alpha Closed",
                "2026-06-16T11:00:00+00:00",
                "2026-06-16T11:05:00+00:00",
                player_registry.PLAYER_SESSION_STATUS_CLOSED,
                player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
                player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
                player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
                player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
                "2026-06-16T11:00:00+00:00",
                "2026-06-16T11:05:00+00:00",
            ),
        )
        count = connection.execute("SELECT COUNT(*) FROM player_sessions").fetchone()[0]
    assert count == 2


def test_registry_db_session_schema_is_idempotent(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    player_registry.ensure_player_registry_db(db_path)
    first_schema = _sqlite_schema_entries(db_path)
    player_registry.ensure_player_registry_db(db_path)
    second_schema = _sqlite_schema_entries(db_path)

    assert second_schema == first_schema


def test_observe_player_session_opens_reliable_session(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    result = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        source_ref="journal:auth:1",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at="2026-06-16T12:00:00+00:00",
        rpl_identity="42",
    )

    assert result.written is True
    assert result.created is True
    assert result.updated is False
    assert result.session is not None
    assert result.session.reliable_id == PLAYER_ALPHA_ID
    assert result.session.name_at_open == "Alpha One"
    assert result.session.name_last == "Alpha One"
    assert result.session.open_observed_at == "2026-06-16T12:00:00+00:00"
    assert result.session.last_seen_at == "2026-06-16T12:00:00+00:00"
    assert result.session.status == player_registry.PLAYER_SESSION_STATUS_OPEN
    assert result.session.open_source == player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH
    assert result.session.open_source_ref == "journal:auth:1"
    assert result.session.open_confidence == player_registry.PLAYER_SESSION_CONFIDENCE_HIGH
    assert result.session.rpl_identity == "42"
    assert _player_session_count(db_path) == 1
    assert player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID) == result.session


def test_observe_player_session_repeated_updates_open_row_not_duplicate(
    tmp_path: Path,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    first = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        source_ref="journal:auth:1",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at="2026-06-16T12:00:00+00:00",
        rpl_identity="42",
        faction="US",
    )
    second = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha Later",
        source=player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
        source_ref="journal:update:2",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at="2026-06-16T12:05:00+00:00",
        connection_id="conn-7",
        session_player_id="player-7",
        side="BLUFOR",
    )

    assert first.session is not None
    assert second.session is not None
    assert second.created is False
    assert second.updated is True
    assert second.session.session_id == first.session.session_id
    assert second.session.name_at_open == "Alpha One"
    assert second.session.name_last == "Alpha Later"
    assert second.session.last_seen_at == "2026-06-16T12:05:00+00:00"
    assert second.session.last_seen_source == (
        player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE
    )
    assert second.session.last_seen_source_ref == "journal:update:2"
    assert second.session.rpl_identity == "42"
    assert second.session.connection_id == "conn-7"
    assert second.session.session_player_id == "player-7"
    assert second.session.faction == "US"
    assert second.session.side == "BLUFOR"
    assert _player_session_count(db_path) == 1


def test_close_player_session_updates_status_and_close_fields(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    opened = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        observed_at="2026-06-16T12:00:00+00:00",
    )

    closed = player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-06-16T12:10:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
        source_ref="journal:shutdown:10",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_LOW,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
    )

    assert opened.session is not None
    assert closed.written is True
    assert closed.closed is True
    assert closed.session is not None
    assert closed.session.session_id == opened.session.session_id
    assert closed.session.status == player_registry.PLAYER_SESSION_STATUS_CLOSED
    assert closed.session.close_observed_at == "2026-06-16T12:10:00+00:00"
    assert closed.session.close_source == player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE
    assert closed.session.close_source_ref == "journal:shutdown:10"
    assert closed.session.close_confidence == player_registry.PLAYER_SESSION_CONFIDENCE_LOW
    assert closed.session.end_reason == player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY
    assert player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID) is None
    assert player_registry.get_player_session(db_path, opened.session.session_id) == closed.session


def test_observe_player_session_ignores_unreliable_id_without_db_write(
    tmp_path: Path,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    result = player_registry.observe_player_session(
        db_path,
        reliable_id="slot-7",
        display_name="Slot Only",
        source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
        observed_at="2026-06-16T12:00:00+00:00",
    )

    assert result.written is False
    assert result.ignored_count == 1
    assert result.session is None
    assert not db_path.exists()


def test_player_session_writer_sanitizes_sources_and_optional_evidence(
    tmp_path: Path,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    result = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha token=raw-player-secret 198.51.100.9",
        source="/home/deus/projects/armactl/private.log token=raw-source-secret",
        source_ref=(
            "/home/deus/armactl-data/default/config/logs/run/"
            "console-198.51.100.7.log:203.0.113.9:1"
        ),
        confidence="not-a-confidence",
        observed_at="2026-06-16T12:00:00+00:00",
        rpl_identity="198.51.100.20",
        connection_id="/home/deus/raw/connection-id",
        session_player_id="player-7 token=raw-correlation-secret",
        be_slot="7",
        faction="US 198.51.100.21 token=raw-faction-secret",
        side="WEST 203.0.113.10",
    )

    assert result.session is not None
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM player_sessions").fetchone())
    encoded_row = json.dumps(row, sort_keys=True)

    assert row["name_at_open"] == "Alpha token=*** ***"
    assert row["open_source"] == "private.log token=***"
    assert row["open_source_ref"] == "console-***.log:***"
    assert row["open_confidence"] == player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM
    assert row["rpl_identity"] is None
    assert row["connection_id"] is None
    assert row["session_player_id"] == "player-7 token=***"
    assert row["faction"] == "US *** token=***"
    assert row["side"] == "WEST ***"
    for forbidden in (
        "raw-player-secret",
        "raw-source-secret",
        "raw-correlation-secret",
        "raw-faction-secret",
        "/home/deus",
        "armactl-data",
        "198.51.100.7",
        "198.51.100.9",
        "198.51.100.20",
        "198.51.100.21",
        "203.0.113.9",
        "203.0.113.10",
    ):
        assert forbidden not in encoded_row


def test_registry_db_migrates_existing_minimal_schema_idempotently(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE players (
                reliable_id TEXT PRIMARY KEY,
                current_name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE player_names (
                reliable_id TEXT NOT NULL,
                name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (reliable_id, name)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO players(
                reliable_id,
                current_name,
                first_seen_at,
                last_seen_at,
                seen_count
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "ABCDEF1234567890",
                "Alpha",
                "2026-06-16T12:00:00+00:00",
                "2026-06-16T12:00:00+00:00",
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO player_names(
                reliable_id,
                name,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "ABCDEF1234567890",
                "Alpha",
                "2026-06-16T12:00:00+00:00",
                "2026-06-16T12:00:00+00:00",
            ),
        )
    db_path.chmod(0o644)

    player_registry.ensure_player_registry_db(db_path)

    assert db_path.stat().st_mode & 0o777 == 0o600
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert "last_source" in _sqlite_columns(db_path, "players")
    assert "seen_count" in _sqlite_columns(db_path, "player_names")
    assert "ip_address" not in _sqlite_columns(db_path, "players")
    assert "idx_player_names_name" in _sqlite_indexes(db_path)
    assert "player_log_events" in _sqlite_tables(db_path)
    assert "idx_player_log_events_observed_at" in _sqlite_indexes(db_path)
    assert "player_sessions" in _sqlite_tables(db_path)
    assert "idx_player_sessions_one_open_per_reliable_id" in _sqlite_indexes(db_path)
    assert player_registry.list_known_players(db_path)[0].last_source == "unknown"
    assert player_registry.list_player_names(db_path, "ABCDEF1234567890")[0].seen_count == 1

    with sqlite3.connect(db_path) as connection:
        first_snapshot = (
            connection.execute("SELECT * FROM players ORDER BY reliable_id").fetchall(),
            connection.execute("SELECT * FROM player_names ORDER BY reliable_id, name").fetchall(),
            connection.execute("SELECT * FROM player_registry_schema_meta ORDER BY key").fetchall(),
        )
    player_registry.ensure_player_registry_db(db_path)

    with sqlite3.connect(db_path) as connection:
        second_snapshot = (
            connection.execute("SELECT * FROM players ORDER BY reliable_id").fetchall(),
            connection.execute("SELECT * FROM player_names ORDER BY reliable_id, name").fetchall(),
            connection.execute("SELECT * FROM player_registry_schema_meta ORDER BY key").fetchall(),
        )
    assert second_snapshot == first_snapshot


def test_registry_db_creation_uses_private_file_mode(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    player_registry.ensure_player_registry_db(db_path)

    assert db_path.stat().st_mode & 0o777 == 0o600

    db_path.chmod(0o644)
    player_registry.ensure_player_registry_db(db_path)

    assert db_path.stat().st_mode & 0o777 == 0o600


def test_record_snapshot_stores_reliable_players_and_ignores_unreliable(
    tmp_path: Path,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    result = player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("Alpha token=raw-secret"), _unreliable_player()],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    players = player_registry.list_known_players(db_path)

    assert result.stored_count == 1
    assert result.ignored_count == 1
    assert len(players) == 1
    assert players[0].reliable_id == "ABCDEF1234567890"
    assert players[0].current_name == "Alpha token=***"
    assert players[0].seen_count == 1


def test_record_snapshot_updates_name_history(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("Alpha")],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("Bravo")],
        observed_at="2026-06-16T12:05:00+00:00",
    )

    player = player_registry.get_known_player(db_path, "ABCDEF1234567890")
    names = player_registry.list_player_names(db_path, "ABCDEF1234567890")

    assert player is not None
    assert player.current_name == "Bravo"
    assert player.first_seen_at == "2026-06-16T12:00:00+00:00"
    assert player.last_seen_at == "2026-06-16T12:05:00+00:00"
    assert player.seen_count == 2
    assert [name.name for name in names] == ["Bravo", "Alpha"]


def test_refresh_current_players_service_updates_known_and_last_seen(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import (
        player_actions,
        player_current_cache,
        player_registry,
        player_sources,
    )

    player_current_cache.clear_current_roster_cache()
    rosters = iter(
        (
            _roster(_current_player("Alpha")),
            _roster(_current_player("Alpha Later")),
        )
    )
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: next(rosters),
    )
    db_path = tmp_path / "default" / "players.db"

    first = player_actions.refresh_current_players(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-16T12:00:00+00:00",
    )
    second = player_actions.refresh_current_players(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-16T12:05:00+00:00",
    )
    player = player_registry.get_known_player(db_path, "ABCDEF1234567890")
    cached = player_current_cache.get_cached_current_roster_snapshot(
        "default",
        data_root=tmp_path,
    )
    persistent = player_current_cache.get_persistent_current_roster_snapshot(
        "default",
        data_root=tmp_path,
    )

    assert first.success is True
    assert first.observed_count == 1
    assert first.stored_count == 1
    assert first.ignored_count == 0
    assert second.success is True
    assert second.observed_count == 1
    assert second.stored_count == 1
    assert player is not None
    assert player.current_name == "Alpha Later"
    assert player.first_seen_at == "2026-06-16T12:00:00+00:00"
    assert player.last_seen_at == "2026-06-16T12:05:00+00:00"
    assert player.seen_count == 2
    assert cached is not None
    assert [player.display_name for player in cached.players] == ["Alpha Later"]
    assert cached.source == "rcon.roster"
    assert persistent is not None
    assert [player.display_name for player in persistent.players] == ["Alpha Later"]
    assert persistent.source == "rcon.roster"
    assert _player_session_count(db_path) == 0


def test_refresh_current_players_service_ignores_unreliable_and_avoids_noop_writes(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import (
        player_actions,
        player_current_cache,
        player_sources,
    )

    db_path = tmp_path / "default" / "players.db"
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_unreliable_current_player("Slot Only")),
    )

    unreliable = player_actions.refresh_current_players("default", data_root=tmp_path)
    dry_run = player_actions.refresh_current_players(
        "default",
        data_root=tmp_path,
        dry_run=True,
    )

    assert unreliable.success is True
    assert unreliable.observed_count == 1
    assert unreliable.stored_count == 0
    assert unreliable.ignored_count == 1
    assert dry_run.success is True
    assert dry_run.dry_run is True
    assert dry_run.stored_count == 0
    assert not db_path.exists()
    assert _player_session_count(db_path) == 0
    assert (
        player_current_cache.get_persistent_current_roster_snapshot(
            "default",
            data_root=tmp_path,
        )
        is None
    )

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: CurrentPlayerRoster(
            available=False,
            players=(),
            total_count=0,
            source="rcon.roster",
            status="unavailable",
            error="connection failed token=raw-secret",
        ),
    )
    unavailable = player_actions.refresh_current_players("default", data_root=tmp_path)

    assert unavailable.success is False
    assert unavailable.message == "Current player roster unavailable."
    assert unavailable.stored_count == 0
    assert not db_path.exists()
    assert _player_session_count(db_path) == 0
    assert (
        player_current_cache.get_persistent_current_roster_snapshot(
            "default",
            data_root=tmp_path,
        )
        is None
    )


def test_list_known_players_searches_by_name_and_id(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [
            _reliable_player("Alpha", "ABCDEF1234567890"),
            _reliable_player("Bravo", "BBBBBBBBBBBBBBBB"),
        ],
        observed_at="2026-06-16T12:00:00+00:00",
    )

    by_name = player_registry.list_known_players(db_path, query="brav")
    by_id = player_registry.list_known_players(db_path, query="cdef")

    assert [player.current_name for player in by_name] == ["Bravo"]
    assert [player.current_name for player in by_id] == ["Alpha"]


def test_list_player_summaries_aggregates_event_stats(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [
            _reliable_player("Alpha One", PLAYER_ALPHA_ID),
            _reliable_player("Bravo Two", PLAYER_BRAVO_ID),
        ],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "SCRIPT : INFO: Faction: player Alpha One "
                f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) "
                "has joined faction #US_Army (US)",
                observed_at="2026-06-16T12:01:00+00:00",
                raw_source_ref="journal:faction",
            ),
            _parse_log_event(
                "SCRIPT : INFO: KILL TK: Bravo Two "
                f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
                "at <4 5 6> was killed by Alpha One "
                f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
                "who was at that time at <4 5 7> [2.2m away from the corpse]. "
                "With last inflicted damage type Bullet to the 'LeftArm' hit zone",
                observed_at="2026-06-16T12:02:00+00:00",
                raw_source_ref="journal:teamkill",
            ),
            _parse_log_event(
                "SCRIPT : INFO: KILL SUICIDE: Alpha One "
                f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
                "at <1 2 3> killed himself! "
                "With last inflicted damage type Explosion to the 'Torso' hit zone",
                observed_at="2026-06-16T12:03:00+00:00",
                raw_source_ref="journal:suicide",
            ),
        ],
        ingested_at="2026-06-16T12:04:00+00:00",
    )

    summaries = {
        player.reliable_id: player
        for player in player_registry.list_player_summaries(db_path)
    }

    assert summaries[PLAYER_ALPHA_ID].faction == "US"
    assert summaries[PLAYER_ALPHA_ID].event_count == 3
    assert summaries[PLAYER_ALPHA_ID].kill_count == 1
    assert summaries[PLAYER_ALPHA_ID].death_count == 1
    assert summaries[PLAYER_ALPHA_ID].teamkill_count == 1
    assert summaries[PLAYER_ALPHA_ID].suicide_count == 1
    assert summaries[PLAYER_BRAVO_ID].event_count == 1
    assert summaries[PLAYER_BRAVO_ID].death_count == 1


def test_list_player_sessions_filters_read_only_surface(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha First",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at="2026-06-16T12:00:00+00:00",
    )
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha Last",
        source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
        observed_at="2026-06-16T12:05:00+00:00",
    )
    player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-06-16T12:10:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_LOW,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    )
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_BRAVO_ID,
        display_name="Bravo One",
        source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
        observed_at="2026-06-16T12:12:00+00:00",
    )

    sessions = player_registry.list_player_sessions(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        query="Last",
        status=player_registry.PLAYER_SESSION_STATUS_CLOSED,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        limit=5,
    )

    assert [session.reliable_id for session in sessions] == [PLAYER_ALPHA_ID]
    assert sessions[0].name_at_open == "Alpha First"
    assert sessions[0].name_last == "Alpha Last"
    assert sessions[0].status == player_registry.PLAYER_SESSION_STATUS_CLOSED
    assert sessions[0].end_reason == player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE
    assert player_registry.list_player_sessions(
        db_path,
        reliable_id="not-a-reliable-id",
    ) == []
    assert player_registry.list_player_sessions(
        tmp_path / "default" / "missing.db",
    ) == []


def test_players_route_requires_authentication(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    get_response = client.get("/players", follow_redirects=False)
    known_response = client.get("/players/known", follow_redirects=False)
    history_response = client.get("/players/history", follow_redirects=False)
    sessions_response = client.get("/players/sessions", follow_redirects=False)
    current_json_response = client.get("/players/current.json", follow_redirects=False)
    post_response = client.post("/players/refresh", data={}, follow_redirects=False)
    current_post_response = client.post(
        "/players/refresh-current",
        data={},
        follow_redirects=False,
    )

    assert get_response.status_code == 303
    assert get_response.headers["location"] == "/login"
    assert known_response.status_code == 303
    assert known_response.headers["location"] == "/login"
    assert history_response.status_code == 303
    assert history_response.headers["location"] == "/login"
    assert sessions_response.status_code == 303
    assert sessions_response.headers["location"] == "/login"
    assert current_json_response.status_code == 303
    assert current_json_response.headers["location"] == "/login"
    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/login"
    assert current_post_response.status_code == 303
    assert current_post_response.headers["location"] == "/login"


def test_players_route_requires_players_view_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.services import player_current_refresh

    setup_owner_user(tmp_path, "owner", "owner players password")
    set_web_owner_permissions(set())
    monkeypatch.setattr(
        player_current_refresh,
        "request_player_current_refresh_and_start",
        AssertionError,
    )
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", "owner players password")

    response = client.get("/players", follow_redirects=False)
    known_response = client.get("/players/known", follow_redirects=False)
    history_response = client.get("/players/history", follow_redirects=False)
    sessions_response = client.get("/players/sessions", follow_redirects=False)
    current_json_response = client.get("/players/current.json", follow_redirects=False)
    post_response = client.post(
        "/players/refresh",
        data={"csrf_token": "unused"},
        follow_redirects=False,
    )
    current_post_response = client.post(
        "/players/refresh-current",
        data={"csrf_token": "unused"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert known_response.status_code == 403
    assert known_response.text == "Permission denied."
    assert history_response.status_code == 403
    assert history_response.text == "Permission denied."
    assert sessions_response.status_code == 403
    assert sessions_response.text == "Permission denied."
    assert current_json_response.status_code == 403
    assert current_json_response.text == "Permission denied."
    assert post_response.status_code == 403
    assert post_response.text == "Permission denied."
    assert current_post_response.status_code == 403
    assert current_post_response.text == "Permission denied."
    assert not PLAYERS_VIEW.endswith(":manage")


def test_players_route_html_escapes_player_names(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("<Alpha & Co>")],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch, db_path=db_path)

    response = client.get("/players/known", follow_redirects=False)

    assert response.status_code == 200
    assert "&lt;Alpha &amp; Co&gt;" in response.text
    assert "<Alpha & Co>" not in response.text


def test_known_players_page_is_identity_directory_not_stat_board(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("Alpha One", PLAYER_ALPHA_ID)],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch, db_path=db_path)

    response = client.get("/players/known", follow_redirects=False)

    assert response.status_code == 200
    html = response.text
    assert "Known reliable player identities recorded from current roster refreshes" in html
    assert "Alpha One" in html
    assert PLAYER_ALPHA_ID[:8] in html
    assert "<th>Seen count</th>" in html
    assert "<th>First seen</th>" in html
    assert "<th>Last seen</th>" in html
    assert "Source:" in html
    assert "<th>Source</th>" not in html
    assert "<th>Kills</th>" not in html
    assert "<th>Deaths</th>" not in html
    assert "<th>Events</th>" not in html


def test_players_route_defaults_to_current_player_table(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(
            _current_player("Live Alpha", PLAYER_ALPHA_ID),
            _unreliable_current_player("Live Slot"),
        ),
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players", follow_redirects=False)

    assert response.status_code == 200
    html = response.text
    assert "Current player table" in html
    assert "Live Alpha" in html
    assert "Live Slot" in html
    assert PLAYER_ALPHA_ID in html
    assert "Refresh current players" in html
    assert "players_current_poll.js" in html
    assert "data-current-players-root" in html
    assert "data-current-players-endpoint=\"/players/current.json\"" in html
    assert "data-current-players-interval-ms=\"60000\"" in html
    assert "data-current-players-search" in html
    assert 'action="/players/refresh-current"' in html
    assert "<th>Source</th>" in html
    assert "<th>Last seen</th>" not in html
    assert "<th>K/D</th>" not in html
    assert "<th>Faction</th>" not in html
    assert "<th>Role</th>" not in html
    assert "<th>Joined</th>" not in html
    assert "Not tracked" not in html
    assert "Known player table" not in html
    assert "Player event log" not in html
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_route_shows_count_only_when_roster_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _count_only_roster(7),
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players", follow_redirects=False)
    html = response.text

    assert response.status_code == 200
    assert "Roster unavailable; A2S reports 7 current player(s)." in html
    assert "Showing 0 of 7 current player(s)" in html
    assert "Showing 0 of 0 current player(s)" not in html
    assert ">No current players online.<" not in html
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_current_json_returns_count_only_fields_without_player_db_writes(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _count_only_roster(7),
    )
    client = _authed_client(tmp_path, monkeypatch)
    response = client.get("/players/current.json", follow_redirects=False)
    db_path = tmp_path / "default" / "players.db"

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["observed_count"] == 7
    assert payload["total_count"] == 7
    assert payload["filtered_count"] == 0
    assert payload["count_source"] == "a2s"
    assert payload["roster_available"] is False
    assert payload["roster_configured"] is True
    assert payload["players"] == []
    persistent = player_current_cache.get_persistent_current_roster_snapshot(
        "default",
        data_root=tmp_path,
    )
    assert persistent is not None
    assert persistent.observed_count == 7
    assert persistent.total_count == 7
    assert persistent.players == ()
    assert persistent.roster_available is False
    assert not db_path.exists()
    assert _player_session_count(db_path) == 0


def test_current_players_polling_hook_only_on_current_page(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Live Alpha", PLAYER_ALPHA_ID)),
    )
    client = _authed_client(tmp_path, monkeypatch)

    current = client.get("/players?player_search=Live", follow_redirects=False)
    known = client.get("/players/known", follow_redirects=False)
    history = client.get("/players/history", follow_redirects=False)

    assert current.status_code == 200
    assert known.status_code == 200
    assert history.status_code == 200
    assert "players_current_poll.js" in current.text
    assert "data-current-players-root" in current.text
    assert "name=\"player_search\" value=\"Live\"" in current.text
    assert "players_current_poll.js" not in known.text
    assert "data-current-players-root" not in known.text
    assert "players_current_poll.js" not in history.text
    assert "data-current-players-root" not in history.text


def test_current_players_polling_js_uses_no_store_and_preserves_search():
    script = Path(
        "src/armactl/web/static/js/players_current_poll.js",
    ).read_text()

    assert "DEFAULT_POLL_INTERVAL_MS = 60000" in script
    assert "cache: \"no-store\"" in script
    assert "url.searchParams.set(\"player_search\", query)" in script
    assert "currentPlayersCountOnlyTemplate" in script
    assert "data.observed_count" in script
    assert "data.roster_available === false" in script
    assert "window.setInterval(refreshCurrentPlayers, intervalMs)" in script


def test_players_route_uses_fresh_current_roster_cache(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    calls: list[str] = []

    def load_roster(instance):
        calls.append(instance)
        return _roster(_current_player("Live Alpha", PLAYER_ALPHA_ID))

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        load_roster,
    )
    client = _authed_client(tmp_path, monkeypatch)

    first = client.get("/players", follow_redirects=False)
    second = client.get("/players", follow_redirects=False)

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == ["default"]
    assert "Live Alpha" in first.text
    assert "Live Alpha" in second.text
    assert "Updated" in first.text
    assert "data-current-players-stale hidden" in second.text
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_current_json_uses_fresh_persistent_cache_without_live_source(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    player_current_cache.store_persistent_current_roster_snapshot(
        player_current_cache.CurrentRosterSnapshot(
            instance="default",
            players=(
                player_current_cache.CurrentRosterPlayerSnapshot(
                    display_name="Persistent Alpha",
                    reliable_id=PLAYER_ALPHA_ID,
                    source="rcon.guid",
                ),
            ),
            source="rcon.roster",
            status="available",
            error="",
            collected_at=datetime.now(timezone.utc).isoformat(),
        ),
        data_root=tmp_path,
    )
    calls: list[str] = []

    def fail_if_live_source_is_used(instance):
        calls.append(instance)
        raise AssertionError("live source should not be called for fresh cache")

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        fail_if_live_source_is_used,
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/current.json", follow_redirects=False)
    db_path = tmp_path / "default" / "players.db"

    assert response.status_code == 200
    assert calls == []
    payload = response.json()
    assert payload["cache_status"] == "persistent"
    assert payload["is_stale"] is False
    assert payload["players"] == [
        {
            "display_name": "Persistent Alpha",
            "reliable_id": PLAYER_ALPHA_ID,
            "source": "rcon.guid",
        }
    ]
    assert payload["updated_at"]
    assert not db_path.exists()
    assert _player_session_count(db_path) == 0


def test_players_current_json_serves_stale_persistent_cache_on_live_failure(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    player_current_cache.store_persistent_current_roster_snapshot(
        player_current_cache.CurrentRosterSnapshot(
            instance="default",
            players=(
                player_current_cache.CurrentRosterPlayerSnapshot(
                    display_name="Persistent Stale Alpha",
                    reliable_id=PLAYER_ALPHA_ID,
                    source="rcon.guid",
                ),
            ),
            source="rcon.roster",
            status="available",
            error="",
            collected_at="1970-01-01T00:00:00+00:00",
        ),
        data_root=tmp_path,
    )
    calls: list[str] = []

    def fail_roster(instance):
        calls.append(instance)
        raise RuntimeError(
            "failed token=raw-roster-secret from 198.51.100.9 "
            "using /home/deus/private.log"
        )

    monkeypatch.setattr(player_sources, "load_current_player_roster", fail_roster)
    client = _authed_client(tmp_path, monkeypatch)
    response = client.get("/players/current.json", follow_redirects=False)
    rendered = json.dumps(response.json(), sort_keys=True)
    db_path = tmp_path / "default" / "players.db"

    assert response.status_code == 200
    assert calls == ["default"]
    payload = response.json()
    assert payload["cache_status"] == "stale_persistent"
    assert payload["is_stale"] is True
    assert payload["players"][0]["display_name"] == "Persistent Stale Alpha"
    assert "raw-roster-secret" not in rendered
    assert "198.51.100.9" not in rendered
    assert "/home/deus/private.log" not in rendered
    assert not db_path.exists()
    assert _player_session_count(db_path) == 0


def test_players_route_refreshes_stale_current_roster_cache(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    stale_snapshot = player_current_cache.CurrentRosterSnapshot(
        instance="default",
        players=(
            player_current_cache.CurrentRosterPlayerSnapshot(
                display_name="Cached Alpha",
                reliable_id=PLAYER_ALPHA_ID,
                source="rcon.roster",
            ),
        ),
        source="rcon.roster",
        status="available",
        error="",
        collected_at="1970-01-01T00:00:00+00:00",
    )
    player_current_cache.store_current_roster_snapshot(
        stale_snapshot,
        data_root=tmp_path,
    )
    calls: list[str] = []

    def load_roster(instance):
        calls.append(instance)
        return _roster(_current_player("Fresh Bravo", PLAYER_BRAVO_ID))

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        load_roster,
    )
    client = _authed_client(tmp_path, monkeypatch)
    response = client.get("/players", follow_redirects=False)

    assert response.status_code == 200
    assert calls == ["default"]
    assert "Fresh Bravo" in response.text
    assert "Cached Alpha" not in response.text
    assert "data-current-players-stale hidden" in response.text
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_route_serves_stale_cache_when_live_roster_fails(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    stale_snapshot = player_current_cache.CurrentRosterSnapshot(
        instance="default",
        players=(
            player_current_cache.CurrentRosterPlayerSnapshot(
                display_name="Cached Alpha",
                reliable_id=PLAYER_ALPHA_ID,
                source="rcon.roster",
            ),
        ),
        source="rcon.roster",
        status="available",
        error="",
        collected_at="1970-01-01T00:00:00+00:00",
    )
    player_current_cache.store_current_roster_snapshot(
        stale_snapshot,
        data_root=tmp_path,
    )
    calls: list[str] = []

    def fail_roster(instance):
        calls.append(instance)
        raise RuntimeError(
            "failed token=raw-roster-secret from 198.51.100.9 "
            "using /home/deus/private.log"
        )

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        fail_roster,
    )
    client = _authed_client(tmp_path, monkeypatch)
    response = client.get("/players", follow_redirects=False)
    html = response.text

    assert response.status_code == 200
    assert calls == ["default"]
    assert "Cached Alpha" in html
    assert "Stale" in html
    assert "raw-roster-secret" not in html
    assert "198.51.100.9" not in html
    assert "/home/deus/private.log" not in html
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_current_json_uses_current_roster_cache_without_db_writes(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()
    calls: list[str] = []

    def load_roster(instance):
        calls.append(instance)
        return _roster(_current_player("Json Alpha", PLAYER_ALPHA_ID))

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        load_roster,
    )
    client = _authed_client(tmp_path, monkeypatch)
    first = client.get("/players/current.json", follow_redirects=False)
    second = client.get("/players/current.json", follow_redirects=False)
    db_path = tmp_path / "default" / "players.db"

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["content-type"].startswith("application/json")
    assert calls == ["default"]
    payload = second.json()
    assert payload["source"] == "rcon.roster"
    assert payload["age_seconds"] is not None
    assert payload["is_stale"] is False
    assert payload["players"] == [
        {
            "display_name": "Json Alpha",
            "reliable_id": PLAYER_ALPHA_ID,
            "source": "rcon.guid",
        }
    ]
    persistent = player_current_cache.get_persistent_current_roster_snapshot(
        "default",
        data_root=tmp_path,
    )
    assert persistent is not None
    assert [player.display_name for player in persistent.players] == ["Json Alpha"]
    assert not db_path.exists()
    assert _player_session_count(db_path) == 0


def test_players_current_output_sanitizes_roster_values(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_current_cache, player_sources

    player_current_cache.clear_current_roster_cache()

    def load_roster(instance):
        return CurrentPlayerRoster(
            available=True,
            players=(
                CurrentPlayer(
                    display_name=(
                        "Alpha token=raw-player-secret 198.51.100.9 "
                        "/home/deus/name.log"
                    ),
                    reliable_id=PLAYER_ALPHA_ID,
                    admin_reference=PLAYER_ALPHA_ID,
                    source="/home/deus/source.log token=raw-source-secret",
                ),
            ),
            total_count=1,
            source="rcon.roster token=raw-roster-secret",
            status="available",
            error="",
        )

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        load_roster,
    )
    client = _authed_client(tmp_path, monkeypatch)
    page_response = client.get("/players", follow_redirects=False)
    json_response = client.get("/players/current.json", follow_redirects=False)
    rendered = page_response.text + json.dumps(json_response.json(), sort_keys=True)

    assert page_response.status_code == 200
    assert json_response.status_code == 200
    for forbidden in (
        "raw-player-secret",
        "raw-source-secret",
        "raw-roster-secret",
        "198.51.100.9",
        "/home/deus/name.log",
        "/home/deus/source.log",
    ):
        assert forbidden not in rendered
    assert "Alpha token=***" in rendered
    assert not (tmp_path / "default" / "players.db").exists()


def test_player_get_routes_do_not_write_player_sessions(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_live_session_scanner, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Live Alpha", PLAYER_ALPHA_ID)),
    )
    monkeypatch.setattr(
        player_live_session_scanner,
        "scan_live_player_sessions_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("GET routes must not scan live sessions")
        ),
    )
    client = _authed_client(tmp_path, monkeypatch)
    db_path = tmp_path / "default" / "players.db"

    current_response = client.get("/players", follow_redirects=False)
    current_json_response = client.get("/players/current.json", follow_redirects=False)
    known_response = client.get("/players/known", follow_redirects=False)
    history_response = client.get("/players/history", follow_redirects=False)
    sessions_response = client.get("/players/sessions", follow_redirects=False)

    assert current_response.status_code == 200
    assert current_json_response.status_code == 200
    assert known_response.status_code == 200
    assert history_response.status_code == 200
    assert sessions_response.status_code == 200
    assert "Live Alpha" in current_response.text
    assert PLAYER_ALPHA_ID in current_response.text
    assert current_json_response.json()["players"] == [
        {
            "display_name": "Live Alpha",
            "reliable_id": PLAYER_ALPHA_ID,
            "source": "rcon.guid",
        }
    ]
    assert not db_path.exists()
    assert _player_session_count(db_path) == 0


def test_player_get_routes_do_not_close_existing_player_sessions(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry, player_sources

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
        lambda instance: _roster(_current_player("Live Alpha", PLAYER_ALPHA_ID)),
    )
    client = _authed_client(tmp_path, monkeypatch, db_path=db_path)

    current_response = client.get("/players", follow_redirects=False)
    known_response = client.get("/players/known", follow_redirects=False)
    history_response = client.get("/players/history", follow_redirects=False)
    sessions_response = client.get("/players/sessions", follow_redirects=False)
    session = player_registry.get_open_player_session(db_path, PLAYER_ALPHA_ID)

    assert current_response.status_code == 200
    assert known_response.status_code == 200
    assert history_response.status_code == 200
    assert sessions_response.status_code == 200
    assert session is not None
    assert session.status == player_registry.PLAYER_SESSION_STATUS_OPEN
    assert _player_session_count(db_path) == 1


def test_player_sessions_route_is_read_only_and_does_not_load_current_cache(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import (
        player_current_cache,
        player_live_session_scanner,
        player_sources,
    )

    monkeypatch.setattr(
        player_current_cache,
        "load_current_roster_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sessions page must not load current cache")
        ),
    )
    monkeypatch.setattr(
        player_live_session_scanner,
        "scan_live_player_sessions_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sessions page must not scan live sessions")
        ),
    )
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sessions page must not query current roster")
        ),
    )
    client = _authed_client(tmp_path, monkeypatch)
    db_path = tmp_path / "default" / "players.db"

    response = client.get("/players/sessions", follow_redirects=False)

    assert response.status_code == 200
    assert "No player sessions recorded yet." in response.text
    assert not db_path.exists()


def test_player_sessions_route_renders_filtered_sanitized_session_fields(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    raw_path = "/home/deus/armactl-data/default/config/logs/run/console.log"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha token=raw-player-secret 198.51.100.9",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        source_ref=f"{raw_path}:1 token=raw-source-secret",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at="2026-06-16T12:00:00+00:00",
        rpl_identity="42",
        connection_id="conn-17",
        session_player_id="7",
        be_slot="23",
        faction="US",
        side="BLUFOR",
    )
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha Later",
        source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
        observed_at="2026-06-16T12:05:00+00:00",
    )
    player_registry.close_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        close_observed_at="2026-06-16T12:10:00+00:00",
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        source_ref=f"{raw_path}:2 token=raw-close-secret",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_LOW,
        end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    )
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_BRAVO_ID,
        display_name="Bravo One",
        source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
        observed_at="2026-06-16T12:15:00+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get(
        "/players/sessions"
        f"?player_id={PLAYER_ALPHA_ID}"
        "&q=Alpha"
        "&status=closed"
        "&end_reason=stale_absence"
        "&source=scanner.checkpoint"
        "&limit=5",
        follow_redirects=False,
    )

    assert response.status_code == 200
    html = response.text
    assert 'href="/players/sessions">Player Sessions</a>' in html
    assert 'value="closed" selected' in html
    assert 'value="stale_absence" selected' in html
    assert 'value="scanner.checkpoint" selected' in html
    assert "Alpha Later" in html
    assert "Alpha token=*** ***" in html
    assert PLAYER_ALPHA_ID in html
    assert "Bravo One" not in html
    assert "Observed" in html
    assert "Last observed" in html
    assert "Inferred close" in html
    assert "Backend auth" in html
    assert "RCON roster" in html
    assert "Scanner checkpoint" in html
    assert "High" in html
    assert "Medium" in html
    assert "Low" in html
    assert "Stale absence" in html
    for forbidden in (
        raw_path,
        "/home/deus",
        "armactl-data",
        "raw-player-secret",
        "raw-source-secret",
        "raw-close-secret",
        "198.51.100.9",
        "conn-17",
        "rplIdentity",
        "BLUFOR",
        "K/D",
        "Role",
        "Joined",
        "playtime",
        "Discord",
        "ban/kick",
    ):
        assert forbidden not in html


def test_player_history_route_renders_empty_state_and_migrates_v1_db(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

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
            VALUES ('schema_version', '1')
            """
        )
        connection.execute(
            """
            CREATE TABLE players (
                reliable_id TEXT PRIMARY KEY,
                current_name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL,
                last_source TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE player_names (
                reliable_id TEXT NOT NULL,
                name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL,
                PRIMARY KEY (reliable_id, name)
            )
            """
        )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history", follow_redirects=False)

    assert response.status_code == 200
    assert "No player events recorded yet." in response.text
    assert "player_log_events" in _sqlite_tables(db_path)
    assert "player_sessions" in _sqlite_tables(db_path)
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION


def test_player_history_route_renders_stored_rows_without_raw_sources(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    raw_auth_line = (
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha 203.0.113.9"
    )
    parsed_events = [
        _parse_log_event(
            raw_auth_line,
            observed_at="2026-01-01T12:00:01+00:00",
            raw_source_ref=(
                "/home/deus/armactl-data/default/config/logs/run/"
                "console-198.51.100.7.log:203.0.113.9:1"
            ),
        ),
        _parse_log_event(
            "NETWORK : ### Updating player: PlayerId=7, Name=Alpha One, "
            f"rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
            observed_at="2026-01-01T12:00:02+00:00",
            raw_source_ref="journal:update",
        ),
        _parse_log_event(
            "SCRIPT : INFO: Faction: player Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) "
            "has joined faction #US_Army (US)",
            observed_at="2026-01-01T12:00:03+00:00",
            raw_source_ref="journal:faction",
        ),
        _parse_log_event(
            "SCRIPT : INFO: KILL TK: Bravo Two "
            f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
            "at <4 5 6> was killed by Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
            "who was at that time at <4 5 7> [2.2m away from the corpse]. "
            "With last inflicted damage type Bullet to the 'LeftArm' hit zone",
            observed_at="2026-01-01T12:00:04+00:00",
            raw_source_ref="journal:teamkill",
        ),
    ]
    player_registry.ingest_player_log_events(
        db_path,
        parsed_events,
        ingested_at="2026-01-01T12:00:05+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history", follow_redirects=False)

    assert response.status_code == 200
    html = response.text
    assert 'data-local-time datetime="2026-01-01T12:00:' in html
    assert "2026-01-01 12:00 UTC" not in html
    assert "player_authenticated" in html
    assert "player_update" in html
    assert "faction_join" in html
    assert "teamkill" in html
    assert "player-events-table" in html
    assert "player-event-details-row" not in html
    assert "player-event-diagnostics-row" in html
    assert "player-event-details-cell" in html
    assert "player-event-card" not in html
    assert "<table" in html
    assert "<th>Diagnostics</th>" not in html
    assert "Alpha ***" in html
    assert "Alpha One" in html
    assert "Bravo Two" in html
    assert PLAYER_ALPHA_ID in html
    assert PLAYER_BRAVO_ID in html
    assert "US_Army" not in html
    assert "US" in html
    assert "Faction resource" not in html
    assert "session " not in html
    assert "Confidence" not in html
    assert "Diagnostics" in html
    assert "Bullet" in html
    assert "LeftArm" in html
    assert "2.2 m" in html
    assert "console-***.log:***" in html
    assert "/home/deus" not in html
    assert "armactl-data" not in html
    assert "198.51.100.7" not in html
    assert "203.0.113.9" not in html
    assert raw_auth_line not in html


def test_player_history_default_hides_session_evidence_rows(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
                observed_at="2026-01-01T12:00:01+00:00",
                raw_source_ref="journal:alpha-auth",
            ),
            _parse_log_event(
                "RPL : ServerImpl event: disconnected (identity=42), "
                "group=5, reason=Normal",
                observed_at="2026-01-01T12:00:02+00:00",
                raw_source_ref="journal:rpl-disconnect",
            ),
            _parse_log_event(
                "NETWORK : Player disconnected: connectionID=conn-17",
                observed_at="2026-01-01T12:00:03+00:00",
                raw_source_ref="journal:network-disconnect",
            ),
            _parse_log_event(
                "DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started.",
                observed_at="2026-01-01T12:00:04+00:00",
                raw_source_ref="journal:shutdown",
            ),
        ],
        ingested_at="2026-01-01T12:00:05+00:00",
    )
    stored_event_types = [
        event.event_type for event in player_registry.list_player_log_events(db_path)
    ]
    sessionizer_event_types = [
        event.event_type
        for event in player_registry.list_player_log_events_for_sessionization(db_path)
    ]
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history", follow_redirects=False)

    assert response.status_code == 200
    html = response.text
    assert "player_authenticated" in html
    assert "journal:alpha-auth" in html
    assert 'title="server_lifecycle"' not in html
    assert 'title="player_disconnected"' not in html
    assert "journal:rpl-disconnect" not in html
    assert "journal:network-disconnect" not in html
    assert "journal:shutdown" not in html
    assert "Correlation only" not in html
    assert "System" not in html
    assert "server_lifecycle" in stored_event_types
    assert stored_event_types.count("player_disconnected") == 2
    assert "server_lifecycle" in sessionizer_event_types
    assert sessionizer_event_types.count("player_disconnected") == 2


def test_player_history_session_evidence_mode_renders_safe_diagnostics(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    raw_rpl_line = (
        "RPL : ServerImpl event: disconnected (identity=42), group=5, reason=Normal"
    )
    raw_network_line = "NETWORK : Player disconnected: connectionID=conn-17"
    raw_be_line = (
        "DEFAULT : BattlEye Server: "
        + chr(39)
        + "Player #23 Bravo Two disconnected"
        + chr(39)
    )
    raw_lifecycle_line = "DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started."
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                raw_rpl_line,
                observed_at="2026-01-01T12:00:02+00:00",
                raw_source_ref=(
                    "/home/deus/armactl-data/default/config/logs/run/"
                    "console-198.51.100.7.log:203.0.113.9:44"
                ),
            ),
            _parse_log_event(
                raw_network_line,
                observed_at="2026-01-01T12:00:03+00:00",
                raw_source_ref="journal:network-disconnect",
            ),
            _parse_log_event(
                raw_be_line,
                observed_at="2026-01-01T12:00:04+00:00",
                raw_source_ref="journal:be-disconnect",
            ),
            _parse_log_event(
                raw_lifecycle_line,
                observed_at="2026-01-01T12:00:05+00:00",
                raw_source_ref="journal:shutdown",
            ),
        ],
        ingested_at="2026-01-01T12:00:06+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get(
        "/players/history?mode=session_evidence",
        follow_redirects=False,
    )

    assert response.status_code == 200
    html = response.text
    assert 'value="session_evidence" selected' in html
    assert 'title="server_lifecycle"' in html
    assert 'title="player_disconnected"' in html
    assert "System" in html
    assert html.count("Correlation only") == 3
    assert ">Unknown<" not in html
    assert "Unknown player" not in html
    assert "RPL identity" in html
    assert "42" in html
    assert "Connection ID" in html
    assert "conn-17" in html
    assert "BE slot" in html
    assert "23" in html
    assert "console-***.log:***" in html
    assert "/home/deus" not in html
    assert "armactl-data" not in html
    assert "198.51.100.7" not in html
    assert "203.0.113.9" not in html
    assert raw_rpl_line not in html
    assert raw_network_line not in html
    assert raw_be_line not in html
    assert raw_lifecycle_line not in html


def test_player_history_route_filters_query_with_default_player_event_scope(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
                observed_at="2026-01-01T12:00:01+00:00",
                raw_source_ref="journal:alpha-auth",
            ),
            _parse_log_event(
                "SCRIPT : INFO: Faction: player Bravo One "
                f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) "
                "has joined faction #US_Army (US)",
                observed_at="2026-01-01T12:00:03+00:00",
                raw_source_ref="journal:bravo-faction",
            ),
            _parse_log_event(
                "DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started.",
                observed_at="2026-01-01T12:00:04+00:00",
                raw_source_ref="journal:shutdown",
            ),
        ],
        ingested_at="2026-01-01T12:00:05+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history?q=Bravo", follow_redirects=False)

    assert response.status_code == 200
    html = response.text
    assert "faction_join" in html
    assert "Bravo One" in html
    assert "journal:bravo-faction" in html
    assert "journal:alpha-auth" not in html
    assert "title=\"server_lifecycle\"" not in html
    assert "journal:shutdown" not in html


def test_player_history_route_filters_known_event_type(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
                observed_at="2026-01-01T12:00:01+00:00",
                raw_source_ref="journal:alpha-auth",
            ),
            _parse_log_event(
                "SCRIPT : INFO: Faction: player Alpha One "
                f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) "
                "has joined faction #US_Army (US)",
                observed_at="2026-01-01T12:00:03+00:00",
                raw_source_ref="journal:faction",
            ),
        ],
        ingested_at="2026-01-01T12:00:05+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history?event_type=faction_join", follow_redirects=False)

    assert response.status_code == 200
    assert "faction_join" in response.text
    assert "journal:faction" in response.text
    assert "journal:alpha-auth" not in response.text
    assert 'value="faction_join" selected' in response.text


def test_player_history_route_ignores_unknown_event_type_filter(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
                observed_at="2026-01-01T12:00:01+00:00",
                raw_source_ref="journal:alpha-auth",
            )
        ],
        ingested_at="2026-01-01T12:00:05+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history?event_type=not-real", follow_redirects=False)

    assert response.status_code == 200
    assert "player_authenticated" in response.text
    assert "not-real" not in response.text


def test_players_refresh_requires_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_current_refresh, player_registry

    db_path = tmp_path / "default" / "players.db"
    client = _authed_client(tmp_path, monkeypatch, db_path=db_path)
    monkeypatch.setattr(
        player_current_refresh,
        "request_player_current_refresh_and_start",
        AssertionError,
    )

    response = client.post(
        "/players/refresh-current",
        data={"csrf_token": "wrong-token"},
        follow_redirects=False,
    )
    alias_response = client.post(
        "/players/refresh",
        data={"csrf_token": "wrong-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert alias_response.status_code == 403
    assert alias_response.text == "Invalid CSRF token."
    assert player_registry.list_known_players(db_path) == []


def test_players_refresh_job_records_reliable_current_players(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import get_job, list_recent_jobs, player_current
    from armactl.web.services import player_registry, player_sources

    db_path = tmp_path / "default" / "players.db"
    started_jobs: list[int] = []
    setup_owner_user(tmp_path, "owner", "owner players password")
    monkeypatch.setattr(
        player_registry,
        "player_registry_db_path",
        lambda instance, data_root=None: db_path,
    )
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(
            _current_player("Alpha"),
            _unreliable_current_player(),
        ),
    )
    monkeypatch.setattr(
        player_current,
        "start_player_current_refresh_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", "owner players password")
    csrf_token = _form_token(client.get("/players", follow_redirects=False).text)

    response = client.post(
        "/players/refresh-current",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/players?refresh_current=queued")
    assert len(started_jobs) == 1
    job_id = list_recent_jobs(tmp_path / "web" / "web.db")[0].id
    dispatch = player_current.dispatch_player_current_refresh_job(
        tmp_path / "web" / "web.db",
        job_id,
    )
    job = get_job(tmp_path / "web" / "web.db", job_id)

    assert dispatch.ran is True
    assert job is not None
    assert job.status == "succeeded"
    assert "observed=2" in job.stdout_tail
    assert "stored=1" in job.stdout_tail
    assert "ignored=1" in job.stdout_tail
    known = player_registry.list_known_players(db_path)
    assert [(player.reliable_id, player.current_name) for player in known] == [
        ("ABCDEF1234567890", "Alpha")
    ]


def test_players_refresh_job_uses_runtime_data_root(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.jobs import list_recent_jobs, player_current
    from armactl.web.services import player_registry, player_sources

    setup_owner_user(tmp_path, "owner", "owner players password")
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Alpha")),
    )
    monkeypatch.setattr(
        player_current,
        "start_player_current_refresh_worker",
        lambda db_path, job_id: None,
    )
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", "owner players password")
    csrf_token = _form_token(client.get("/players", follow_redirects=False).text)

    response = client.post(
        "/players/refresh-current",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    job_id = list_recent_jobs(tmp_path / "web" / "web.db")[0].id
    player_current.dispatch_player_current_refresh_job(tmp_path / "web" / "web.db", job_id)

    db_path = tmp_path / "default" / "players.db"
    assert response.status_code == 303
    assert db_path.is_file()
    assert [player.current_name for player in player_registry.list_known_players(db_path)] == [
        "Alpha"
    ]


def test_duplicate_active_player_current_refresh_job_is_deduped(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import list_active_jobs, player_current
    from armactl.web.services import player_current_refresh

    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_current,
        "start_player_current_refresh_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )
    db_path = tmp_path / "web" / "web.db"
    audit_log_path = tmp_path / "logs" / "web" / "audit.log"

    first = player_current_refresh.request_player_current_refresh_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )
    second = player_current_refresh.request_player_current_refresh_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )

    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    assert started_jobs == [first.job.id]
    active_jobs = list_active_jobs(db_path)
    assert [job.id for job in active_jobs] == [first.job.id]
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent", "intent"]
    assert {event["action"] for event in events} == {
        player_current.PLAYER_CURRENT_REFRESH_ACTION
    }


def test_players_refresh_route_queues_background_job_and_notice(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import list_recent_jobs, player_current

    setup_owner_user(tmp_path, "owner", "owner players password")
    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_current,
        "start_player_current_refresh_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", "owner players password")
    page = client.get("/players", follow_redirects=False)
    csrf_token = _form_token(page.text)

    response = client.post(
        "/players/refresh-current",
        data={"csrf_token": csrf_token, "raw_path": str(tmp_path / "secret.log")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/players?refresh_current=queued")
    assert started_jobs == [list_recent_jobs(tmp_path / "web" / "web.db")[0].id]
    notice_page = client.get(response.headers["location"], follow_redirects=False)

    assert notice_page.status_code == 200
    assert "notice-success" in notice_page.text
    assert "Current player refresh queued." in notice_page.text
    assert 'href="/jobs"' in notice_page.text
    assert str(tmp_path / "secret.log") not in notice_page.text
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_refresh_intent_audit_failure_aborts_roster_load_and_registry_write(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_registry, player_sources
    from armactl.web.services.audit import AuditLogError

    calls = {"roster": 0, "registry": 0}

    def fail_intent(*args, **kwargs):
        if (kwargs.get("details") or {}).get("phase") == "intent":
            raise AuditLogError("disk full token=raw-audit-secret")
        raise AssertionError("unexpected outcome audit")

    def load_roster(instance):
        calls["roster"] += 1
        raise AssertionError("roster should not load")

    def record_snapshot(db_path, observations):
        calls["registry"] += 1
        raise AssertionError("registry should not write")

    monkeypatch.setattr(player_actions, "append_audit_event", fail_intent)
    monkeypatch.setattr(player_sources, "load_current_player_roster", load_roster)
    monkeypatch.setattr(
        player_registry,
        "record_current_players_snapshot",
        record_snapshot,
    )

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is False
    assert result.intent_audited is False
    assert result.backend_success is False
    assert result.audit_written is False
    assert result.message == "Player registry refresh was not run because audit logging failed."
    assert "raw-audit-secret" not in result.audit_error
    assert calls == {"roster": 0, "registry": 0}
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_refresh_success_writes_outcome_audit_success(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_registry, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(
            _current_player("Alpha token=raw-player-secret"),
            _unreliable_current_player("Slot token=raw-slot-secret"),
        ),
    )

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is True
    assert result.backend_success is True
    assert result.audit_written is True
    assert result.stored_count == 1
    assert result.ignored_count == 1
    assert result.reliable_count == 1
    assert [
        player.current_name
        for player in player_registry.list_known_players(tmp_path / "default" / "players.db")
    ] == ["Alpha token=***"]
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    outcome = events[1]
    assert outcome["action"] == "players.refresh"
    assert outcome["instance"] == "default"
    assert outcome["success"] is True
    assert outcome["message"] == "Player registry refreshed."
    assert outcome["details"] == {
        "phase": "outcome",
        "action": "players.refresh",
        "instance": "default",
        "source": "rcon.roster",
        "status": "available",
        "observed_count": "2",
        "reliable_count": "1",
        "recorded_count": "1",
        "ignored_count": "1",
    }
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "Alpha" not in audit_text
    assert "raw-player-secret" not in audit_text
    assert "raw-slot-secret" not in audit_text


def test_players_refresh_job_source_failure_writes_safe_failure_audit(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import get_job, player_current
    from armactl.web.services import player_current_refresh, player_sources

    raw_failure = (
        "roster unavailable token=raw-roster-secret from 198.51.100.9 "
        "using /home/deus/projects/armactl/private.log"
    )

    def fail_roster(instance):
        raise RuntimeError(raw_failure)

    monkeypatch.setattr(player_sources, "load_current_player_roster", fail_roster)
    monkeypatch.setattr(
        player_current,
        "start_player_current_refresh_worker",
        lambda db_path, job_id: None,
    )
    db_path = tmp_path / "web" / "web.db"
    queued = player_current_refresh.request_player_current_refresh_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )

    dispatch = player_current.dispatch_player_current_refresh_job(db_path, queued.job.id)
    job = get_job(db_path, queued.job.id)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")

    assert dispatch.ran is True
    assert job is not None
    assert job.status == "failed"
    assert job.error_class == "RuntimeError"
    assert job.error_message == "Player registry refresh failed while loading current players."
    assert "observed=0" in job.stdout_tail
    assert "stored=0" in job.stdout_tail
    assert not (tmp_path / "default" / "players.db").exists()
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    outcome = events[1]
    assert outcome["action"] == player_current.PLAYER_CURRENT_REFRESH_ACTION
    assert outcome["target"] == player_current.PLAYER_CURRENT_REFRESH_JOB_KIND
    assert outcome["success"] is False
    assert outcome["message"] == "Player registry refresh failed while loading current players."
    assert outcome["details"]["observed"] == "0"
    assert outcome["details"]["stored"] == "0"
    assert outcome["details"]["ignored"] == "0"
    assert outcome["details"]["source"] == "unavailable"
    assert outcome["details"]["status"] == "unavailable"
    assert outcome["details"]["reason_class"] == "RuntimeError"
    for rendered in (job.error_message, job.stdout_tail, audit_text):
        assert raw_failure not in rendered
        assert "raw-roster-secret" not in rendered
        assert "198.51.100.9" not in rendered
        assert "/home/deus/projects/armactl/private.log" not in rendered


def test_players_refresh_job_registry_failure_writes_safe_failure_audit(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.jobs import get_job, player_current
    from armactl.web.services import (
        player_current_refresh,
        player_registry,
        player_sources,
    )

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Alpha token=raw-player-secret")),
    )

    def fail_record(db_path, observations, *, observed_at=None):
        tuple(observations)
        raise OSError(
            "write failed token=raw-registry-secret at "
            "/home/deus/projects/armactl/players.db from 198.51.100.10"
        )

    monkeypatch.setattr(
        player_registry,
        "record_current_players_snapshot",
        fail_record,
    )
    monkeypatch.setattr(
        player_current,
        "start_player_current_refresh_worker",
        lambda db_path, job_id: None,
    )
    db_path = tmp_path / "web" / "web.db"
    queued = player_current_refresh.request_player_current_refresh_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )

    player_current.dispatch_player_current_refresh_job(db_path, queued.job.id)
    job = get_job(db_path, queued.job.id)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")

    assert job is not None
    assert job.status == "failed"
    assert job.error_message == "Player registry refresh failed while saving current players."
    assert "observed=1" in job.stdout_tail
    assert "stored=0" in job.stdout_tail
    events = _audit_events(tmp_path)
    outcome = events[1]
    assert outcome["success"] is False
    assert outcome["message"] == "Player registry refresh failed while saving current players."
    assert outcome["details"]["source"] == "rcon.roster"
    assert outcome["details"]["observed"] == "1"
    assert outcome["details"]["stored"] == "0"
    assert outcome["details"]["ignored"] == "0"
    assert outcome["details"]["reason_class"] == "OSError"
    for rendered in (job.error_message, job.stdout_tail, audit_text):
        assert "Alpha" not in rendered
        assert "raw-player-secret" not in rendered
        assert "raw-registry-secret" not in rendered
        assert "198.51.100.10" not in rendered
        assert "/home/deus/projects/armactl/players.db" not in rendered


def test_players_refresh_successful_persist_reports_outcome_audit_failure(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_registry, player_sources
    from armactl.web.services.audit import AuditLogError, append_audit_event

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Alpha token=raw-player-secret")),
    )

    def fail_success_outcome(audit_log_path, **kwargs):
        is_success_outcome = (
            kwargs.get("success") is True
            and (kwargs.get("details") or {}).get("phase") == "outcome"
        )
        if is_success_outcome:
            raise AuditLogError("disk full token=raw-outcome-secret")
        return append_audit_event(audit_log_path, **kwargs)

    monkeypatch.setattr(player_actions, "append_audit_event", fail_success_outcome)

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is False
    assert result.backend_success is True
    assert result.audit_written is False
    assert result.message == "Player registry refreshed, but audit logging failed."
    assert result.stored_count == 1
    assert result.ignored_count == 0
    assert "raw-outcome-secret" not in result.audit_error
    known = player_registry.list_known_players(tmp_path / "default" / "players.db")
    assert [(player.reliable_id, player.current_name) for player in known] == [
        ("ABCDEF1234567890", "Alpha token=***")
    ]
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent"]


def test_players_refresh_failure_outcome_audit_failure_is_controlled(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_sources
    from armactl.web.services.audit import AuditLogError, append_audit_event

    def fail_roster(instance):
        raise RuntimeError("roster unavailable token=raw-roster-secret")

    def fail_failure_outcome(audit_log_path, **kwargs):
        is_failure_outcome = (
            kwargs.get("success") is False
            and (kwargs.get("details") or {}).get("phase") == "outcome"
        )
        if is_failure_outcome:
            raise AuditLogError("disk full token=raw-audit-secret")
        return append_audit_event(audit_log_path, **kwargs)

    monkeypatch.setattr(player_sources, "load_current_player_roster", fail_roster)
    monkeypatch.setattr(player_actions, "append_audit_event", fail_failure_outcome)

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is False
    assert result.backend_success is False
    assert result.audit_written is False
    assert result.message == "Player registry refresh failed, and audit logging also failed."
    assert "raw-audit-secret" not in result.audit_error
    assert "raw-roster-secret" not in result.message
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent"]


def test_players_refresh_audit_details_exclude_display_names_and_raw_secrets(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: CurrentPlayerRoster(
            available=True,
            players=(
                _current_player("Alpha token=raw-player-secret"),
                _unreliable_current_player("Slot token=raw-slot-secret"),
            ),
            total_count=2,
            source="rcon.roster token=raw-source-secret",
            status="available",
            error="",
        ),
    )

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is True
    outcome_details = _audit_events(tmp_path)[1]["details"]
    assert outcome_details == {
        "phase": "outcome",
        "action": "players.refresh",
        "instance": "default",
        "source": "rcon.roster token=***",
        "status": "available",
        "observed_count": "2",
        "reliable_count": "1",
        "recorded_count": "1",
        "ignored_count": "1",
    }
    encoded_details = json.dumps(outcome_details, sort_keys=True)
    assert "Alpha" not in encoded_details
    assert "Slot" not in encoded_details
    assert "raw-player-secret" not in encoded_details
    assert "raw-slot-secret" not in encoded_details
    assert "raw-source-secret" not in encoded_details


def test_player_registry_tests_do_not_use_route_global_monkeypatch_pattern():
    forbidden = (
        "__" + "globals__",
        "endpoint." + "__" + "globals__",
        "app." + "router",
        "dependency_" + "overrides",
    )
    test_root = Path(__file__).parent
    for test_path in test_root.glob("test_*.py"):
        source = test_path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in source, f"{pattern} found in {test_path}"
