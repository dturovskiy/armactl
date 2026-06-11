"""Tests for the web CLI launcher foundation."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from armactl.cli import main
from armactl.ports import WEB_PANEL_DEFAULT_PORT


def invoke_web(*args: str):
    return CliRunner().invoke(main, ["web", *args])


def test_web_help_exists():
    result = invoke_web("--help")

    assert result.exit_code == 0
    assert "Manage the planned browser web panel." in result.output
    assert "run" in result.output


def test_web_run_rejects_reserved_arma_game_port():
    result = invoke_web("run", "--port", "2001")

    assert result.exit_code == 1
    assert "Port 2001 is reserved for Arma game default." in result.output


def test_web_run_default_port_reaches_placeholder():
    result = invoke_web("run")

    assert result.exit_code == 1
    assert f"Port {WEB_PANEL_DEFAULT_PORT} is reserved" not in result.output
    assert "Web runtime is not implemented yet." in result.output
    assert f"127.0.0.1:{WEB_PANEL_DEFAULT_PORT}" in result.output


def test_run_web_script_delegates_to_armactl_web_run():
    script = Path("scripts/run-web").read_text(encoding="utf-8")

    dollar = chr(36)
    quote = chr(34)
    expected_mode = (
        "ARMACTL_BOOTSTRAP_MODE="
        + quote
        + dollar
        + "{ARMACTL_BOOTSTRAP_MODE:---web}"
        + quote
    )
    expected_exec = (
        "exec "
        + quote
        + dollar
        + "PROJECT_ROOT/armactl"
        + quote
        + " web run "
        + quote
        + dollar
        + "@"
        + quote
    )

    assert expected_mode in script
    assert expected_exec in script


def test_repo_launcher_tracks_bootstrap_mode_for_web_extras():
    launcher = Path("armactl").read_text(encoding="utf-8")
    bootstrap = Path("scripts/bootstrap.sh").read_text(encoding="utf-8")

    assert "mode_satisfies()" in launcher
    assert 'mode_satisfies "$stamped_mode" "$BOOTSTRAP_MODE"' in launcher
    assert "stamp_mode()" in bootstrap
    assert 'printf \'%s %s\\n\' "$(pyproject_hash)" "$(stamp_mode)"' in bootstrap
