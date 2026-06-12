"""Regression tests for ServerAdminTools admin-role guard."""

from __future__ import annotations

import json
from pathlib import Path

from armactl import sat_admin_guard

SAT_UUID = "21761a7f-c9b4-4bff-8375-b4b43abb95ec"
STEAM_ID = "76561198000000001"


def _write_config(config_path: Path, admins: list[str]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "game": {
            "name": "Test Server",
            "scenarioId": "{TEST}Missions/Test.conf",
            "maxPlayers": 64,
            "mods": [],
            "admins": admins,
        },
    }
    config_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")


def _write_admin_state(config_path: Path, *, name: str = "Bublik") -> None:
    state_path = config_path.parent.parent / "admins-state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "scope": "armactl-admin-labels",
                "admins": [
                    {
                        "identityId": STEAM_ID,
                        "name": name,
                        "source": "steamid64",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_uuid_map(config_path: Path) -> None:
    map_path = sat_admin_guard.sat_uuid_map_path_for_config(config_path)
    map_path.write_text(
        json.dumps({"version": 1, "identities": {"Bublik": SAT_UUID}}),
        encoding="utf-8",
    )


def test_sat_guard_repairs_default_roles_and_preserves_other_config(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    sat_path = config_path.parent / sat_admin_guard.SAT_CONFIG_FILENAME
    _write_config(config_path, [STEAM_ID])
    _write_admin_state(config_path)
    _write_uuid_map(config_path)
    sat_path.write_text(
        json.dumps(
            {
                "admins": [sat_admin_guard.ZERO_UUID],
                "gameMasters": ["example"],
                "bans": ["example-ban"],
                "discordLink": "https://example.invalid/keep-me",
                "messages": {"welcome": "hello"},
            },
            indent=4,
        ),
        encoding="utf-8",
    )

    result = sat_admin_guard.guard_sat_admin_config(config_path)
    saved = json.loads(sat_path.read_text(encoding="utf-8"))

    assert result.checked is True
    assert result.changed is True
    assert Path(result.backup_path).is_file()
    assert saved["admins"] == [SAT_UUID]
    assert saved["gameMasters"] == [SAT_UUID]
    assert saved["bans"] == []
    assert saved["discordLink"] == "https://example.invalid/keep-me"
    assert saved["messages"] == {"welcome": "hello"}


def test_sat_guard_merges_required_uuid_without_clobbering_existing_roles(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    sat_path = config_path.parent / sat_admin_guard.SAT_CONFIG_FILENAME
    existing_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _write_config(config_path, [STEAM_ID])
    _write_admin_state(config_path)
    _write_uuid_map(config_path)
    sat_path.write_text(
        json.dumps({"admins": [existing_uuid], "gameMasters": [existing_uuid]}),
        encoding="utf-8",
    )

    sat_admin_guard.guard_sat_admin_config(config_path)
    saved = json.loads(sat_path.read_text(encoding="utf-8"))

    assert saved["admins"] == [existing_uuid, SAT_UUID]
    assert saved["gameMasters"] == [existing_uuid, SAT_UUID]


def test_sat_guard_uses_official_uuid_admin_without_uuid_map(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    sat_path = config_path.parent / sat_admin_guard.SAT_CONFIG_FILENAME
    _write_config(config_path, [SAT_UUID])
    sat_path.write_text(
        json.dumps({"admins": [sat_admin_guard.ZERO_UUID], "gameMasters": ["example"]}),
        encoding="utf-8",
    )

    result = sat_admin_guard.guard_sat_admin_config(config_path)
    saved = json.loads(sat_path.read_text(encoding="utf-8"))

    assert result.changed is True
    assert result.missing_mappings == ()
    assert saved["admins"] == [SAT_UUID]
    assert saved["gameMasters"] == [SAT_UUID]


def test_sat_guard_warns_when_steam_admin_has_no_uuid_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    sat_path = config_path.parent / sat_admin_guard.SAT_CONFIG_FILENAME
    _write_config(config_path, [STEAM_ID])
    _write_admin_state(config_path)
    sat_path.write_text(
        json.dumps({"admins": [sat_admin_guard.ZERO_UUID], "gameMasters": ["example"]}),
        encoding="utf-8",
    )

    result = sat_admin_guard.guard_sat_admin_config(config_path)
    saved = json.loads(sat_path.read_text(encoding="utf-8"))

    assert result.changed is False
    assert result.missing_mappings == ("Bublik",)
    assert saved["admins"] == [sat_admin_guard.ZERO_UUID]
    assert saved["gameMasters"] == ["example"]


def test_sat_guard_repairs_invalid_json_when_required_uuid_is_available(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    sat_path = config_path.parent / sat_admin_guard.SAT_CONFIG_FILENAME
    _write_config(config_path, [STEAM_ID])
    _write_admin_state(config_path)
    _write_uuid_map(config_path)
    sat_path.write_text("{not json", encoding="utf-8")

    result = sat_admin_guard.guard_sat_admin_config(config_path)
    saved = json.loads(sat_path.read_text(encoding="utf-8"))

    assert result.changed is True
    assert Path(result.backup_path).read_text(encoding="utf-8") == "{not json"
    assert saved["admins"] == [SAT_UUID]
    assert saved["gameMasters"] == [SAT_UUID]


def test_sat_inspection_reports_default_only_roles(tmp_path: Path) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    sat_path = config_path.parent / sat_admin_guard.SAT_CONFIG_FILENAME
    _write_config(config_path, [STEAM_ID])
    _write_admin_state(config_path)
    _write_uuid_map(config_path)
    sat_path.write_text(
        json.dumps({"admins": [sat_admin_guard.ZERO_UUID], "gameMasters": ["example"]}),
        encoding="utf-8",
    )

    inspection = sat_admin_guard.inspect_sat_admin_config(config_path)

    assert inspection.available is True
    assert inspection.valid_json is True
    assert inspection.default_only_admins is True
    assert inspection.default_only_game_masters is True
    assert inspection.warning == "ServerAdminTools admins are default/example only."
