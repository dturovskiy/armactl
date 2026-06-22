"""Tests for CLI config compatibility adapters."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from armactl.cli import main
from armactl.server_config_schema import generated_default_config_values
from armactl.state import ServerState


def _write_default_config(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            generated_default_config_values(
                rcon_password="generated-rcon-secret",
                password_admin="generated-admin-secret",
            ),
            indent=2,
        ),
        encoding="utf-8",
    )


def _patch_config_state(monkeypatch, config_path: Path) -> None:
    state = ServerState(
        server_installed=True,
        config_exists=True,
        config_path=str(config_path),
    )
    monkeypatch.setattr("armactl.cli._get_state", lambda ctx: state)


def test_cli_config_set_name_uses_registered_field_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_default_config(config_path)
    _patch_config_state(monkeypatch, config_path)

    result = CliRunner().invoke(main, ["config", "set-name", "Registry Server"])

    assert result.exit_code == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["game"]["name"] == "Registry Server"
    assert payload["game"]["scenarioId"] == "{ECC61978EDCC2B5A}Missions/23_Campaign.conf"


def test_cli_config_set_name_rejects_invalid_registered_string(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "instance" / "config" / "config.json"
    _write_default_config(config_path)
    _patch_config_state(monkeypatch, config_path)

    result = CliRunner().invoke(main, ["config", "set-name", "bad\tname"])

    assert result.exit_code == 1
    assert "game.name is required" in result.output
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["game"]["name"] == "Arma Reforger Server"
