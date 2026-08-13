"""Tests for transactional server updates and compatibility rollback."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from armactl import integrity, safe_update
from armactl.service_manager import ServiceResult


class FakeServiceAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def start_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("start", service_name))
        return ServiceResult(True, "started")

    def stop_service(self, service_name: str) -> ServiceResult:
        self.calls.append(("stop", service_name))
        return ServiceResult(True, "stopped")

    def get_service_status(self, service_name: str):
        self.calls.append(("status", service_name))
        return {"active": True, "active_state": "active", "sub_state": "running"}


def _write_server(server: Path, build: str, content: str) -> None:
    (server / "steamapps").mkdir(parents=True)
    (server / "ArmaReforgerServer").write_text(content, encoding="utf-8")
    (server / "steamapps" / "appmanifest_1874900.acf").write_text(
        f'"AppState"\n{{\n  "buildid" "{build}"\n  "StateFlags" "4"\n}}\n',
        encoding="utf-8",
    )
    integrity.write_package_manifest(server)


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    instance_root = tmp_path / "default"
    server = instance_root / "server"
    profile = instance_root / "config"
    _write_server(server, "100", "old server")
    (profile / "addons" / "WCS").mkdir(parents=True)
    (profile / "addons" / "WCS" / "mod.pak").write_text("old mod", encoding="utf-8")
    (profile / "config.json").write_text(
        json.dumps(
            {
                "game": {
                    "name": "Production",
                    "password": "production-secret",
                    "maxPlayers": 128,
                    "scenarioId": "{CUSTOM}Missions/Custom.conf",
                    "admins": ["admin-1"],
                    "mods": [
                        {"modId": "0123456789ABCDEF", "name": "WCS"},
                    ],
                    "gameProperties": {
                        "persistence": True,
                        "battlEye": True,
                    },
                },
                "bindAddress": "0.0.0.0",
                "bindPort": 2001,
                "publicAddress": "203.0.113.10",
                "publicPort": 2001,
                "a2s": {"address": "127.0.0.1", "port": 17777},
                "rcon": {"address": "127.0.0.1", "port": 19999, "password": "rcon"},
            }
        ),
        encoding="utf-8",
    )
    return server, profile / "config.json"


def _fake_update(install_dir: Path, *, instance: str):
    assert instance == "default"
    _write_server(install_dir, "200", "new server")
    yield "candidate downloaded"


def _ready(*args):
    del args
    return True, "ready"


def test_safe_update_promotes_verified_server_and_profile(tmp_path: Path, monkeypatch):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    monkeypatch.setattr(safe_update, "ensure_staging_capacity", lambda paths: (1, 2))

    def canary(update_paths: safe_update.UpdatePaths) -> safe_update.CanaryResult:
        candidate_config = json.loads(
            (update_paths.candidate_profile / "config.json").read_text(encoding="utf-8")
        )
        assert candidate_config["game"]["password"] != "production-secret"
        assert candidate_config["game"]["name"].endswith("[armactl update canary]")
        assert not (update_paths.candidate_profile / "addons").exists()
        assert (update_paths.profile / "addons" / "WCS" / "mod.pak").read_text(
            encoding="utf-8"
        ) == "old mod"
        return safe_update.CanaryResult(ready_seconds=12.5, max_players=128, map_name="Map")

    output = list(
        safe_update.stream_safe_server_update(
            server,
            config_path,
            "armareforger.service",
            update_stream=_fake_update,
            canary_runner=canary,
            adapter=adapter,
            readiness_checker=_ready,
        )
    )

    update_paths = safe_update.resolve_update_paths(server, config_path)
    production_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert safe_update.read_build_id(server) == "200"
    assert (server / "ArmaReforgerServer").read_text(encoding="utf-8") == "new server"
    assert production_config["game"]["password"] == "production-secret"
    assert production_config["game"]["name"] == "Production"
    assert (config_path.parent / "addons" / "WCS" / "mod.pak").read_text(
        encoding="utf-8"
    ) == "old mod"
    assert safe_update.read_build_id(update_paths.rollback_server) == "100"
    assert json.loads(update_paths.metadata.read_text(encoding="utf-8"))["phase"] == "committed"
    assert adapter.calls == [("start", "armareforger.service")]
    assert any("active and stable" in line for line in output)


def test_canary_rejection_leaves_old_generation_and_restarts_it(tmp_path: Path, monkeypatch):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    monkeypatch.setattr(safe_update, "ensure_staging_capacity", lambda paths: (1, 2))

    def reject(update_paths: safe_update.UpdatePaths) -> safe_update.CanaryResult:
        del update_paths
        raise safe_update.CanaryRejectedError('Can\'t compile "Game" script module')

    with pytest.raises(safe_update.SafeUpdateError, match="rejected before promotion"):
        list(
            safe_update.stream_safe_server_update(
                server,
                config_path,
                "armareforger.service",
                update_stream=_fake_update,
                canary_runner=reject,
                adapter=adapter,
                readiness_checker=_ready,
            )
        )

    update_paths = safe_update.resolve_update_paths(server, config_path)
    assert safe_update.read_build_id(server) == "100"
    assert not update_paths.candidate_server.exists()
    assert not update_paths.candidate_profile.exists()
    assert json.loads(update_paths.metadata.read_text(encoding="utf-8"))["phase"] == "rejected"
    assert adapter.calls == [("start", "armareforger.service")]


def test_failed_production_readiness_rolls_back_both_roots(tmp_path: Path, monkeypatch):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    monkeypatch.setattr(safe_update, "ensure_staging_capacity", lambda paths: (1, 2))

    def canary(update_paths: safe_update.UpdatePaths) -> safe_update.CanaryResult:
        assert not (update_paths.candidate_profile / "addons").exists()
        assert (update_paths.profile / "addons" / "WCS" / "mod.pak").is_file()
        return safe_update.CanaryResult(ready_seconds=1.0, max_players=128, map_name="Map")

    readiness_results = iter([(False, "new build failed"), (True, "old build ready")])

    def readiness(*args):
        del args
        return next(readiness_results)

    with pytest.raises(safe_update.SafeUpdateError, match="rolled back"):
        list(
            safe_update.stream_safe_server_update(
                server,
                config_path,
                "armareforger.service",
                update_stream=_fake_update,
                canary_runner=canary,
                adapter=adapter,
                readiness_checker=readiness,
            )
        )

    update_paths = safe_update.resolve_update_paths(server, config_path)
    assert safe_update.read_build_id(server) == "100"
    assert (config_path.parent / "addons" / "WCS" / "mod.pak").read_text(
        encoding="utf-8"
    ) == "old mod"
    assert json.loads(update_paths.metadata.read_text(encoding="utf-8"))["phase"] == "rolled-back"
    assert adapter.calls == [
        ("start", "armareforger.service"),
        ("stop", "armareforger.service"),
        ("start", "armareforger.service"),
    ]


def test_interrupted_partial_promotion_restores_old_server(tmp_path: Path):
    server, config_path = _layout(tmp_path)
    update_paths = safe_update.resolve_update_paths(server, config_path)
    update_paths.update_root.mkdir(mode=0o700)
    _write_server(update_paths.candidate_server, "200", "new server")
    safe_update._write_metadata(update_paths, "promoting", old_build="100", new_build="200")
    server.replace(update_paths.rollback_server)
    update_paths.candidate_server.replace(server)

    assert safe_update.recover_interrupted_promotion(update_paths) is True
    assert safe_update.read_build_id(server) == "100"
    assert config_path.is_file()
    assert json.loads(update_paths.metadata.read_text(encoding="utf-8"))["phase"] == "recovered"


def test_compatibility_canary_rejects_fatal_compile_output(tmp_path: Path, monkeypatch):
    server, config_path = _layout(tmp_path)
    update_paths = safe_update.resolve_update_paths(server, config_path)
    update_paths.update_root.mkdir(mode=0o700)
    _write_server(update_paths.candidate_server, "200", "new server")
    safe_update._copy_profile_bundle(
        update_paths,
        update_paths.profile,
        update_paths.candidate_profile,
    )
    update_paths.candidate_logs.mkdir()

    class FakeProcess:
        pid = 12345
        stdout = iter(
            [
                'SCRIPT : E : Can\'t compile "Game" script module\n',
                "SCRIPT : E : Missing required API symbol\n",
            ]
        )

        def poll(self):
            return None

    monkeypatch.setattr(safe_update, "_terminate_process", lambda process: None)

    with pytest.raises(
        safe_update.CanaryRejectedError,
        match="Missing required API symbol",
    ):
        safe_update.run_compatibility_canary(
            update_paths,
            timeout_seconds=1.0,
            stability_seconds=0.5,
            poll_interval_seconds=0.01,
            popen=lambda *args, **kwargs: FakeProcess(),
            status_probe=lambda path: safe_update.a2s.PlayerStatus(
                available=False,
                host="127.0.0.1",
                port=17777,
                error="not ready",
            ),
            sleep=time.sleep,
        )
    diagnostic = update_paths.update_root / safe_update.LAST_CANARY_FAILURE_NAME
    assert diagnostic.is_file()
    assert "Missing required API symbol" in diagnostic.read_text(encoding="utf-8")


def test_vanilla_profile_preserves_host_settings_without_mutating_source(
    tmp_path: Path,
):
    server, config_path = _layout(tmp_path)
    del server
    source = json.loads(config_path.read_text(encoding="utf-8"))
    destination = config_path.parent.parent / "vanilla"

    disabled = safe_update.make_vanilla_profile(config_path.parent, destination)

    vanilla = json.loads((destination / "config.json").read_text(encoding="utf-8"))
    assert disabled == 1
    assert vanilla["bindAddress"] == "0.0.0.0"
    assert vanilla["bindPort"] == 2001
    assert vanilla["publicAddress"] == "203.0.113.10"
    assert vanilla["rcon"] == source["rcon"]
    assert vanilla["game"]["admins"] == ["admin-1"]
    assert vanilla["game"]["password"] == "production-secret"
    assert vanilla["game"]["maxPlayers"] == 128
    assert vanilla["game"]["mods"] == []
    assert vanilla["game"]["scenarioId"] == safe_update.DEFAULT_VANILLA_SCENARIO
    assert vanilla["game"]["gameProperties"]["persistence"] is False
    assert vanilla["game"]["gameProperties"]["battlEye"] is True
    assert list((destination / "addons").iterdir()) == []
    assert json.loads(config_path.read_text(encoding="utf-8")) == source


def test_modded_rejection_promotes_vanilla_and_parks_complete_profile(
    tmp_path: Path,
    monkeypatch,
):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    monkeypatch.setattr(safe_update, "ensure_staging_capacity", lambda paths: (1, 2))
    calls = 0

    def canary(update_paths: safe_update.UpdatePaths) -> safe_update.CanaryResult:
        nonlocal calls
        calls += 1
        config = json.loads(
            (update_paths.candidate_profile / "config.json").read_text(encoding="utf-8")
        )
        if calls == 1:
            assert config["game"]["mods"][0]["name"] == "WCS"
            raise safe_update.CanaryRejectedError('Can\'t compile "Game" script module')
        assert config["game"]["mods"] == []
        assert config["game"]["scenarioId"] == safe_update.DEFAULT_VANILLA_SCENARIO
        return safe_update.CanaryResult(2.0, 128, "Everon")

    output = list(
        safe_update.stream_safe_server_update(
            server,
            config_path,
            "armareforger.service",
            update_stream=_fake_update,
            canary_runner=canary,
            adapter=adapter,
            readiness_checker=_ready,
        )
    )

    update_paths = safe_update.resolve_update_paths(server, config_path)
    active = json.loads(config_path.read_text(encoding="utf-8"))
    parked = json.loads(
        (update_paths.parked_modded_profile / "config.json").read_text(encoding="utf-8")
    )
    assert calls == 2
    assert safe_update.read_build_id(server) == "200"
    assert active["game"]["mods"] == []
    assert active["game"]["scenarioId"] == safe_update.DEFAULT_VANILLA_SCENARIO
    assert active["game"]["password"] == "production-secret"
    assert parked["game"]["scenarioId"] == "{CUSTOM}Missions/Custom.conf"
    assert parked["game"]["mods"][0]["name"] == "WCS"
    assert not (update_paths.parked_modded_profile / "addons").exists()
    assert (config_path.parent / "addons" / "WCS" / "mod.pak").is_file()
    parked_details = safe_update.get_parked_modded_profile(server, config_path)
    assert parked_details is not None
    assert parked_details.name == "modded"
    assert parked_details.mod_count == 1
    status = safe_update.get_compatibility_status(server, config_path)
    assert status.active_mode == safe_update.VANILLA_MODE
    assert status.disabled_mod_count == 1
    assert any("parked" in line for line in output)


def test_activate_vanilla_then_retry_complete_modded_profile(
    tmp_path: Path,
):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()

    vanilla_output = list(
        safe_update.activate_vanilla(
            server,
            config_path,
            "armareforger.service",
            current_canary_runner=lambda update_paths: safe_update.CanaryResult(
                1.0, 128, "Everon"
            ),
            adapter=adapter,
            readiness_checker=_ready,
        )
    )
    update_paths = safe_update.resolve_update_paths(server, config_path)
    vanilla_status = safe_update.get_compatibility_status(server, config_path)
    assert vanilla_status.active_mode == "vanilla"
    assert vanilla_status.rollback_available is True
    assert update_paths.parked_modded_profile.is_dir()
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == []
    assert any("Vanilla mode is active" in line for line in vanilla_output)

    retry_output = list(
        safe_update.retry_modded(
            server,
            config_path,
            "armareforger.service",
            current_canary_runner=lambda update_paths: safe_update.CanaryResult(
                1.0, 128, "Custom"
            ),
            adapter=adapter,
            readiness_checker=_ready,
        )
    )
    restored = json.loads(config_path.read_text(encoding="utf-8"))
    assert restored["game"]["scenarioId"] == "{CUSTOM}Missions/Custom.conf"
    assert restored["game"]["mods"][0]["name"] == "WCS"
    assert (config_path.parent / "addons" / "WCS" / "mod.pak").is_file()
    assert not update_paths.parked_modded_profile.exists()
    assert safe_update.get_compatibility_status(server, config_path).active_mode == "modded"
    assert any("active and stable again" in line for line in retry_output)


def test_retry_rejection_keeps_vanilla_and_parked_modded_profile(tmp_path: Path):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    list(
        safe_update.activate_vanilla(
            server,
            config_path,
            "armareforger.service",
            current_canary_runner=lambda paths: safe_update.CanaryResult(1.0, 128, "Everon"),
            adapter=adapter,
            readiness_checker=_ready,
        )
    )

    def reject(paths: safe_update.UpdatePaths) -> safe_update.CanaryResult:
        del paths
        raise safe_update.CanaryRejectedError("WCS API mismatch")

    with pytest.raises(safe_update.SafeUpdateError, match="still incompatible"):
        list(
            safe_update.retry_modded(
                server,
                config_path,
                "armareforger.service",
                current_canary_runner=reject,
                adapter=adapter,
                readiness_checker=_ready,
            )
        )

    update_paths = safe_update.resolve_update_paths(server, config_path)
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == []
    assert not (update_paths.parked_modded_profile / "addons").exists()
    assert (config_path.parent / "addons" / "WCS" / "mod.pak").is_file()
    assert safe_update.get_compatibility_status(server, config_path).active_mode == "vanilla"


def test_automatic_vanilla_fallback_can_be_disabled(tmp_path: Path, monkeypatch):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    monkeypatch.setattr(safe_update, "ensure_staging_capacity", lambda paths: (1, 2))
    safe_update.set_automatic_vanilla_fallback(
        server,
        config_path,
        enabled=False,
    )
    calls = 0

    def reject(paths: safe_update.UpdatePaths) -> safe_update.CanaryResult:
        nonlocal calls
        del paths
        calls += 1
        raise safe_update.CanaryRejectedError("mod API mismatch")

    with pytest.raises(safe_update.SafeUpdateError, match="fallback is disabled"):
        list(
            safe_update.stream_safe_server_update(
                server,
                config_path,
                "armareforger.service",
                update_stream=_fake_update,
                canary_runner=reject,
                adapter=adapter,
                readiness_checker=_ready,
            )
        )

    assert calls == 1
    assert safe_update.read_build_id(server) == "100"
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"]
    assert not safe_update.resolve_update_paths(server, config_path).parked_modded_profile.exists()
    assert safe_update.get_update_policy(
        server, config_path
    ).automatic_vanilla_fallback is False


def test_named_profiles_can_be_created_and_switched_both_ways(tmp_path: Path):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    renamed = safe_update.rename_active_profile(
        server,
        config_path,
        name="zakarpattia",
    )
    assert renamed.name == "zakarpattia"
    created = safe_update.create_named_profile(
        server,
        config_path,
        name="vanilla-everon",
        vanilla=True,
    )
    assert created.mode == "vanilla"
    assert created.mod_count == 0

    before = safe_update.get_named_profiles(server, config_path)
    assert [(item.name, item.active) for item in before] == [
        ("zakarpattia", True),
        ("vanilla-everon", False),
    ]

    output = list(
        safe_update.switch_named_profile(
            server,
            config_path,
            "armareforger.service",
            name="vanilla-everon",
            current_canary_runner=lambda paths: safe_update.CanaryResult(
                1.0, 128, "Everon"
            ),
            adapter=adapter,
            readiness_checker=_ready,
        )
    )
    update_paths = safe_update.resolve_update_paths(server, config_path)
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == []
    assert not (update_paths.profiles_root / "zakarpattia" / "addons").exists()
    assert (config_path.parent / "addons" / "WCS" / "mod.pak").is_file()
    assert not (update_paths.profiles_root / "vanilla-everon").exists()
    assert any("prior profile zakarpattia is stored" in line for line in output)

    after = safe_update.get_named_profiles(server, config_path)
    assert [(item.name, item.active) for item in after] == [
        ("vanilla-everon", True),
        ("zakarpattia", False),
    ]
    list(
        safe_update.switch_named_profile(
            server,
            config_path,
            "armareforger.service",
            name="zakarpattia",
            current_canary_runner=lambda paths: safe_update.CanaryResult(
                1.0, 128, "Custom"
            ),
            adapter=adapter,
            readiness_checker=_ready,
        )
    )
    restored = json.loads(config_path.read_text(encoding="utf-8"))
    assert restored["game"]["mods"][0]["name"] == "WCS"
    assert (update_paths.profiles_root / "vanilla-everon" / "config.json").is_file()
    assert not (update_paths.profiles_root / "zakarpattia").exists()


def test_manual_rollback_from_vanilla_update_restores_original_modded_generation(
    tmp_path: Path,
    monkeypatch,
):
    server, config_path = _layout(tmp_path)
    adapter = FakeServiceAdapter()
    monkeypatch.setattr(safe_update, "ensure_staging_capacity", lambda paths: (1, 2))
    calls = 0

    def canary(paths: safe_update.UpdatePaths) -> safe_update.CanaryResult:
        nonlocal calls
        del paths
        calls += 1
        if calls == 1:
            raise safe_update.CanaryRejectedError("WCS Core API mismatch")
        return safe_update.CanaryResult(1.0, 128, "Everon")

    list(
        safe_update.stream_safe_server_update(
            server,
            config_path,
            "armareforger.service",
            update_stream=_fake_update,
            canary_runner=canary,
            adapter=adapter,
            readiness_checker=_ready,
        )
    )
    assert safe_update.get_compatibility_status(
        server, config_path
    ).rollback_available is True

    list(
        safe_update.rollback_last_update(
            server,
            config_path,
            "armareforger.service",
            adapter=adapter,
            readiness_checker=_ready,
        )
    )

    update_paths = safe_update.resolve_update_paths(server, config_path)
    restored = json.loads(config_path.read_text(encoding="utf-8"))
    assert safe_update.read_build_id(server) == "100"
    assert restored["game"]["scenarioId"] == "{CUSTOM}Missions/Custom.conf"
    assert restored["game"]["mods"][0]["name"] == "WCS"
    assert (config_path.parent / "addons" / "WCS" / "mod.pak").is_file()
    assert not update_paths.parked_modded_profile.exists()
    status = safe_update.get_compatibility_status(server, config_path)
    assert status.active_mode == "modded"
    assert status.rollback_available is False
