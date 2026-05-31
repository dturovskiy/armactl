"""Regression tests for official game.admins plus local label metadata."""
from __future__ import annotations

import json
from pathlib import Path

from armactl import admins_manager
from armactl.admins_manager import (
    add_admin,
    admins_state_path_for_config,
    get_admins,
    migrate_legacy_admins,
    remove_admin,
    resolve_steam_identity,
)


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
            "mods": [],
        },
    }
    if admins is not None:
        payload["game"]["admins"] = admins
    config_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")


def test_add_numeric_steamid64_writes_official_server_acl_and_label_sidecar(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path)

    assert add_admin(config_path, "76561198000000001", "Owner")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == ["76561198000000001"]
    assert get_admins(config_path) == [
        {"identityId": "76561198000000001", "name": "Owner", "source": "steamid64"}
    ]


def test_readding_same_profile_updates_label_without_duplicate(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path)

    assert add_admin(
        config_path,
        "https://steamcommunity.com/profiles/76561198000000001/",
        "InitialOperator",
    )
    assert not add_admin(
        config_path,
        "https://steamcommunity.com/profiles/76561198000000001/",
        "updated-operator",
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == ["76561198000000001"]
    assert get_admins(config_path)[0]["name"] == "updated-operator"


def test_vanity_slug_uses_best_effort_public_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        admins_manager,
        "_resolve_vanity_with_public_profile",
        lambda vanity: admins_manager.ResolvedSteamIdentity(
            "76561198000000001", display_name="updated-operator", source=f"steam vanity:{vanity}"
        ),
    )
    resolved = resolve_steam_identity("updated-operator")
    assert resolved.identity_id == "76561198000000001"
    assert resolved.display_name == "updated-operator"


def test_migrate_restores_ids_removed_by_bad_sidecar_build(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path)
    sidecar = admins_state_path_for_config(config_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(
            {
                "version": 1,
                "scope": "armactl-local-operators",
                "admins": [{"identityId": "76561198000000001", "name": "Owner"}],
            }
        ),
        encoding="utf-8",
    )

    result = migrate_legacy_admins(config_path)

    assert result.migrated
    assert result.restored_from_sidecar == 1
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == ["76561198000000001"]


def test_migrate_normalizes_legacy_object_entries_to_official_strings(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path, [{"identityId": "76561198000000002", "name": "Host"}])

    result = migrate_legacy_admins(config_path)

    assert result.migrated
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == ["76561198000000002"]
    assert get_admins(config_path)[0]["name"] == "Host"


def test_remove_admin_updates_server_acl_and_sidecar(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_config(config_path)
    assert add_admin(config_path, "76561198000000001", "Owner")
    assert remove_admin(config_path, "76561198000000001")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["game"]["admins"] == []
    assert get_admins(config_path) == []
