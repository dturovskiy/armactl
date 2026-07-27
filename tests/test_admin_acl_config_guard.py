"""Ensure raw config editing cannot bypass synchronized admin mutations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from armactl.web.services import config_edit


def test_raw_config_replacement_rejects_game_admin_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    original = {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "game": {
            "name": "Test Server",
            "scenarioId": "{TEST}Missions/Test.conf",
            "maxPlayers": 64,
            "mods": [],
            "admins": ["21761a7f-c9b4-4bff-8375-b4b43abb95ec"],
        },
    }
    config_path.write_text(json.dumps(original, indent=4), encoding="utf-8")
    submitted = json.loads(json.dumps(original))
    submitted["game"]["admins"].append(
        "0109fcf5-1111-2222-3333-444444449797"
    )

    with pytest.raises(config_edit.ConfigEditError) as raised:
        config_edit.validate_raw_config_replacement(
            config_path,
            json.dumps(submitted),
        )

    assert "Admins workflow" in str(raised.value)
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
