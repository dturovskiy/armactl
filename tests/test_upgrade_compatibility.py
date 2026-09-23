"""Compatibility acceptance for an existing v0.5.3-style runtime tree."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from subprocess import CompletedProcess

from armactl import discovery, installer, paths, safe_update


def _snapshot_files(*roots: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for root in roots:
        for item in sorted(root.rglob("*")):
            if item.is_file():
                snapshot[f"{root.name}/{item.relative_to(root)}"] = item.read_bytes()
    return snapshot


def test_existing_v053_runtime_tree_remains_readable_and_byte_preserved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "armactl-data"
    instance_root = data_root / "default"
    server = instance_root / "server"
    config_dir = instance_root / "config"
    config_path = config_dir / "config.json"
    update_root = instance_root / "server-update"
    systemd_root = tmp_path / "systemd"

    (server / "steamapps").mkdir(parents=True)
    (server / "ArmaReforgerServer").write_bytes(b"legacy-server-binary")
    (server / "steamapps" / "appmanifest_1874900.acf").write_text(
        '"AppState"\n{\n  "buildid" "23728491"\n  "StateFlags" "4"\n}\n',
        encoding="utf-8",
    )
    (config_dir / "addons" / "0123456789ABCDEF").mkdir(parents=True)
    (config_dir / "addons" / "0123456789ABCDEF" / "data.pak").write_bytes(
        b"shared-workshop-payload"
    )
    config = {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "publicAddress": "",
        "publicPort": 2001,
        "a2s": {"address": "0.0.0.0", "port": 17777},
        "rcon": {
            "address": "127.0.0.1",
            "port": 19999,
            "password": "preserved-rcon-secret",
            "permission": "admin",
            "blacklist": [],
            "whitelist": [],
        },
        "game": {
            "name": "Existing server",
            "password": "preserved-game-secret",
            "passwordAdmin": "preserved-admin-secret",
            "admins": ["76561198000000001"],
            "scenarioId": "{CUSTOM}Missions/Existing.conf",
            "maxPlayers": 128,
            "visible": True,
            "gameProperties": {
                "serverMaxViewDistance": 2500,
                "customOperatorSetting": True,
            },
            "mods": [
                {"modId": "0123456789ABCDEF", "name": "Existing Mod"},
            ],
        },
        "operatorOwnedUnknownSection": {"keep": "exactly"},
    }
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    (instance_root / "mods-state.json").write_text(
        '{"disabled": [{"modId": "FEDCBA9876543210"}]}\n',
        encoding="utf-8",
    )
    (instance_root / "admins-state.json").write_text(
        '{"admins": {"76561198000000001": {"label": "Owner"}}}\n',
        encoding="utf-8",
    )
    (instance_root / "backups").mkdir()
    (instance_root / "backups" / "config.pre-upgrade.json").write_text(
        "legacy backup\n",
        encoding="utf-8",
    )
    (instance_root / "logs").mkdir()
    (instance_root / "logs" / "host-tests-old.log").write_text(
        "legacy diagnostics\n",
        encoding="utf-8",
    )

    players_db = instance_root / "players.db"
    with sqlite3.connect(players_db) as connection:
        connection.execute("CREATE TABLE legacy_players (identity TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO legacy_players VALUES ('player-1')")

    stored_profile = update_root / "profiles" / "night-ops"
    stored_profile.mkdir(parents=True)
    (stored_profile / "config.json").write_text(
        json.dumps(
            {
                "game": {
                    "scenarioId": "{NIGHT}Missions/Night.conf",
                    "mods": [{"modId": "0123456789ABCDEF", "name": "Existing Mod"}],
                }
            }
        ),
        encoding="utf-8",
    )
    (update_root / "rollback-server").mkdir(parents=True)
    (update_root / "rollback-server" / "ArmaReforgerServer").write_bytes(b"rollback-server-binary")
    (update_root / "rollback-profile").mkdir()
    (update_root / "rollback-profile" / "config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )
    (update_root / "state.json").write_text(
        json.dumps(
            {
                "version": 2,
                "phase": "committed",
                "active_mode": "modded",
                "active_profile": "modded",
                "active_build": "23728491",
                "rollback_available": True,
                "rollback_kind": "server+profile",
            }
        ),
        encoding="utf-8",
    )

    systemd_root.mkdir()
    timer_name = paths.TIMER_NAME
    (systemd_root / timer_name).write_text(
        "[Timer]\nOnCalendar=*-*-* 06:00:00\nOnCalendar=*-*-* 18:00:00\n",
        encoding="utf-8",
    )

    before = _snapshot_files(instance_root, systemd_root)
    monkeypatch.setattr(discovery, "_service_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(discovery, "_timer_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(discovery, "_is_service_active", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        discovery, "_check_listening_ports", lambda ports: dict.fromkeys(ports, False)
    )

    state = discovery.discover(instance="default", data_root=data_root, save=False)
    monkeypatch.setattr(installer.paths, "config_file", lambda _instance: config_path)
    installer.generate_default_config("default")

    profiles = safe_update.get_named_profiles(server, config_path)
    compatibility = safe_update.get_compatibility_status(server, config_path)

    from armactl import service_manager

    monkeypatch.setattr(service_manager.paths, "SYSTEMD_DIR", systemd_root)
    monkeypatch.setattr(
        service_manager.subprocess,
        "run",
        lambda command, **_kwargs: CompletedProcess(
            command,
            0,
            stdout=(
                "ActiveState=active\nSubState=waiting\nUnitFileState=enabled\nTimersCalendar=\n"
            ),
            stderr="",
        ),
    )
    timer = service_manager.get_timer_status(timer_name)

    after = _snapshot_files(instance_root, systemd_root)
    assert after == before
    assert state.server_installed is True
    assert state.config_path == str(config_path)
    assert json.loads(config_path.read_text(encoding="utf-8")) == config
    assert {profile.name for profile in profiles} == {"modded", "night-ops"}
    assert compatibility.active_build == "23728491"
    assert compatibility.rollback_available is True
    assert timer["schedule"] == "06:00, 18:00"
    assert paths.logs_dir("default", data_root) == instance_root / "logs"
