"""Regression tests for canonical game-admin ACL synchronization."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from armactl import admin_acl_sync

ADMIN_UUID = "21761a7f-c9b4-4bff-8375-b4b43abb95ec"
SECOND_UUID = "0109fcf5-1111-2222-3333-444444449797"
STALE_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
STEAM_ID = "76561198000000001"


def _write_config(config_path: Path, admins: list[str]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "bindAddress": "0.0.0.0",
                "bindPort": 2001,
                "game": {
                    "name": "Test Server",
                    "scenarioId": "{TEST}Missions/Test.conf",
                    "maxPlayers": 64,
                    "mods": [],
                    "admins": admins,
                },
            },
            indent=4,
        ),
        encoding="utf-8",
    )


def _write_mod_acls(
    config_path: Path,
    *,
    sat_admins: list[str] | None = None,
    wcs_admins: list[str] | None = None,
) -> tuple[Path, Path]:
    profile = config_path.parent / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    sat_path = profile / "ServerAdminTools_Config.json"
    wcs_path = profile / "WCS_Admin.json"
    sat_path.write_text(
        json.dumps(
            {
                "admins": sat_admins if sat_admins is not None else [STALE_UUID],
                "gameMasters": sat_admins if sat_admins is not None else [STALE_UUID],
                "bans": ["keep-ban"],
                "messages": {"welcome": "keep"},
            },
            indent=4,
        ),
        encoding="utf-8",
    )
    wcs_path.write_text(
        json.dumps(
            {
                "gameMaster": wcs_admins if wcs_admins is not None else [STALE_UUID],
                "Team": ["developer-only"],
                "other": {"keep": True},
            },
            indent=4,
        ),
        encoding="utf-8",
    )
    return sat_path, wcs_path


def _game_admins(config_path: Path) -> list[str]:
    return json.loads(config_path.read_text(encoding="utf-8"))["game"]["admins"]


def test_add_admin_exactly_syncs_sat_and_wcs_roles_and_preserves_other_fields(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])
    sat_path, wcs_path = _write_mod_acls(config_path)

    result = admin_acl_sync.add_admin_and_sync(config_path, ADMIN_UUID, "Captain")

    sat = json.loads(sat_path.read_text(encoding="utf-8"))
    wcs = json.loads(wcs_path.read_text(encoding="utf-8"))
    assert result.created is True
    assert result.changed is True
    assert result.sync.checked_configs == 2
    assert result.sync.changed_configs == 2
    assert result.sync.backup_count == 2
    assert _game_admins(config_path) == [ADMIN_UUID]
    assert sat["admins"] == [ADMIN_UUID]
    assert sat["gameMasters"] == [ADMIN_UUID]
    assert sat["bans"] == ["keep-ban"]
    assert sat["messages"] == {"welcome": "keep"}
    assert wcs["gameMaster"] == [ADMIN_UUID]
    assert wcs["Team"] == ["developer-only"]
    assert wcs["other"] == {"keep": True}
    backups = tmp_path / "instance" / "backups" / "admin-permissions"
    assert len(tuple(backups.glob("*.bak"))) == 2


def test_remove_admin_removes_stale_mod_rights_from_both_supported_acls(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [ADMIN_UUID, SECOND_UUID])
    sat_path, wcs_path = _write_mod_acls(
        config_path,
        sat_admins=[ADMIN_UUID, SECOND_UUID],
        wcs_admins=[ADMIN_UUID, SECOND_UUID],
    )

    result = admin_acl_sync.remove_admin_and_sync(config_path, SECOND_UUID)

    sat = json.loads(sat_path.read_text(encoding="utf-8"))
    wcs = json.loads(wcs_path.read_text(encoding="utf-8"))
    assert result.changed is True
    assert result.created is None
    assert _game_admins(config_path) == [ADMIN_UUID]
    assert sat["admins"] == [ADMIN_UUID]
    assert sat["gameMasters"] == [ADMIN_UUID]
    assert wcs["gameMaster"] == [ADMIN_UUID]


def test_missing_mod_identity_mapping_rolls_back_official_admin_change(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [ADMIN_UUID])
    sat_path, wcs_path = _write_mod_acls(
        config_path,
        sat_admins=[ADMIN_UUID],
        wcs_admins=[ADMIN_UUID],
    )
    original_config = config_path.read_bytes()
    original_sat = sat_path.read_bytes()
    original_wcs = wcs_path.read_bytes()

    with pytest.raises(admin_acl_sync.AdminAclSyncError) as raised:
        admin_acl_sync.add_admin_and_sync(config_path, STEAM_ID, "Steam Admin")

    assert raised.value.rollback_complete is True
    assert "rolled back" in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert config_path.read_bytes() == original_config
    assert sat_path.read_bytes() == original_sat
    assert wcs_path.read_bytes() == original_wcs
    assert not (tmp_path / "instance" / "admins-state.json").exists()


def test_invalid_wcs_config_rolls_back_official_and_sat_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])
    sat_path, wcs_path = _write_mod_acls(config_path, sat_admins=[])
    wcs_path.write_text("[]", encoding="utf-8")
    original_config = config_path.read_bytes()
    original_sat = sat_path.read_bytes()
    original_wcs = wcs_path.read_bytes()

    with pytest.raises(admin_acl_sync.AdminAclSyncError):
        admin_acl_sync.add_admin_and_sync(config_path, ADMIN_UUID)

    assert config_path.read_bytes() == original_config
    assert sat_path.read_bytes() == original_sat
    assert wcs_path.read_bytes() == original_wcs


def test_publish_failure_rolls_back_every_acl_and_official_admin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])
    sat_path, wcs_path = _write_mod_acls(config_path, sat_admins=[], wcs_admins=[])
    original_config = config_path.read_bytes()
    original_sat = sat_path.read_bytes()
    original_wcs = wcs_path.read_bytes()
    real_atomic_write = admin_acl_sync._atomic_write_bytes
    failed = False

    def fail_wcs_once(path: Path, content: bytes, *, mode: int | None) -> None:
        nonlocal failed
        if path == wcs_path and not failed:
            failed = True
            raise admin_acl_sync.AdminAclSyncError("controlled publish failure")
        real_atomic_write(path, content, mode=mode)

    monkeypatch.setattr(admin_acl_sync, "_atomic_write_bytes", fail_wcs_once)

    with pytest.raises(admin_acl_sync.AdminAclSyncError) as raised:
        admin_acl_sync.add_admin_and_sync(config_path, ADMIN_UUID)

    assert raised.value.rollback_complete is True
    assert config_path.read_bytes() == original_config
    assert sat_path.read_bytes() == original_sat
    assert wcs_path.read_bytes() == original_wcs


def test_no_supported_mod_configs_keeps_official_admin_workflow_available(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])

    result = admin_acl_sync.add_admin_and_sync(config_path, STEAM_ID, "Owner")

    assert result.created is True
    assert result.sync.checked_configs == 0
    assert _game_admins(config_path) == [STEAM_ID]


def test_profile_acl_path_is_preferred_and_duplicate_paths_fail_closed(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])
    profile_sat, _wcs_path = _write_mod_acls(config_path, sat_admins=[], wcs_admins=[])

    assert admin_acl_sync.sat_config_path_for_config(config_path) == profile_sat

    legacy_sat = config_path.parent / "ServerAdminTools_Config.json"
    legacy_sat.write_text("{}", encoding="utf-8")
    with pytest.raises(admin_acl_sync.AdminAclSyncError):
        admin_acl_sync.sync_admin_acls(config_path)


def test_symlinked_mod_acl_is_rejected_without_changing_official_admin(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])
    sat_path, _wcs_path = _write_mod_acls(config_path, sat_admins=[], wcs_admins=[])
    external_path = tmp_path / "external-sat.json"
    external_path.write_bytes(sat_path.read_bytes())
    sat_path.unlink()
    sat_path.symlink_to(external_path)
    original_config = config_path.read_bytes()
    original_external = external_path.read_bytes()

    with pytest.raises(admin_acl_sync.AdminAclSyncError) as raised:
        admin_acl_sync.add_admin_and_sync(config_path, ADMIN_UUID)

    assert "symlink" in str(raised.value).lower()
    assert config_path.read_bytes() == original_config
    assert external_path.read_bytes() == original_external


def test_non_string_mod_role_value_rolls_back_official_admin_change(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])
    sat_path, wcs_path = _write_mod_acls(config_path, sat_admins=[], wcs_admins=[])
    sat = json.loads(sat_path.read_text(encoding="utf-8"))
    sat["admins"] = [{"unexpected": "object"}]
    sat_path.write_text(json.dumps(sat, indent=4), encoding="utf-8")
    original_config = config_path.read_bytes()
    original_sat = sat_path.read_bytes()
    original_wcs = wcs_path.read_bytes()

    with pytest.raises(admin_acl_sync.AdminAclSyncError):
        admin_acl_sync.add_admin_and_sync(config_path, ADMIN_UUID)

    assert config_path.read_bytes() == original_config
    assert sat_path.read_bytes() == original_sat
    assert wcs_path.read_bytes() == original_wcs


def test_acl_backup_retention_is_bounded(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [])
    _write_mod_acls(config_path, sat_admins=[], wcs_admins=[])
    backup_dir = tmp_path / "instance" / "backups" / "admin-permissions"
    backup_dir.mkdir(parents=True)
    for index in range(55):
        backup = backup_dir / f"old-{index:02d}.bak"
        backup.write_text("old", encoding="utf-8")
        timestamp = 1_000_000_000 + index
        os.utime(backup, ns=(timestamp, timestamp))

    result = admin_acl_sync.add_admin_and_sync(config_path, ADMIN_UUID)

    assert result.sync.backup_count == 2
    backups = tuple(backup_dir.glob("*.bak"))
    assert len(backups) == 50
    assert not (backup_dir / "old-00.bak").exists()
