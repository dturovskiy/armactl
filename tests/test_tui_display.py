"""Tests for TUI display labels."""

from __future__ import annotations

import json
from pathlib import Path

from armactl.tui.display import get_instance_display_label


def _write_config(config_path: Path, server_name: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"game": {"name": server_name}}, indent=4),
        encoding="utf-8",
    )


def test_instance_display_label_uses_server_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, "Denis Server")

    assert get_instance_display_label("default", config_path=config_path) == (
        "Denis Server [default]"
    )


def test_instance_display_label_falls_back_when_config_missing(tmp_path: Path) -> None:
    assert get_instance_display_label("default", config_path=tmp_path / "missing.json") == "default"


def test_instance_display_label_falls_back_when_config_invalid(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not-json", encoding="utf-8")

    assert get_instance_display_label("default", config_path=config_path) == "default"


def test_instance_display_label_falls_back_when_server_name_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, "   ")

    assert get_instance_display_label("default", config_path=config_path) == "default"
