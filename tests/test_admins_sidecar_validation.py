"""Validation regression tests for official game.admins schema."""
from __future__ import annotations

from armactl.config_manager import validate_config


def _config(admins):
    return {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "publicPort": 2001,
        "game": {
            "name": "Test",
            "scenarioId": "{TEST}Missions/Test.conf",
            "maxPlayers": 16,
            "mods": [],
            "admins": admins,
        },
    }


def test_game_admins_string_ids_are_allowed() -> None:
    assert validate_config(data=_config(["76561198200329058"])) == []


def test_game_admins_object_entries_are_rejected() -> None:
    errors = validate_config(data=_config([{"identityId": "76561198200329058"}]))
    assert any("game.admins[0]" in error for error in errors)


def test_game_admins_duplicates_are_rejected() -> None:
    errors = validate_config(data=_config(["76561198200329058", "76561198200329058"]))
    assert any("Duplicate admin ID" in error for error in errors)


def test_game_admins_is_limited_to_twenty_unique_ids() -> None:
    errors = validate_config(data=_config([str(76561198200329000 + index) for index in range(21)]))
    assert any("at most 20" in error for error in errors)
