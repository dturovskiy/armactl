"""Tests for safe Workshop addon cleanup."""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from armactl import addon_cleanup, mods_manager
from armactl.addon_cleanup import (
    CleanupResult,
    cleanup_addons_by_mod_ids,
    cleanup_unconfigured_addons,
    extract_mod_id_from_addon_dir_name,
    is_path_inside,
    resolve_safe_addons_dir,
)
from armactl.config_manager import ConfigError
from armactl.mods import remove_mod_detailed as legacy_remove_mod_detailed
from armactl.mods_manager import remove_mod_detailed, set_mods_detailed


def _write_config(config_path: Path, mods: list[dict[str, str]] | None = None) -> None:
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
            "mods": mods or [],
        },
    }
    config_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")


def _create_addon_dir(addons_dir: Path, name: str, size: int = 512) -> Path:
    addon_dir = addons_dir / name
    addon_dir.mkdir(parents=True, exist_ok=True)
    (addon_dir / "data.bin").write_bytes(b"x" * size)
    return addon_dir


def _symlink_or_skip(target: Path, link: Path, *, target_is_directory: bool = True) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable here: {exc}")


def _enospc_error() -> ConfigError:
    cause = OSError(errno.ENOSPC, "No space left on device")
    error = ConfigError("backup failed")
    error.__cause__ = cause
    return error


def test_extract_mod_id_from_addon_dir_name_valid_names() -> None:
    assert (
        extract_mod_id_from_addon_dir_name("TacticalFlava_5D550926D43F1409")
        == "5D550926D43F1409"
    )
    assert (
        extract_mod_id_from_addon_dir_name("RHS-ContentPack01_1337C0DE5DABBEEF")
        == "1337C0DE5DABBEEF"
    )
    assert extract_mod_id_from_addon_dir_name("5d550926d43f1409") == "5D550926D43F1409"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "random_directory",
        "Mod_5D550926D43F1409_extra",
        "Mod_5D550926D43F1409Z",
        "Mod_GHIJKLMNOPQRSTUV",
        "Mod_5D550926D43F140900",
    ],
)
def test_extract_mod_id_from_addon_dir_name_invalid_names(name: str) -> None:
    assert extract_mod_id_from_addon_dir_name(name) is None


def test_resolve_safe_addons_dir_accepts_canonical_layout(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path)

    assert resolve_safe_addons_dir(config_path) == (
        tmp_path / "instance" / "config" / "addons"
    ).resolve(strict=False)


@pytest.mark.parametrize(
    "config_suffix",
    [
        ("server", "config.json"),
        ("config", "..", "server", "config.json"),
        ("config", "not-config.json"),
    ],
)
def test_invalid_config_paths_do_not_delete_anything(
    tmp_path: Path,
    config_suffix: tuple[str, ...],
) -> None:
    instance = tmp_path / "instance"
    canonical_config = instance / "config" / "config.json"
    invalid_config = instance.joinpath(*config_suffix)
    _write_config(canonical_config)
    _write_config(invalid_config)

    server_addon = _create_addon_dir(
        instance / "server" / "addons",
        "ServerMod_AAAAAAAAAAAAAAAA",
    )
    config_addon = _create_addon_dir(
        instance / "config" / "addons",
        "ConfigMod_AAAAAAAAAAAAAAAA",
    )

    result = cleanup_unconfigured_addons(invalid_config)

    assert result.errors
    assert server_addon.exists()
    assert config_addon.exists()


def test_addons_root_symlink_is_rejected(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    config_path = instance / "config" / "config.json"
    _write_config(config_path)
    server_addons = instance / "server" / "addons"
    _create_addon_dir(server_addons, "ServerMod_AAAAAAAAAAAAAAAA")
    _symlink_or_skip(server_addons, instance / "config" / "addons")

    result = cleanup_unconfigured_addons(config_path)

    assert result.errors
    assert (server_addons / "ServerMod_AAAAAAAAAAAAAAAA").exists()


def test_child_addon_symlink_is_skipped_and_not_followed(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    config_path = instance / "config" / "config.json"
    addons = instance / "config" / "addons"
    _write_config(config_path, [])
    target = _create_addon_dir(instance / "server" / "addons", "Target_AAAAAAAAAAAAAAAA")
    addons.mkdir(parents=True, exist_ok=True)
    _symlink_or_skip(target, addons / "Mod_AAAAAAAAAAAAAAAA")

    result = cleanup_unconfigured_addons(config_path)

    assert result.deleted == []
    assert len(result.skipped) == 1
    assert target.exists()
    assert (target / "data.bin").exists()


def test_is_path_inside_rejects_root_and_outside_paths(tmp_path: Path) -> None:
    root = tmp_path / "instance" / "config" / "addons"
    child = root / "Mod_AAAAAAAAAAAAAAAA"
    outside = tmp_path / "instance" / "server" / "addons"

    assert is_path_inside(child, root)
    assert not is_path_inside(root, root)
    assert not is_path_inside(outside, root)


def test_cleanup_by_mod_ids_deletes_only_matching_dirs(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(config_path)
    removed = _create_addon_dir(addons, "Remove_AAAAAAAAAAAAAAAA", size=1024)
    kept = _create_addon_dir(addons, "Keep_BBBBBBBBBBBBBBBB", size=2048)
    unknown = _create_addon_dir(addons, "UnknownFormat", size=4096)

    result = cleanup_addons_by_mod_ids(config_path, {"aaaaaaaaaaaaaaaa"})

    assert result.deleted == [removed.resolve()]
    assert result.bytes_deleted == 1024
    assert not removed.exists()
    assert kept.exists()
    assert unknown.exists()
    assert result.skipped == [unknown.resolve()]


def test_cleanup_by_mod_ids_skips_invalid_target_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(config_path)
    addon_dir = _create_addon_dir(addons, "Keep_AAAAAAAAAAAAAAAA")

    result = cleanup_addons_by_mod_ids(config_path, {"not-a-mod-id"})

    assert result == CleanupResult()
    assert addon_dir.exists()


def test_cleanup_unconfigured_addons_preserves_active_and_skips_unknown(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(config_path, [{"modId": "AAAAAAAAAAAAAAAA", "name": "Active"}])
    active = _create_addon_dir(addons, "Active_AAAAAAAAAAAAAAAA")
    stale = _create_addon_dir(addons, "Stale_BBBBBBBBBBBBBBBB")
    unknown = _create_addon_dir(addons, "UnknownFormat")

    result = cleanup_unconfigured_addons(config_path)

    assert result.deleted == [stale.resolve()]
    assert active.exists()
    assert not stale.exists()
    assert unknown.exists()
    assert result.skipped == [unknown.resolve()]


def test_dry_run_reports_deletions_without_removing(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(config_path, [])
    stale = _create_addon_dir(addons, "Stale_AAAAAAAAAAAAAAAA", size=1234)

    result = cleanup_unconfigured_addons(config_path, dry_run=True)

    assert result.deleted == [stale.resolve()]
    assert result.bytes_deleted == 1234
    assert stale.exists()


def test_legacy_remove_mod_keeps_addon_if_case_variant_duplicate_remains(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(
        config_path,
        [
            {"modId": "aaaaaaaaaaaaaaaa", "name": "lower"},
            {"modId": "AAAAAAAAAAAAAAAA", "name": "upper"},
        ],
    )
    addon_dir = _create_addon_dir(addons, "Mod_AAAAAAAAAAAAAAAA")

    result = legacy_remove_mod_detailed(config_path, "AAAAAAAAAAAAAAAA")

    assert result.config_changed
    assert result.removed_ids == set()
    assert result.cleanup_result == CleanupResult()
    assert addon_dir.exists()


def test_single_mod_removal_does_not_delete_unrelated_stale_addons(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(
        config_path,
        [
            {"modId": "AAAAAAAAAAAAAAAA", "name": "Remove"},
            {"modId": "BBBBBBBBBBBBBBBB", "name": "Keep"},
        ],
    )
    removed = _create_addon_dir(addons, "Remove_AAAAAAAAAAAAAAAA")
    kept = _create_addon_dir(addons, "Keep_BBBBBBBBBBBBBBBB")
    unrelated_stale = _create_addon_dir(addons, "Stale_CCCCCCCCCCCCCCCC")

    result = remove_mod_detailed(config_path, "AAAAAAAAAAAAAAAA")

    assert result.config_changed
    assert result.cleanup_result is not None
    assert result.cleanup_result.deleted == [removed.resolve()]
    assert not removed.exists()
    assert kept.exists()
    assert unrelated_stale.exists()


def test_set_mods_handles_enospc_by_cleaning_removed_ids_and_retrying_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(
        config_path,
        [
            {"modId": "AAAAAAAAAAAAAAAA", "name": "Remove"},
            {"modId": "BBBBBBBBBBBBBBBB", "name": "Keep"},
        ],
    )
    removed = _create_addon_dir(addons, "Remove_AAAAAAAAAAAAAAAA")
    kept = _create_addon_dir(addons, "Keep_BBBBBBBBBBBBBBBB")
    unrelated_stale = _create_addon_dir(addons, "Stale_CCCCCCCCCCCCCCCC")

    real_save_config = mods_manager.save_config
    calls = 0

    def flaky_save_config(path: Path | str, data: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _enospc_error()
        real_save_config(path, data, backup=False)

    monkeypatch.setattr(mods_manager, "save_config", flaky_save_config)

    result = set_mods_detailed(config_path, [{"modId": "BBBBBBBBBBBBBBBB", "name": "Keep"}])

    assert calls == 2
    assert result.enospc_retry_performed
    assert result.removed_ids == {"AAAAAAAAAAAAAAAA"}
    assert result.cleanup_result is not None
    assert result.cleanup_result.deleted == [removed.resolve()]
    assert not removed.exists()
    assert kept.exists()
    assert unrelated_stale.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["mods"] == [{"modId": "BBBBBBBBBBBBBBBB", "name": "Keep"}]


def test_cleanup_errors_are_returned_to_mod_update_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    addons = tmp_path / "instance" / "config" / "addons"
    _write_config(config_path, [{"modId": "AAAAAAAAAAAAAAAA", "name": "Remove"}])
    addon_dir = _create_addon_dir(addons, "Remove_AAAAAAAAAAAAAAAA")

    def fail_rmtree(path: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(addon_cleanup.shutil, "rmtree", fail_rmtree)

    result = remove_mod_detailed(config_path, "AAAAAAAAAAAAAAAA")

    assert result.config_changed
    assert result.cleanup_result is not None
    assert result.cleanup_result.deleted == []
    assert result.cleanup_result.errors
    assert addon_dir.exists()
