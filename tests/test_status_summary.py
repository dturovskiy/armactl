"""Tests for compact config/mod status summaries."""

from __future__ import annotations

from pathlib import Path

from armactl.status_summary import load_status_summaries, summarize_config, summarize_mods


def test_summarize_config_extracts_non_secret_fields() -> None:
    summary = summarize_config(
        {
            "bindPort": 2001,
            "a2s": {"port": 17777},
            "rcon": {"port": 19999, "password": "secret"},
            "game": {
                "name": "Denis Server",
                "scenarioId": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
                "maxPlayers": 64,
                "visible": True,
                "gameProperties": {"battlEye": True},
            },
        }
    )

    assert summary.available is True
    assert summary.server_name == "Denis Server"
    assert summary.scenario_id == "{ECC61978EDCC2B5A}Missions/23_Campaign.conf"
    assert summary.max_players == 64
    assert summary.bind_port == 2001
    assert summary.a2s_port == 17777
    assert summary.rcon_port == 19999
    assert summary.visible is True
    assert summary.battleye is True


def test_summarize_mods_builds_preview_and_remaining_count() -> None:
    summary = summarize_mods(
        {
            "game": {
                "mods": [
                    {"modId": "A1", "name": "Weapons"},
                    {"modId": "B2", "name": "Vehicles"},
                    {"modId": "C3", "name": "Maps"},
                    {"modId": "D4", "name": "QoL"},
                ]
            }
        },
        preview_limit=2,
    )

    assert summary.available is True
    assert summary.count == 4
    assert [entry.label for entry in summary.preview] == [
        "Weapons (A1)",
        "Vehicles (B2)",
    ]
    assert summary.remaining_count == 2


def test_load_status_summaries_returns_unavailable_for_missing_config(tmp_path: Path) -> None:
    config_summary, mods_summary = load_status_summaries(tmp_path / "missing.json")

    assert config_summary.available is False
    assert mods_summary.available is False
