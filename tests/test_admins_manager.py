"""Tests for Arma Reforger admin identity config management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from armactl.admins_manager import add_admin, get_admins, remove_admin
from armactl.config_manager import ConfigError


def _write_config(config_path: Path, admins: list[object] | None = None) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "publicAddress": "",
        "publicPort": 2001,
        "game": {
            "name": "Test Server",
            "scenarioId": "{TEST}Missions/Test.conf",
            "maxPlayers": 64,
            "admins": admins or [],
            "mods": [],
        },
    }
    config_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")


def test_get_admins_returns_tui_entries_from_server_string_schema(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "config.json"
    _write_config(config_path, ["76561198200329058"])

    assert get_admins(config_path) == [{"identityId": "76561198200329058"}]


def test_add_admin_saves_official_game_admins_string_schema(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "config.json"
    _write_config(config_path)

    assert add_admin(config_path, "aaaaaaaaaaaaaaaa", "Ignored note")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == ["AAAAAAAAAAAAAAAA"]


def test_add_admin_migrates_legacy_object_entries_to_strings(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "config.json"
    _write_config(
        config_path,
        [{"identityId": "aaaaaaaaaaaaaaaa", "name": "Legacy note"}],
    )

    assert not add_admin(config_path, "AAAAAAAAAAAAAAAA")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == ["AAAAAAAAAAAAAAAA"]


def test_remove_admin_deletes_configured_identity(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "config.json"
    _write_config(config_path, ["AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB"])

    assert remove_admin(config_path, "aaaaaaaaaaaaaaaa")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == ["BBBBBBBBBBBBBBBB"]


def test_add_admin_enforces_server_unique_admin_limit(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "config.json"
    admins = [f"{index:016X}" for index in range(20)]
    _write_config(config_path, admins)

    with pytest.raises(ConfigError):
        add_admin(config_path, "FFFFFFFFFFFFFFFF")
