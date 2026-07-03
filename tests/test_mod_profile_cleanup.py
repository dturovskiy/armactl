"""Tests for narrow disabled-mod profile settings cleanup."""

from __future__ import annotations

import json
from pathlib import Path

from armactl.mods_diagnostics import collect_mod_diagnostics
from armactl.mods_state import load_disabled_mods, save_disabled_mods
from armactl.state import ServerState

ACE_MOD_ID = "65AD7C75826B46C6"
ACE_MODULE = "ACE_Radio_SettingsModule"


def _state(config_path: Path) -> ServerState:
    return ServerState(
        server_installed=True,
        config_exists=True,
        service_exists=True,
        config_path=str(config_path),
    )


def _write_config(
    tmp_path: Path,
    *,
    active_mods: list[dict[str, str]] | None = None,
    disabled_mods: list[dict[str, str]] | None = None,
) -> Path:
    config_path = tmp_path / "instance" / "config" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "publicPort": 2001,
        "game": {
            "name": "Test Server",
            "scenarioId": "{TEST}Missions/Test.conf",
            "maxPlayers": 32,
            "mods": active_mods or [],
        },
    }
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if disabled_mods is not None:
        save_disabled_mods(config_path, disabled_mods)
    return config_path


def _settings_path(config_path: Path) -> Path:
    return config_path.parent / "profile" / ".save" / "settings" / "ReforgerGameSettings.conf"


def _write_settings(config_path: Path, text: str) -> Path:
    path = _settings_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _patch_discovery(monkeypatch, config_path: Path) -> None:
    from armactl.web.services import mod_profile_cleanup

    monkeypatch.setattr(
        mod_profile_cleanup.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )


def _run_cleanup(tmp_path: Path):
    from armactl.web.services import mod_profile_cleanup

    return mod_profile_cleanup.cleanup_profile_settings_and_audit(
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )


def _audit_events(tmp_path: Path) -> list[dict[str, object]]:
    audit_path = tmp_path / "logs" / "web" / "audit.log"
    return [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]


def _backup_files(config_path: Path) -> list[Path]:
    return sorted(
        (config_path.parent.parent / "backups" / "profile-settings-cleanup").glob("*.bak")
    )


def _settings_with_known_block() -> str:
    return (
        "GameSettings {\n"
        "    KeepModule {\n"
        "        enabled 1\n"
        "    }\n"
        f"    {ACE_MODULE} {ACE_MODULE} \"{{69B11840D553139A}}\" {{\n"
        "        enabled 1\n"
        "        Nested {\n"
        "            value \"{not a block}\"\n"
        "        }\n"
        "    }\n"
        "    OtherModule {\n"
        "        value 2\n"
        "    }\n"
        "}\n"
    )


def test_profile_cleanup_removes_exact_known_disabled_module_block(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.services import pending_work

    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    original = _settings_with_known_block()
    settings_path = _write_settings(config_path, original)
    _patch_discovery(monkeypatch, config_path)

    result = _run_cleanup(tmp_path)

    updated = settings_path.read_text(encoding="utf-8")
    assert result.success is True
    assert result.changed is True
    assert result.details == {
        "files_considered": "1",
        "files_changed": "1",
        "modules_removed": "1",
        "skipped_ambiguous": "0",
        "skipped_missing": "0",
    }
    assert ACE_MODULE not in updated
    assert "KeepModule" in updated
    assert "OtherModule" in updated
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["mods"] == []
    assert load_disabled_mods(config_path) == [{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}]

    backups = _backup_files(config_path)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    item = pending_work.get_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_MODS,
    )
    assert item is not None
    assert item.source_action == "mod.profile-settings-cleanup"
    assert item.source_path == "/mods"
    assert item.details == "profile settings references"


def test_profile_cleanup_does_not_remove_module_if_mod_is_not_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path)
    original = _settings_with_known_block()
    settings_path = _write_settings(config_path, original)
    _patch_discovery(monkeypatch, config_path)

    result = _run_cleanup(tmp_path)

    assert result.success is True
    assert result.changed is False
    assert settings_path.read_text(encoding="utf-8") == original
    assert _backup_files(config_path) == []


def test_profile_cleanup_does_not_remove_unknown_module_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    original = (
        "GameSettings {\n"
        "    ACE_Unknown_SettingsModule {\n"
        "        enabled 1\n"
        "    }\n"
        "}\n"
    )
    settings_path = _write_settings(config_path, original)
    _patch_discovery(monkeypatch, config_path)

    result = _run_cleanup(tmp_path)

    assert result.success is True
    assert result.changed is False
    assert settings_path.read_text(encoding="utf-8") == original
    assert result.details["modules_removed"] == "0"
    assert result.details["skipped_missing"] == "1"


def test_profile_cleanup_ambiguous_unbalanced_block_is_skipped_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    original = f"GameSettings {{\n    {ACE_MODULE} {{\n        enabled 1\n"
    settings_path = _write_settings(config_path, original)
    _patch_discovery(monkeypatch, config_path)

    result = _run_cleanup(tmp_path)

    assert result.success is False
    assert result.changed is False
    assert result.details["modules_removed"] == "0"
    assert result.details["skipped_ambiguous"] == "1"
    assert settings_path.read_text(encoding="utf-8") == original
    assert _backup_files(config_path) == []


def test_profile_cleanup_second_run_is_noop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    _write_settings(config_path, _settings_with_known_block())
    _patch_discovery(monkeypatch, config_path)

    first = _run_cleanup(tmp_path)
    second = _run_cleanup(tmp_path)

    assert first.changed is True
    assert second.success is True
    assert second.changed is False
    assert second.details["files_changed"] == "0"
    assert second.details["modules_removed"] == "0"
    assert len(_backup_files(config_path)) == 1


def test_profile_cleanup_backup_exists_before_publish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.services import mod_profile_cleanup

    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    original = _settings_with_known_block()
    settings_path = _write_settings(config_path, original)
    _patch_discovery(monkeypatch, config_path)
    original_publish = mod_profile_cleanup._publish_staged_update
    observed: list[str] = []

    def assert_backup_before_publish(file_plan, staged_path, backup_path) -> None:
        assert backup_path.is_file()
        assert backup_path.read_bytes() == file_plan.original_bytes
        assert settings_path.read_text(encoding="utf-8") == original
        observed.append(backup_path.name)
        original_publish(file_plan, staged_path, backup_path)

    monkeypatch.setattr(
        mod_profile_cleanup,
        "_publish_staged_update",
        assert_backup_before_publish,
    )

    result = _run_cleanup(tmp_path)

    assert result.success is True
    assert observed


def test_profile_cleanup_pending_restart_fallback_is_used(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.services import pending_work

    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    _write_settings(config_path, _settings_with_known_block())
    _patch_discovery(monkeypatch, config_path)

    def fail_primary_pending(*args, **kwargs):
        raise RuntimeError("web.db locked /raw/path token=raw-pending-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_primary_pending)

    result = _run_cleanup(tmp_path)

    assert result.success is True
    assert result.changed is True
    assert result.pending_work_warning == pending_work.PENDING_WORK_FALLBACK_WARNING
    assert result.pending_work_error == ""
    item = pending_work.get_fallback_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_MODS,
    )
    assert item is not None
    assert item.source_action == "mod.profile-settings-cleanup"
    sidecar_text = pending_work.fallback_pending_work_path(
        tmp_path / "web" / "web.db"
    ).read_text(encoding="utf-8")
    assert "raw-pending-secret" not in sidecar_text
    assert "/raw/path" not in sidecar_text


def test_profile_cleanup_audit_counts_are_safe_and_have_no_raw_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    _write_settings(config_path, _settings_with_known_block())
    _patch_discovery(monkeypatch, config_path)

    result = _run_cleanup(tmp_path)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    events = _audit_events(tmp_path)
    outcome = events[-1]

    assert result.success is True
    assert str(tmp_path) not in audit_text
    assert str(config_path) not in audit_text
    assert str(_settings_path(config_path)) not in audit_text
    assert str(tmp_path) not in json.dumps(result.details, sort_keys=True)
    assert outcome["action"] == "mod.profile-settings-cleanup"
    assert outcome["target"] == "profile settings"
    assert outcome["details"] == {
        "phase": "outcome",
        "files_considered": "1",
        "files_changed": "1",
        "modules_removed": "1",
        "skipped_ambiguous": "0",
        "skipped_missing": "0",
    }


def test_profile_cleanup_rejects_symlink_candidate_without_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    outside = tmp_path / "outside-settings.conf"
    outside.write_text(_settings_with_known_block(), encoding="utf-8")
    settings_path = _settings_path(config_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.symlink_to(outside)
    _patch_discovery(monkeypatch, config_path)

    result = _run_cleanup(tmp_path)

    assert result.success is False
    assert result.changed is False
    assert ACE_MODULE in outside.read_text(encoding="utf-8")
    assert _backup_files(config_path) == []


def test_profile_cleanup_rejects_traversal_candidate_without_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import mods_diagnostics

    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    outside = config_path.parent / ".." / "outside-settings.conf"
    outside.write_text(_settings_with_known_block(), encoding="utf-8")
    monkeypatch.setattr(
        mods_diagnostics,
        "PROFILE_SETTINGS_CANDIDATES",
        (Path("../outside-settings.conf"),),
    )
    _patch_discovery(monkeypatch, config_path)

    result = _run_cleanup(tmp_path)

    assert result.success is False
    assert result.changed is False
    assert ACE_MODULE in outside.read_text(encoding="utf-8")
    assert _backup_files(config_path) == []


def test_profile_cleanup_diagnostics_no_longer_reports_removed_stale_reference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        disabled_mods=[{"modId": ACE_MOD_ID, "name": "ACE Radio Dev"}],
    )
    _write_settings(config_path, _settings_with_known_block())
    _patch_discovery(monkeypatch, config_path)

    before = collect_mod_diagnostics(config_path)
    result = _run_cleanup(tmp_path)
    after = collect_mod_diagnostics(config_path)

    assert before.stale_profile_settings_references
    assert result.success is True
    assert after.stale_profile_settings_references == ()
