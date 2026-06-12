"""Tests for the web CLI launcher foundation."""

from __future__ import annotations

import builtins
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from armactl.cli import main
from armactl.ports import WEB_PANEL_DEFAULT_PORT
from armactl.web.auth.users import get_user_by_username, verify_user_password
from armactl.web.runtime import (
    ensure_web_runtime,
    load_web_runtime_config,
    save_web_runtime_config,
)

FORBIDDEN_IMPORT_PREFIXES = ("armactl.tui", "textual")


def invoke_web(*args: str, input_text: str | None = None):
    return CliRunner().invoke(main, ["web", *args], input=input_text)


def _web_user_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM web_users").fetchone()
    assert row is not None
    return row[0]


def _capture_web_run(monkeypatch):
    from armactl.web import launcher

    calls = []

    def fake_run_web_foreground(prepared):
        calls.append(prepared)

    monkeypatch.setattr(launcher, "run_web_foreground", fake_run_web_foreground)
    return calls


def test_web_help_exists():
    result = invoke_web("--help")

    assert result.exit_code == 0
    assert "Manage the planned browser web panel." in result.output
    assert "run" in result.output
    assert "init" in result.output


def test_web_run_help_describes_bind_overrides_from_env():
    result = invoke_web("run", "--help")

    assert result.exit_code == 0
    assert "comes from web.env" in result.output
    assert "[default: 127.0.0.1]" not in result.output
    assert f"[default: {WEB_PANEL_DEFAULT_PORT}]" not in result.output


def test_web_run_rejects_reserved_arma_game_port(tmp_path: Path, monkeypatch):
    calls = _capture_web_run(monkeypatch)

    result = invoke_web("run", "--data-root", str(tmp_path), "--port", "2001")

    assert result.exit_code == 1
    assert "Port 2001 is reserved for Arma game default." in result.output
    assert not (tmp_path / "web" / "web.env").exists()
    assert calls == []


def test_web_run_uses_runtime_config_without_explicit_host_port(
    tmp_path: Path,
    monkeypatch,
):
    config = ensure_web_runtime(tmp_path)
    save_web_runtime_config(
        replace(
            config,
            bind_host="0.0.0.0",
            bind_port=8766,
            https_required=True,
        )
    )
    reloaded = load_web_runtime_config(tmp_path)
    calls = _capture_web_run(monkeypatch)

    result = invoke_web("run", "--data-root", str(tmp_path))

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0].host == "0.0.0.0"
    assert calls[0].port == 8766
    assert calls[0].config.db_path == reloaded.db_path
    assert "Starting armactl web." in result.output
    assert "http://0.0.0.0:8766" in result.output
    assert "HTTPS required: yes" in result.output
    assert reloaded.session_secret not in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_run_explicit_bind_is_transient_and_does_not_rewrite_env(
    tmp_path: Path,
    monkeypatch,
):
    config = ensure_web_runtime(tmp_path)
    save_web_runtime_config(
        replace(
            config,
            bind_host="0.0.0.0",
            bind_port=8766,
            https_required=True,
        )
    )
    calls = _capture_web_run(monkeypatch)

    result = invoke_web(
        "run",
        "--data-root",
        str(tmp_path),
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    )
    reloaded = load_web_runtime_config(tmp_path)

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0].host == "127.0.0.1"
    assert calls[0].port == 8765
    assert "http://127.0.0.1:8765" in result.output
    assert reloaded.bind_host == "0.0.0.0"
    assert reloaded.bind_port == 8766
    assert reloaded.https_required is True


def test_web_run_creates_runtime_when_missing(tmp_path: Path, monkeypatch):
    calls = _capture_web_run(monkeypatch)

    result = invoke_web(
        "run",
        "--data-root",
        str(tmp_path),
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert (tmp_path / "web" / "web.env").exists()
    assert (tmp_path / "web" / "web.db").exists()
    assert "Runtime dir:" in result.output
    assert str(tmp_path / "web") in result.output


def test_web_run_calls_uvicorn_with_app_for_data_root(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web import launcher

    app = object()
    build_calls: list[Path] = []
    uvicorn_calls: list[dict[str, object]] = []

    def fake_build_web_app(data_root: Path):
        build_calls.append(data_root)
        return app

    def fake_uvicorn_run(app_arg, *, host: str, port: int):
        uvicorn_calls.append({"app": app_arg, "host": host, "port": port})

    monkeypatch.setattr(launcher, "build_web_app", fake_build_web_app)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_uvicorn_run))

    prepared = launcher.prepare_web_run(
        launcher.WebRunRequest(host="127.0.0.1", port=8765, data_root=tmp_path)
    )
    launcher.run_web_foreground(prepared)

    assert build_calls == [tmp_path]
    assert uvicorn_calls == [{"app": app, "host": "127.0.0.1", "port": 8765}]


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


def test_web_init_without_owner_does_not_create_user(tmp_path: Path):
    result = invoke_web("init", "--data-root", str(tmp_path))

    assert result.exit_code == 0
    assert _web_user_count(tmp_path / "web" / "web.db") == 0
    assert "Owner user:" not in result.output
    assert "Owner role:" not in result.output


def test_web_init_with_owner_creates_runtime_and_owner_user(tmp_path: Path):
    password = "owner password stays hidden"

    result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--owner",
        "  Admin  ",
        input_text=f"{password}\n{password}\n",
    )

    db_path = tmp_path / "web" / "web.db"
    user = get_user_by_username(db_path, "ADMIN")

    assert result.exit_code == 0
    assert (tmp_path / "web" / "web.env").exists()
    assert db_path.exists()
    assert user is not None
    assert user.username == "admin"
    assert user.role == "owner"
    assert verify_user_password(db_path, " admin ", password) is True
    assert "Web runtime initialized." in result.output
    assert "Owner user:     admin" in result.output
    assert "Owner role:     owner" in result.output
    assert password not in result.output
    assert user.password_hash not in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_init_owner_duplicate_is_controlled_error(tmp_path: Path):
    first_password = "first owner password"
    second_password = "second owner password"
    first_result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--owner",
        "owner",
        input_text=f"{first_password}\n{first_password}\n",
    )

    second_result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--owner",
        " OWNER ",
        input_text=f"{second_password}\n{second_password}\n",
    )

    assert first_result.exit_code == 0
    assert second_result.exit_code == 1
    assert "Web owner user already exists." in second_result.output
    assert "Traceback" not in second_result.output
    assert first_password not in second_result.output
    assert second_password not in second_result.output
    assert _web_user_count(tmp_path / "web" / "web.db") == 1


def test_web_init_owner_existing_owner_does_not_prompt_for_password(tmp_path: Path):
    first_password = "first owner password"
    first_result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--owner",
        "owner",
        input_text=f"{first_password}\n{first_password}\n",
    )

    second_result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--owner",
        "second-owner",
        input_text="",
    )

    assert first_result.exit_code == 0
    assert second_result.exit_code == 1
    assert "Web owner user already exists." in second_result.output
    assert "Owner password" not in second_result.output
    assert "Traceback" not in second_result.output
    assert _web_user_count(tmp_path / "web" / "web.db") == 1


def test_web_init_owner_rejects_empty_username_with_controlled_error(tmp_path: Path):
    password = "owner password"

    result = invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--owner",
        " ",
        input_text=f"{password}\n{password}\n",
    )

    assert result.exit_code == 1
    assert "Username cannot be empty." in result.output
    assert "Traceback" not in result.output
    assert password not in result.output
    assert _web_user_count(tmp_path / "web" / "web.db") == 0


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


def test_web_run_does_not_import_tui_or_textual(tmp_path: Path, monkeypatch):
    _forget_modules("armactl.tui", "textual")
    _capture_web_run(monkeypatch)
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, FORBIDDEN_IMPORT_PREFIXES):
            blocked_imports.append(name)
            raise AssertionError(f"web run imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = invoke_web("run", "--data-root", str(tmp_path), "--port", "8765")

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
