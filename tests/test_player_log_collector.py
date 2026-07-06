"""Tests for bounded manual player log collection."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from armactl import player_log_events as events
from armactl.player_log_collector import collect_player_log_events

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"


def _fixture_lines() -> list[str]:
    return [
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
        "NETWORK : ### Updating player: PlayerId=7, Name=Alpha One, "
        f"rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
        "SCRIPT : INFO: Faction: player Alpha One "
        f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) "
        "has joined faction #US_Army (US)",
        "SCRIPT : INFO: KILL TK: Bravo Two "
        f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
        "at <4 5 6> was killed by Alpha One "
        f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction. "
        "With last inflicted damage type Bullet to the 'LeftArm' hit zone",
        "DEFAULT : unrelated server message",
    ]


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def _player_rows(db_path: Path) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM players
            ORDER BY reliable_id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _event_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM player_log_events").fetchone()
    assert row is not None
    return int(row[0])


def test_collector_imports_sanitized_auth_update_faction_and_combat_fixture(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    _write_log(log_path, _fixture_lines())

    summary = collect_player_log_events(log_path, db_path, ingested_at="2026-01-01T00:00:00+00:00")

    rows = _event_rows(db_path)
    assert summary.files_requested == 1
    assert summary.files_scanned == 1
    assert summary.lines_scanned == 5
    assert summary.matched_events == 4
    assert summary.stored_events == 4
    assert summary.duplicate_events == 0
    assert summary.unmatched_lines == 1
    assert [row["event_type"] for row in rows] == [
        events.EVENT_TYPE_PLAYER_AUTHENTICATED,
        events.EVENT_TYPE_PLAYER_UPDATE,
        events.EVENT_TYPE_FACTION_JOIN,
        events.EVENT_TYPE_TEAMKILL,
    ]
    assert str(rows[0]["source_ref"]).startswith("console.log:")
    assert str(rows[0]["source_ref"]).endswith(":1")
    assert str(rows[3]["source_ref"]).startswith("console.log:")
    assert str(rows[3]["source_ref"]).endswith(":4")
    assert all("/" not in str(row["source_ref"] or "") for row in rows)
    assert str(tmp_path) not in json.dumps(rows, sort_keys=True)


def test_dry_run_does_not_create_or_modify_db_events(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    _write_log(log_path, _fixture_lines())

    dry_summary = collect_player_log_events(log_path, db_path, dry_run=True)

    assert dry_summary.dry_run is True
    assert dry_summary.matched_events == 4
    assert dry_summary.stored_events == 0
    assert not db_path.exists()

    collect_player_log_events(log_path, db_path)
    before_count = _event_count(db_path)
    dry_again = collect_player_log_events(log_path, db_path, dry_run=True)

    assert dry_again.matched_events == 4
    assert _event_count(db_path) == before_count


def test_duplicate_second_import_is_noop_and_counted(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    _write_log(log_path, _fixture_lines())

    first = collect_player_log_events(log_path, db_path)
    second = collect_player_log_events([log_path], db_path)

    assert first.stored_events == 4
    assert first.duplicate_events == 0
    assert second.stored_events == 0
    assert second.duplicate_events == 4
    assert _event_count(db_path) == 4


def test_missing_and_invalid_files_return_controlled_summary(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.log"
    directory_path = tmp_path / "log-dir"
    directory_path.mkdir()

    summary = collect_player_log_events([missing_path, directory_path], tmp_path / "players.db")

    encoded = json.dumps(summary.to_dict(), sort_keys=True)

    assert summary.files_requested == 2
    assert summary.files_scanned == 0
    assert summary.files_skipped == 2
    assert summary.error_count == 2
    assert {error.code for error in summary.errors} == {"missing_file", "not_file"}
    assert "missing.log" in encoded
    assert "log-dir" in encoded
    assert str(tmp_path) not in encoded


def test_bounded_line_and_byte_behavior(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    _write_log(log_path, _fixture_lines())

    line_limited = collect_player_log_events(
        log_path,
        db_path,
        dry_run=True,
        max_lines=1,
    )

    assert line_limited.files_scanned == 1
    assert line_limited.lines_scanned == 1
    assert line_limited.matched_events == 1
    assert line_limited.files[0].limited is True
    assert line_limited.files[0].limit_reason == "max_lines"
    assert line_limited.skipped_lines == 1
    assert not db_path.exists()

    byte_limited = collect_player_log_events(
        log_path,
        db_path,
        dry_run=True,
        max_bytes=1,
    )

    assert byte_limited.files_scanned == 0
    assert byte_limited.files_skipped == 1
    assert byte_limited.matched_events == 0
    assert byte_limited.errors[0].code == "file_too_large"
    assert not db_path.exists()


def test_binary_file_is_skipped_without_writing(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    log_path.parent.mkdir(parents=True)
    log_path.write_bytes(b"BACKEND : Authenticated player\0not-text")

    summary = collect_player_log_events(log_path, db_path)

    assert summary.files_scanned == 0
    assert summary.files_skipped == 1
    assert summary.errors[0].code == "binary_file"
    assert not db_path.exists()


def test_collector_does_not_store_raw_line_ip_address_or_absolute_path(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "console-198.51.100.7.log"
    db_path = tmp_path / "default" / "players.db"
    raw_line = (
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha 203.0.113.9"
    )
    _write_log(log_path, [raw_line])

    summary = collect_player_log_events(log_path, db_path)
    rows = _event_rows(db_path)
    encoded_rows = json.dumps(rows, sort_keys=True)
    encoded_summary = json.dumps(summary.to_dict(), sort_keys=True)

    assert summary.stored_events == 1
    assert rows[0]["player_name"] == "Alpha ***"
    assert str(rows[0]["source_ref"]).startswith("console-***.log:")
    assert str(rows[0]["source_ref"]).endswith(":1")
    assert raw_line not in encoded_rows
    assert "203.0.113.9" not in encoded_rows
    assert "198.51.100.7" not in encoded_rows
    assert "203.0.113.9" not in encoded_summary
    assert "198.51.100.7" not in encoded_summary
    assert str(tmp_path) not in encoded_rows
    assert str(tmp_path) not in encoded_summary


def test_same_basename_from_different_directories_keeps_distinct_refs(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "default" / "players.db"
    line = (
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One"
    )
    first_log = tmp_path / "run-a" / "console.log"
    second_log = tmp_path / "run-b" / "console.log"
    _write_log(first_log, [line])
    _write_log(second_log, [line])

    summary = collect_player_log_events([first_log, second_log], db_path)
    rows = _event_rows(db_path)

    assert summary.matched_events == 2
    assert summary.stored_events == 2
    assert summary.duplicate_events == 0
    assert len({row["source_ref"] for row in rows}) == 2
    assert all(str(row["source_ref"]).startswith("console.log:") for row in rows)
    assert all("/" not in str(row["source_ref"]) for row in rows)
    assert str(tmp_path) not in json.dumps(rows, sort_keys=True)

def test_collector_derives_occurrence_time_from_dated_log_context(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "2026-07-05-run" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    _write_log(
        log_path,
        [
            "18:31:00.000 BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
            "18:35:00.000 NETWORK : ### Updating player: PlayerId=7, "
            f"Name=Alpha One, rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
        ],
    )

    summary = collect_player_log_events(
        log_path,
        db_path,
        ingested_at="2026-07-06T09:00:00+00:00",
    )

    rows = _event_rows(db_path)
    players = _player_rows(db_path)
    assert summary.stored_events == 2
    assert [row["occurred_at"] for row in rows] == [
        "2026-07-05T18:31:00Z",
        "2026-07-05T18:35:00Z",
    ]
    assert [row["collected_at"] for row in rows] == [
        "2026-07-06T09:00:00+00:00",
        "2026-07-06T09:00:00+00:00",
    ]
    assert {row["time_source"] for row in rows} == {
        events.EVENT_TIME_SOURCE_LOG_PREFIX_WITH_DATE
    }
    assert {row["time_confidence"] for row in rows} == {
        events.EVENT_TIME_CONFIDENCE_DERIVED
    }
    assert players[0]["first_seen_at"] == "2026-07-05T18:31:00Z"
    assert players[0]["last_seen_at"] == "2026-07-05T18:35:00Z"
    assert "2026-07-06T09:00:00" not in {
        players[0]["first_seen_at"],
        players[0]["last_seen_at"],
    }


def test_collector_keeps_time_of_day_without_date_ambiguous(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "run-without-date" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    _write_log(
        log_path,
        [
            "18:31:00.000 BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
        ],
    )

    summary = collect_player_log_events(
        log_path,
        db_path,
        ingested_at="2026-07-06T09:00:00+00:00",
    )

    rows = _event_rows(db_path)
    assert summary.stored_events == 1
    assert rows[0]["occurred_at"] is None
    assert rows[0]["log_timestamp"] == "18:31:00.000"
    assert rows[0]["time_source"] == events.EVENT_TIME_SOURCE_LOG_PREFIX_WITHOUT_DATE
    assert rows[0]["time_confidence"] == events.EVENT_TIME_CONFIDENCE_AMBIGUOUS
    assert _player_rows(db_path) == []


def test_collector_handles_midnight_rollover_from_dated_log_context(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "2026-07-05-run" / "console.log"
    db_path = tmp_path / "default" / "players.db"
    _write_log(
        log_path,
        [
            "23:59:59.000 BACKEND : Authenticated player: "
            f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
            "00:00:02.000 NETWORK : ### Updating player: PlayerId=7, "
            f"Name=Alpha One, rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
        ],
    )

    collect_player_log_events(log_path, db_path)

    rows = _event_rows(db_path)
    assert [row["occurred_at"] for row in rows] == [
        "2026-07-05T23:59:59Z",
        "2026-07-06T00:00:02Z",
    ]
