"""Tests for safe junk cleanup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import armactl.cleaner as cleaner


def _patch_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    config_dir = tmp_path / "armactl-data" / "default" / "config"
    backups_dir = tmp_path / "armactl-data" / "default" / "backups"
    monkeypatch.setattr(cleaner.paths, "config_dir", lambda instance: config_dir)
    monkeypatch.setattr(cleaner.paths, "backups_dir", lambda instance: backups_dir)
    return config_dir, backups_dir


def _write_file(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _symlink_or_skip(target: Path, link: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable here: {exc}")


def test_clean_junk_deletes_logs_dumps_and_old_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir, backups_dir = _patch_roots(monkeypatch, tmp_path)
    log_file = _write_file(config_dir / "logs" / "latest" / "console.log")
    dump_file = _write_file(config_dir / "crash" / "server.rpt")

    backups = [
        _write_file(backups_dir / f"config.json.{index}.bak", str(index))
        for index in range(4)
    ]
    for index, backup in enumerate(backups):
        os.utime(backup, (index + 1, index + 1))

    stats = cleaner.get_junk_stats("default")

    assert stats["logs"]["paths"] == [log_file.resolve()]
    assert stats["dumps"]["paths"] == [dump_file.resolve()]
    assert [path.name for path in stats["backups"]["paths"]] == [
        "config.json.0.bak",
        "config.json.1.bak",
    ]

    result = cleaner.clean_junk("default")

    assert result["files_deleted"] == 4
    assert not log_file.exists()
    assert not dump_file.exists()
    assert not backups[0].exists()
    assert not backups[1].exists()
    assert backups[2].exists()
    assert backups[3].exists()


def test_clean_junk_skips_symlinked_files_and_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir, _backups_dir = _patch_roots(monkeypatch, tmp_path)
    external_dir = tmp_path / "external"
    outside_log = _write_file(external_dir / "outside.log")
    log_dir = config_dir / "logs"
    log_dir.mkdir(parents=True)
    linked_file = log_dir / "linked.log"
    linked_dir = config_dir / "linked-dir"
    _symlink_or_skip(outside_log, linked_file)
    _symlink_or_skip(external_dir, linked_dir, target_is_directory=True)

    stats = cleaner.get_junk_stats("default")
    result = cleaner.clean_junk("default")

    assert stats["logs"]["paths"] == []
    assert result["files_deleted"] == 0
    assert outside_log.exists()
    assert linked_file.exists()
    assert linked_dir.exists()


def test_clean_junk_refuses_outside_paths_from_stats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir, backups_dir = _patch_roots(monkeypatch, tmp_path)
    config_dir.mkdir(parents=True)
    backups_dir.mkdir(parents=True)
    outside = _write_file(tmp_path / "outside.log")

    monkeypatch.setattr(
        cleaner,
        "get_junk_stats",
        lambda instance: {
            "logs": {"count": 1, "size": 1, "paths": [outside]},
            "dumps": {"count": 0, "size": 0, "paths": []},
            "backups": {"count": 0, "size": 0, "paths": []},
            "total_size": 1,
        },
    )

    result = cleaner.clean_junk("default")

    assert result == {"freed_bytes": 0, "files_deleted": 0}
    assert outside.exists()
