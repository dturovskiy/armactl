"""Regression tests for server-facing config validation and sidecar migration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import armactl.mods_state as mods_state
from armactl.config_manager import ConfigError, save_config, validate_config
from armactl.mods_state import load_disabled_mods, migrate_legacy_disabled_mods


def _config() -> dict[str, object]:
    return {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "game": {
            "name": "Test",
            "scenarioId": "{TEST}Missions/Test.conf",
            "maxPlayers": 16,
            "mods": [],
        },
    }


def test_validation_rejects_armactl_metadata_in_server_config() -> None:
    data = _config()
    data["game"]["disabledMods"] = []  # type: ignore[index]
    errors = validate_config(data=data)
    assert any("game.disabledMods" in error for error in errors)


def test_save_config_refuses_invalid_server_config(tmp_path: Path) -> None:
    path = tmp_path / "instance" / "config" / "config.json"
    path.parent.mkdir(parents=True)
    data = _config()
    data["game"]["disabledMods"] = []  # type: ignore[index]
    with pytest.raises(ConfigError, match="Refusing to save invalid config"):
        save_config(path, data)


def test_migration_moves_legacy_metadata_to_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "instance" / "config" / "config.json"
    path.parent.mkdir(parents=True)
    data = _config()
    data["game"]["disabledMods"] = [{"modId": "AAAAAAAAAAAAAAAA", "name": "Off"}]  # type: ignore[index]
    path.write_text(json.dumps(data), encoding="utf-8")

    result = migrate_legacy_disabled_mods(path)

    assert result.migrated
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "disabledMods" not in saved["game"]
    assert load_disabled_mods(path) == [{"modId": "AAAAAAAAAAAAAAAA", "name": "Off"}]


def test_migration_rolls_back_sidecar_when_config_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "instance" / "config" / "config.json"
    path.parent.mkdir(parents=True)
    data = _config()
    data["game"]["disabledMods"] = [{"modId": "AAAAAAAAAAAAAAAA", "name": "Off"}]  # type: ignore[index]
    path.write_text(json.dumps(data), encoding="utf-8")

    def fail_save_config(*args, **kwargs) -> None:
        raise ConfigError("config save failed")

    monkeypatch.setattr(mods_state, "save_config", fail_save_config)

    with pytest.raises(ConfigError, match="config save failed"):
        migrate_legacy_disabled_mods(path)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["game"]["disabledMods"] == [{"modId": "AAAAAAAAAAAAAAAA", "name": "Off"}]
    assert load_disabled_mods(path) == []
