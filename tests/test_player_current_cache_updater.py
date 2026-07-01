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
    )


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
    assert loaded.collected_at == "2026-06-16T12:00:00+00:00"
    assert loaded.updated_at
    assert [player.display_name for player in loaded.players] == ["Alpha"]
    assert _cache_table_count(db_path, "web_current_roster_cache") == 1
    assert _cache_table_count(db_path, "web_current_roster_cache_players") == 1


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

    monkeypatch.setattr(
        player_current_cache_updater.player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("CLI Alpha", PLAYER_ALPHA_ID)),
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
            interval_seconds=10,
            data_root=tmp_path,
        )
    captured = capsys.readouterr()

    assert calls == ["default", "default"]
    assert sleeps[:2] == [10, 10]
    assert "retrying in 10s" in captured.err
    assert "Traceback" not in captured.err
    for forbidden in ("raw-loop-secret", "198.51.100.9", "/home/deus/loop.log"):
        assert forbidden not in captured.err
