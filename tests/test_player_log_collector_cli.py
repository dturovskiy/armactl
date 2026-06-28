"""Tests for the manual player-history collector CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from armactl.cli import main

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"


def _write_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One\n",
        encoding="utf-8",
    )


def _event_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM player_log_events").fetchone()
    assert row is not None
    return int(row[0])


def test_player_history_collect_cli_defaults_to_json_dry_run(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "console.log"
    data_root = tmp_path / "data"
    _write_log(log_path)

    result = CliRunner().invoke(
        main,
        [
            "--instance",
            "demo",
            "--json-output",
            "player-history",
            "collect",
            "--data-root",
            str(data_root),
            str(log_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["dry_run"] is True
    assert payload["matched_events"] == 1
    assert payload["stored_events"] == 0
    assert not (data_root / "demo" / "players.db").exists()


def test_player_history_collect_cli_write_uses_instance_data_root(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "console.log"
    data_root = tmp_path / "data"
    db_path = data_root / "demo" / "players.db"
    _write_log(log_path)

    result = CliRunner().invoke(
        main,
        [
            "--instance",
            "demo",
            "--json-output",
            "player-history",
            "collect",
            "--write",
            "--data-root",
            str(data_root),
            str(log_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["dry_run"] is False
    assert payload["stored_events"] == 1
    assert db_path.is_file()
    assert _event_count(db_path) == 1


def test_player_history_collect_cli_missing_file_is_controlled(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.log"

    result = CliRunner().invoke(
        main,
        [
            "--json-output",
            "player-history",
            "collect",
            "--data-root",
            str(tmp_path / "data"),
            str(missing_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["error_count"] == 1
    assert payload["errors"][0]["code"] == "missing_file"
    assert "missing.log" in result.output
    assert str(tmp_path) not in result.output
