"""CLI coverage for shared update-profile compatibility checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from armactl import safe_update
from armactl.cli import main
from armactl.state import ServerState


def _state(tmp_path: Path, *, running: bool = False) -> ServerState:
    instance_root = tmp_path / "instance"
    return ServerState(
        server_installed=True,
        server_running=running,
        install_dir=str(instance_root / "server"),
        config_path=str(instance_root / "config" / "config.json"),
    )


def test_cli_profile_check_uses_shared_backend_without_activation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = _state(tmp_path)
    observed: dict[str, object] = {}

    def verify(install_dir: Path, config_path: Path, *, name: str):
        observed.update(
            install_dir=install_dir,
            config_path=config_path,
            name=name,
        )
        yield "Testing profile vanilla without activating it."
        yield "Profile vanilla is compatible; it was not activated."

    monkeypatch.setattr("armactl.cli._get_state", lambda ctx: state)
    monkeypatch.setattr(safe_update, "verify_named_profile", verify)

    result = CliRunner().invoke(
        main,
        ["--json-output", "update", "profile", "check", "vanilla"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "status": "compatible",
        "profile": "vanilla",
        "steps": [
            "Testing profile vanilla without activating it.",
            "Profile vanilla is compatible; it was not activated.",
        ],
    }
    assert observed == {
        "install_dir": Path(state.install_dir),
        "config_path": Path(state.config_path),
        "name": "vanilla",
    }


def test_cli_profile_check_refuses_while_game_server_is_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("armactl.cli._get_state", lambda ctx: _state(tmp_path, running=True))
    monkeypatch.setattr(safe_update, "verify_named_profile", pytest.fail)

    result = CliRunner().invoke(
        main,
        ["--json-output", "update", "profile", "check", "vanilla"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "failed",
        "error": "Stop the game server before testing profiles.",
    }


def test_cli_profile_check_reports_incompatible_without_claiming_activation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = _state(tmp_path)

    def reject(install_dir: Path, config_path: Path, *, name: str):
        del install_dir, config_path, name
        yield "Testing stored profile."
        raise safe_update.ProfileIncompatibleError(
            "Profile modded is incompatible; the active profile was not changed."
        )

    monkeypatch.setattr("armactl.cli._get_state", lambda ctx: state)
    monkeypatch.setattr(safe_update, "verify_named_profile", reject)

    result = CliRunner().invoke(
        main,
        ["--json-output", "update", "profile", "check", "modded"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "incompatible",
        "profile": "modded",
        "error": (
            "Profile modded is incompatible; the active profile was not changed."
        ),
        "steps": ["Testing stored profile."],
    }
