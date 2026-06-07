"""Tests for armactl.config_manager module."""

import errno
import json
from pathlib import Path

import pytest

from armactl import config_manager
from armactl.config_manager import ConfigError, save_config


def test_save_config_keeps_backups_next_to_nonstandard_config(tmp_path: Path):
    """Backups for non-instance configs should stay near the config file."""
    config_path = tmp_path / "target" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"game": {"name": "Test"}}), encoding="utf-8")

    save_config(config_path, {"game": {"name": "Updated"}}, backup=True, validate=False)

    backup_files = list((tmp_path / "target" / "backups").glob("config.json.*.bak"))
    assert len(backup_files) == 1


def test_create_backup_rotates_before_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Old backups should be rotated before copying the next backup."""
    config_path = tmp_path / "target" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"game": {"name": "Test"}}), encoding="utf-8")
    events: list[tuple[str, int | None]] = []

    def fake_rotate(backups_dir: Path, max_backups: int = 10) -> None:
        events.append(("rotate", max_backups))

    def fake_copy(src: Path, dst: Path) -> None:
        events.append(("copy", None))
        Path(dst).write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(config_manager, "_rotate_backups", fake_rotate)
    monkeypatch.setattr(config_manager.shutil, "copy2", fake_copy)

    save_config(config_path, {"game": {"name": "Updated"}}, backup=True, validate=False)

    assert events[:2] == [("rotate", 9), ("copy", None)]
    assert events[-1] == ("rotate", 10)


def test_failed_backup_copy_removes_partial_backup_and_preserves_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "target" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    original = {"game": {"name": "Test"}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    def fail_copy(src: Path, dst: Path) -> None:
        Path(dst).write_text("partial", encoding="utf-8")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(config_manager.shutil, "copy2", fail_copy)

    with pytest.raises(ConfigError) as exc_info:
        save_config(config_path, {"game": {"name": "Updated"}}, backup=True, validate=False)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
    assert list((tmp_path / "target" / "backups").glob("config.json.*.bak")) == []


def test_backups_do_not_overwrite_when_timestamp_collides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "target" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"game": {"name": "Original"}}), encoding="utf-8")
    monkeypatch.setattr(config_manager.time, "time_ns", lambda: 123456789)

    save_config(config_path, {"game": {"name": "First"}}, backup=True, validate=False)
    save_config(config_path, {"game": {"name": "Second"}}, backup=True, validate=False)

    backup_files = sorted((tmp_path / "target" / "backups").glob("config.json.*.bak"))
    assert [backup.name for backup in backup_files] == [
        "config.json.123456789.1.bak",
        "config.json.123456789.bak",
    ]
    backup_payloads = {
        json.loads(backup.read_text(encoding="utf-8"))["game"]["name"]
        for backup in backup_files
    }
    assert backup_payloads == {"Original", "First"}


def test_failed_tmp_write_removes_tmp_and_preserves_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "target" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    original = {"game": {"name": "Test"}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    def fail_dump(data: dict[str, object], fp, indent: int) -> None:
        fp.write("{")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(config_manager.json, "dump", fail_dump)

    with pytest.raises(ConfigError) as exc_info:
        save_config(config_path, {"game": {"name": "Updated"}}, backup=False, validate=False)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
    assert not config_path.with_suffix(".json.tmp").exists()
