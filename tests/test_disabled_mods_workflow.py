"""Regression tests for disabled Workshop mod sidecar handling."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import armactl.mods_manager as mods_manager
from armactl.addon_cleanup import cleanup_unconfigured_addons
from armactl.config_manager import ConfigError
from armactl.mods_diagnostics import collect_mod_diagnostics
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


def test_disable_mod_rolls_back_sidecar_when_config_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    active = [{"modId": "AAAAAAAAAAAAAAAA", "name": "Toggle"}]
    _write_config(config_path, mods=active)

    def fail_save_config(*args, **kwargs) -> None:
        raise ConfigError("config save failed")

    monkeypatch.setattr(mods_manager, "save_config", fail_save_config)

    with pytest.raises(ConfigError, match="config save failed"):
        disable_mod(config_path, "AAAAAAAAAAAAAAAA")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["mods"] == active
    assert load_disabled_mods(config_path) == []


def test_enable_mod_rolls_back_config_when_sidecar_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    disabled = [{"modId": "AAAAAAAAAAAAAAAA", "name": "Toggle"}]
    _write_config(config_path, disabled_mods=disabled)

    def fail_save_disabled_mods(*args, **kwargs) -> None:
        raise ConfigError("sidecar save failed")

    monkeypatch.setattr(mods_manager, "save_disabled_mods", fail_save_disabled_mods)

    with pytest.raises(ConfigError, match="sidecar save failed"):
        enable_mod(config_path, "AAAAAAAAAAAAAAAA")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["mods"] == []
    assert load_disabled_mods(config_path) == disabled


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
        {"modId": "AAAAAAAAAAAAAAAA", "name": "Imported", "version": ""}
    ]
    assert "disabledMods" not in saved["game"]
    assert load_disabled_mods(config_path) == []


def test_disabled_mod_diagnostics_active_111_disabled_2_without_overlap_ok(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    active_mods = [
        {"modId": f"{index:016X}", "name": f"Active {index}"}
        for index in range(1, 112)
    ]
    disabled_mods = [
        {"modId": "667B230F9505C8BA", "name": "ACE Weather Dev"},
        {"modId": "65AD7C75826B46C6", "name": "ACE Radio Dev"},
    ]
    _write_config(config_path, mods=active_mods, disabled_mods=disabled_mods)

    diagnostics = collect_mod_diagnostics(config_path)

    assert diagnostics.active_config_count == 111
    assert diagnostics.disabled_sidecar_count == 2
    assert diagnostics.overlap_active_disabled == ()
    assert diagnostics.warnings == ()


def test_disabled_mod_diagnostics_warns_on_active_disabled_overlap(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(
        config_path,
        mods=[{"modId": "AAAAAAAAAAAAAAAA", "name": "Active"}],
        disabled_mods=[{"modId": "aaaaaaaaaaaaaaaa", "name": "Disabled duplicate"}],
    )

    diagnostics = collect_mod_diagnostics(config_path)

    assert [item.mod_id for item in diagnostics.overlap_active_disabled] == [
        "AAAAAAAAAAAAAAAA"
    ]
    assert [item.code for item in diagnostics.warnings] == ["active_disabled_overlap"]


def test_disabled_mod_diagnostics_warns_on_profile_settings_module_reference(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    settings_path = (
        config_path.parent / "profile" / ".save" / "settings" / "ReforgerGameSettings.conf"
    )
    _write_config(
        config_path,
        disabled_mods=[{"modId": "65AD7C75826B46C6", "name": "ACE Radio Dev"}],
    )
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        "GameSettings {\n ACE_Radio_SettingsModule {}\n}\n",
        encoding="utf-8",
    )

    diagnostics = collect_mod_diagnostics(config_path)

    assert [item.code for item in diagnostics.warnings] == [
        "disabled_profile_settings_reference"
    ]
    assert diagnostics.stale_profile_settings_references[0].module_name == (
        "ACE_Radio_SettingsModule"
    )
    assert diagnostics.stale_profile_settings_references[0].source == (
        "profile/.save/settings/ReforgerGameSettings.conf"
    )
    assert str(tmp_path) not in diagnostics.stale_profile_settings_references[0].source


def test_disabled_mod_diagnostics_treats_disabled_addon_dir_as_info(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(
        config_path,
        disabled_mods=[{"modId": "65AD7C75826B46C6", "name": "ACE Radio Dev"}],
    )
    _create_addon_dir(addons, "ACE_Radio_65AD7C75826B46C6")

    diagnostics = collect_mod_diagnostics(config_path)

    assert diagnostics.active_config_count == 0
    assert diagnostics.installed_addon_dirs_count == 1
    assert diagnostics.disabled_addon_dirs_present[0].addon_dir == (
        "ACE_Radio_65AD7C75826B46C6"
    )
    assert [item.code for item in diagnostics.info] == ["disabled_addon_dir_present"]
    assert diagnostics.warnings == ()
