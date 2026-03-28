"""Tests for armactl.config_manager module."""

import json
from pathlib import Path

from armactl.config_manager import save_config


def test_save_config_keeps_backups_next_to_nonstandard_config(tmp_path: Path):
    """Backups for non-instance configs should stay near the config file."""
    config_path = tmp_path / "target" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"game": {"name": "Test"}}), encoding="utf-8")

    save_config(config_path, {"game": {"name": "Updated"}}, backup=True)

    backup_files = list((tmp_path / "target" / "backups").glob("config.json.*.bak"))
    assert len(backup_files) == 1
