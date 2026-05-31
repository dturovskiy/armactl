"""Regression tests for disabled Workshop mod sidecar handling."""
from __future__ import annotations

import json
from pathlib import Path

from armactl.addon_cleanup import cleanup_unconfigured_addons
from armactl.mods_manager import dedupe_mods, disable_mod, enable_mod, import_mods_detailed
from armactl.mods_state import load_disabled_mods, save_disabled_mods


def _write_config(
    config_path: Path,
    *,
    mods: list[dict[str, str]] | None = None,
    disabled_mods: list[dict[str, str]] | None = None,
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "game": {
            "name": "Test",
            "scenarioId": "test",
            "maxPlayers": 16,
            "mods": mods or [],
        },
    }
    config_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")
    if disabled_mods is not None:
        save_disabled_mods(config_path, disabled_mods)


def _create_addon_dir(addons: Path, name: str) -> Path:
    destination = addons / name
    destination.mkdir(parents=True)
    (destination / "payload.bin").write_bytes(b"payload")
    return destination


def test_disabled_mod_is_kept_by_unused_addon_cleanup(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(
        config_path,
        disabled_mods=[{"modId": "AAAAAAAAAAAAAAAA", "name": "Disabled"}],
    )
    addon_dir = _create_addon_dir(addons, "Disabled_AAAAAAAAAAAAAAAA")

    result = cleanup_unconfigured_addons(config_path)

    assert result.deleted == []
    assert addon_dir.exists()


def test_disable_and_enable_mod_preserve_local_addon(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(config_path, mods=[{"modId": "AAAAAAAAAAAAAAAA", "name": "Toggle"}])
    addon_dir = _create_addon_dir(addons, "Toggle_AAAAAAAAAAAAAAAA")

    assert disable_mod(config_path, "aaaaaaaaaaaaaaaa")
    assert addon_dir.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "disabledMods" not in saved["game"]
    assert load_disabled_mods(config_path) == [{"modId": "AAAAAAAAAAAAAAAA", "name": "Toggle"}]

    assert enable_mod(config_path, "AAAAAAAAAAAAAAAA")
    assert addon_dir.exists()
    assert load_disabled_mods(config_path) == []


def test_dedupe_prefers_active_mod_over_disabled_duplicate(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(
        config_path,
        mods=[{"modId": "AAAAAAAAAAAAAAAA", "name": "Active"}],
        disabled_mods=[
            {"modId": "aaaaaaaaaaaaaaaa", "name": "Duplicate"},
            {"modId": "BBBBBBBBBBBBBBBB", "name": "Disabled"},
        ],
    )

    assert dedupe_mods(config_path) == 1
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["mods"] == [{"modId": "AAAAAAAAAAAAAAAA", "name": "Active"}]
    assert "disabledMods" not in saved["game"]
    assert load_disabled_mods(config_path) == [{"modId": "BBBBBBBBBBBBBBBB", "name": "Disabled"}]


def test_import_reactivates_disabled_mod(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    import_path = tmp_path / "mods.json"
    _write_config(
        config_path,
        disabled_mods=[{"modId": "AAAAAAAAAAAAAAAA", "name": "Disabled"}],
    )
    import_path.write_text(
        json.dumps([{"modId": "aaaaaaaaaaaaaaaa", "name": "Imported"}]),
        encoding="utf-8",
    )

    added, skipped, _result = import_mods_detailed(config_path, import_path)

    assert (added, skipped) == (1, 0)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["mods"] == [
        {"modId": "aaaaaaaaaaaaaaaa", "name": "Imported", "version": ""}
    ]
    assert "disabledMods" not in saved["game"]
    assert load_disabled_mods(config_path) == []
