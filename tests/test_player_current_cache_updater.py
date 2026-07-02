"""Tests for automatic current-roster cache updater foundation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from armactl.cli import main
from armactl.web.services.player_sources import CurrentPlayer, CurrentPlayerRoster

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"


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
        observed_count=len(players),
        count_source="rcon",
        roster_available=True,
        roster_configured=True,
    )


def _cache_table_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _cache_table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _cache_db_text(db_path: Path) -> str:
    values: list[str] = []
    with sqlite3.connect(db_path) as connection:
        for table in ("web_current_roster_cache", "web_current_roster_cache_players"):
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            values.extend(str(value) for row in rows for value in row)
    return " ".join(values)


def test_persistent_current_roster_cache_read_write_is_idempotent(tmp_path: Path):
    from armactl.web.services import player_current_cache

    snapshot = player_current_cache.CurrentRosterSnapshot(
        instance="default",
        players=(
            player_current_cache.CurrentRosterPlayerSnapshot(
                display_name="Alpha",
                reliable_id=PLAYER_ALPHA_ID,
                source="rcon.guid",
            ),
        ),
        source="rcon.roster",
        status="available",
        error="",
        collected_at="2026-06-16T12:00:00+00:00",
        observed_count=2,
        count_source="rcon",
        roster_available=True,
        roster_configured=True,
    )

    player_current_cache.store_persistent_current_roster_snapshot(
        snapshot,
        data_root=tmp_path,
    )
    player_current_cache.store_persistent_current_roster_snapshot(
        snapshot,
        data_root=tmp_path,
    )
    loaded = player_current_cache.get_persistent_current_roster_snapshot(
        "default",
        data_root=tmp_path,
    )
    db_path = tmp_path / "web" / "web.db"

    assert loaded is not None
    assert loaded.instance == "default"
    assert loaded.source == "rcon.roster"
    assert loaded.status == "available"
    assert loaded.observed_count == 2
    assert loaded.total_count == 2
    assert loaded.count_source == "rcon"
    assert loaded.roster_available is True
    assert loaded.roster_configured is True
    assert loaded.collected_at == "2026-06-16T12:00:00+00:00"
    assert loaded.updated_at
    assert [player.display_name for player in loaded.players] == ["Alpha"]
    assert _cache_table_count(db_path, "web_current_roster_cache") == 1
    assert _cache_table_count(db_path, "web_current_roster_cache_players") == 1


def test_persistent_current_roster_cache_migrates_v11_count_fields(tmp_path: Path):
    from armactl.web.runtime import ensure_web_db
    from armactl.web.runtime.db import WEB_SCHEMA_VERSION
    from armactl.web.services import player_current_cache

    db_path = tmp_path / "web" / "web.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE web_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_schema_meta(key, value)
            VALUES ('schema_version', '11')
            """
        )
        connection.execute(
            """
            CREATE TABLE web_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                instance TEXT NOT NULL DEFAULT 'default',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE web_current_roster_cache (
                instance TEXT PRIMARY KEY CHECK(length(trim(instance)) > 0),
                collected_at TEXT NOT NULL CHECK(length(collected_at) > 0),
                updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE web_current_roster_cache_players (
                instance TEXT NOT NULL CHECK(length(trim(instance)) > 0),
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                display_name TEXT NOT NULL DEFAULT '',
                reliable_id TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(instance, ordinal)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_current_roster_cache(
                instance, collected_at, updated_at, source, status, error
            )
            VALUES (
                'default', '2026-06-16T12:00:00+00:00',
                '2026-06-16T12:00:01+00:00', 'rcon.roster', 'available', ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_current_roster_cache_players(
                instance, ordinal, display_name, reliable_id, source
            )
            VALUES ('default', 0, 'Migrated Alpha', ?, 'rcon.guid')
            """,
            (PLAYER_ALPHA_ID,),
        )

    ensure_web_db(db_path)
    loaded = player_current_cache.get_persistent_current_roster_snapshot(
        "default",
        data_root=tmp_path,
    )

    assert "observed_count" in _cache_table_columns(db_path, "web_current_roster_cache")
    assert "count_source" in _cache_table_columns(db_path, "web_current_roster_cache")
    assert loaded is not None
    assert loaded.observed_count == 1
    assert loaded.total_count == 1
    assert loaded.count_source == "unknown"
    assert loaded.roster_available is True
    assert [player.display_name for player in loaded.players] == ["Migrated Alpha"]
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM web_schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row == (WEB_SCHEMA_VERSION,)


def test_persistent_current_roster_cache_stores_no_raw_path_ip_or_secret(
    tmp_path: Path,
):
    from armactl.web.services import player_current_cache

    player_current_cache.store_persistent_current_roster_snapshot(
        player_current_cache.CurrentRosterSnapshot(
            instance="default",
            players=(
                player_current_cache.CurrentRosterPlayerSnapshot(
                    display_name=(
                        "Alpha token=raw-player-secret 198.51.100.9 "
                        "/home/deus/name.log"
                    ),
                    reliable_id=PLAYER_ALPHA_ID,
                    source="/home/deus/source.log token=raw-source-secret",
                ),
            ),
            source="rcon.roster token=raw-roster-secret",
            status="available",
            error="password=raw-password-secret /home/deus/error.log",
            collected_at="2026-06-16T12:00:00+00:00",
        ),
        data_root=tmp_path,
    )
    stored_text = _cache_db_text(tmp_path / "web" / "web.db")

    for forbidden in (
        "raw-player-secret",
        "raw-source-secret",
        "raw-roster-secret",
        "raw-password-secret",
        "198.51.100.9",
        "/home/deus/name.log",
        "/home/deus/source.log",
        "/home/deus/error.log",
    ):
        assert forbidden not in stored_text


def test_players_current_cache_run_once_writes_only_current_roster_cache(
    tmp_path: Path,
    monkeypatch,
):
    from armactl import player_current_cache_updater
    from armactl.web.services import player_live_session_scanner

    monkeypatch.setattr(
        player_current_cache_updater.player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("CLI Alpha", PLAYER_ALPHA_ID)),
    )
    monkeypatch.setattr(
        player_live_session_scanner,
        "scan_live_player_sessions_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("current-cache updater must not scan sessions")
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "players",
            "current-cache",
            "run",
            "--once",
            "--data-root",
            str(tmp_path),
        ],
    )
    web_db = tmp_path / "web" / "web.db"
    players_db = tmp_path / "default" / "players.db"

    assert result.exit_code == 0
    assert "Current roster cache updated" in result.output
    assert web_db.exists()
    assert _cache_table_count(web_db, "web_current_roster_cache") == 1
    assert _cache_table_count(web_db, "web_current_roster_cache_players") == 1
    assert not players_db.exists()


def test_players_current_cache_run_once_redacts_source_errors_and_storage(
    tmp_path: Path,
    monkeypatch,
):
    from armactl import player_current_cache_updater

    def fail_roster(instance):
        raise RuntimeError(
            "failed password=raw-password-secret token=raw-token-secret "
            "from 198.51.100.9 using /home/deus/private.log"
        )

    monkeypatch.setattr(
        player_current_cache_updater.player_sources,
        "load_current_player_roster",
        fail_roster,
    )

    result = CliRunner().invoke(
        main,
        [
            "players",
            "current-cache",
            "run",
            "--once",
            "--data-root",
            str(tmp_path),
        ],
    )
    combined = result.output
    stored_text = _cache_db_text(tmp_path / "web" / "web.db")

    assert result.exit_code == 1
    assert "Traceback" not in combined
    for forbidden in (
        "raw-password-secret",
        "raw-token-secret",
        "198.51.100.9",
        "/home/deus/private.log",
    ):
        assert forbidden not in combined
        assert forbidden not in stored_text


def test_current_roster_cache_loop_retries_with_redacted_warning(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    from armactl import player_current_cache_updater

    calls: list[str] = []
    sleeps: list[int] = []

    def load_roster(instance):
        calls.append(instance)
        if len(calls) == 1:
            raise RuntimeError(
                "failed token=raw-loop-secret from 198.51.100.9 "
                "at /home/deus/loop.log"
            )
        return _roster(_current_player("Loop Alpha", PLAYER_BRAVO_ID))

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise RuntimeError("stop-loop")

    monkeypatch.setattr(
        player_current_cache_updater.player_sources,
        "load_current_player_roster",
        load_roster,
    )
    monkeypatch.setattr(player_current_cache_updater.time, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        player_current_cache_updater.run_current_roster_cache_updater(
            "default",
            data_root=tmp_path,
        )
    captured = capsys.readouterr()

    assert calls == ["default", "default"]
    assert sleeps[:2] == [60, 60]
    assert "retrying in 60s" in captured.err
    assert "Traceback" not in captured.err
    for forbidden in ("raw-loop-secret", "198.51.100.9", "/home/deus/loop.log"):
        assert forbidden not in captured.err
