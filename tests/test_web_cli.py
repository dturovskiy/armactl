"""Tests for the web CLI launcher foundation."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

from click.testing import CliRunner

from armactl.cli import main
from armactl.ports import WEB_PANEL_DEFAULT_PORT
from armactl.web.runtime import load_web_runtime_config

FORBIDDEN_IMPORT_PREFIXES = ("armactl.tui", "textual")


def invoke_web(*args: str):
    return CliRunner().invoke(main, ["web", *args])


def test_web_help_exists():
    result = invoke_web("--help")

    assert result.exit_code == 0
    assert "Manage the planned browser web panel." in result.output
    assert "run" in result.output
    assert "init" in result.output


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


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if _matches_prefix(module_name, prefixes):
            sys.modules.pop(module_name)


def test_web_init_creates_runtime_env_and_db(tmp_path: Path):
    result = invoke_web("init", "--data-root", str(tmp_path))

    assert result.exit_code == 0
    assert (tmp_path / "web" / "web.env").exists()
    assert (tmp_path / "web" / "web.db").exists()
    assert "Web runtime initialized." in result.output
    assert str(tmp_path / "web") in result.output
    assert str(tmp_path / "web" / "web.env") in result.output
    assert str(tmp_path / "web" / "web.db") in result.output
    assert str(tmp_path / "logs" / "web" / "audit.log") in result.output
    assert f"127.0.0.1:{WEB_PANEL_DEFAULT_PORT}" in result.output
    assert "HTTPS required: no" in result.output


def test_web_init_output_does_not_include_session_secret(tmp_path: Path):
    result = invoke_web("init", "--data-root", str(tmp_path))
    config = load_web_runtime_config(tmp_path)

    assert result.exit_code == 0
    assert config.session_secret
    assert config.session_secret not in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_init_is_idempotent_and_preserves_session_secret(tmp_path: Path):
    first_result = invoke_web("init", "--data-root", str(tmp_path))
    first_config = load_web_runtime_config(tmp_path)

    second_result = invoke_web("init", "--data-root", str(tmp_path))
    second_config = load_web_runtime_config(tmp_path)

    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    assert second_config.session_secret == first_config.session_secret
    assert second_config.bind_host == first_config.bind_host
    assert second_config.bind_port == first_config.bind_port
    assert second_config.https_required == first_config.https_required


def test_web_init_custom_bind_options_are_saved(tmp_path: Path):
    result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--host",
        "0.0.0.0",
        "--port",
        "8766",
        "--https-required",
    )

    config = load_web_runtime_config(tmp_path)

    assert result.exit_code == 0
    assert config.bind_host == "0.0.0.0"
    assert config.bind_port == 8766
    assert config.https_required is True
    assert "0.0.0.0:8766" in result.output
    assert "HTTPS required: yes" in result.output


def test_web_init_without_https_flag_preserves_existing_https_setting(tmp_path: Path):
    first_result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--https-required",
    )
    second_result = invoke_web("init", "--data-root", str(tmp_path))
    config = load_web_runtime_config(tmp_path)

    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    assert config.https_required is True
    assert "HTTPS required: yes" in second_result.output


def test_web_init_rejects_reserved_arma_game_port(tmp_path: Path):
    result = invoke_web("init", "--data-root", str(tmp_path), "--port", "2001")

    assert result.exit_code == 1
    assert "Port 2001 is reserved for Arma game default." in result.output
    assert not (tmp_path / "web" / "web.env").exists()
    assert not (tmp_path / "web" / "web.db").exists()


def test_web_init_does_not_import_tui_or_textual(tmp_path: Path, monkeypatch):
    _forget_modules("armactl.tui", "textual")
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, FORBIDDEN_IMPORT_PREFIXES):
            blocked_imports.append(name)
            raise AssertionError(f"web init imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = invoke_web("init", "--data-root", str(tmp_path))

    assert result.exit_code == 0
    assert blocked_imports == []
    assert not any(
        _matches_prefix(module_name, FORBIDDEN_IMPORT_PREFIXES) for module_name in sys.modules
    )


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
