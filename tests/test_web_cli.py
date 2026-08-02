"""Tests for the web CLI launcher foundation."""

from __future__ import annotations

import builtins
import os
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


def _web_install_result(tmp_path: Path, config, *messages: tuple[bool, str, int]):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    return web_service.WebServiceInstallResult(
        config=config,
        service_name="armactl-web.service",
        service_path=tmp_path / "systemd" / "armactl-web.service",
        results=tuple(
            ServiceResult(success, message, exit_code)
            for success, message, exit_code in messages
        ),
    )

def _stub_web_health_ready(monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import quickstart

    monkeypatch.setattr(
        quickstart,
        "wait_for_web_health",
        lambda config: ServiceResult(True, "web ready", 0),
    )

def test_web_help_exists():
    result = invoke_web("--help")

    assert result.exit_code == 0
    assert "Set up or manage the browser web panel." in result.output
    assert "run" in result.output
    assert "init" in result.output
    assert "service" in result.output
    assert "--access" in result.output


def test_web_run_help_describes_bind_overrides_from_env():
    result = invoke_web("run", "--help")

    assert result.exit_code == 0
    assert "comes from web.env" in result.output
    assert "Enable Uvicorn reload" in result.output
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


def test_web_run_dev_uses_uvicorn_reload_factory(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web import launcher

    uvicorn_calls: list[dict[str, object]] = []

    def fail_build_web_app(data_root: Path):
        raise AssertionError("dev reload should use the app factory import string")

    def fake_uvicorn_run(app_arg, **kwargs):
        uvicorn_calls.append({"app": app_arg, **kwargs})

    monkeypatch.setattr(launcher, "build_web_app", fail_build_web_app)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_uvicorn_run))
    monkeypatch.delenv(launcher.WEB_DATA_ROOT_ENV, raising=False)

    prepared = launcher.prepare_web_run(
        launcher.WebRunRequest(
            host="127.0.0.1",
            port=8765,
            dev=True,
            data_root=tmp_path,
        )
    )
    launcher.run_web_foreground(prepared)

    assert os.environ[launcher.WEB_DATA_ROOT_ENV] == str(tmp_path)
    assert len(uvicorn_calls) == 1
    assert uvicorn_calls[0]["app"] == launcher.WEB_APP_FACTORY
    assert uvicorn_calls[0]["host"] == "127.0.0.1"
    assert uvicorn_calls[0]["port"] == 8765
    assert uvicorn_calls[0]["factory"] is True
    assert uvicorn_calls[0]["reload"] is True
    assert uvicorn_calls[0]["reload_dirs"] == launcher.web_reload_dirs()
    assert "*.py" in uvicorn_calls[0]["reload_includes"]
    assert "*.html" in uvicorn_calls[0]["reload_includes"]
    assert "*.css" in uvicorn_calls[0]["reload_includes"]


def test_create_app_from_env_uses_runtime_data_root(tmp_path: Path, monkeypatch):
    from armactl.web import app as web_app

    monkeypatch.setenv(web_app.WEB_DATA_ROOT_ENV, str(tmp_path))

    created = web_app.create_app_from_env()

    assert created.state.web_data_root == tmp_path


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if _matches_prefix(module_name, prefixes):
            sys.modules.pop(module_name)


def test_web_without_subcommand_runs_one_command_setup(tmp_path: Path, monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    password = "first owner password"
    calls: list[tuple[str, Path | None]] = []

    def fake_install(data_root: Path | None = None):
        calls.append(("install", data_root))
        return _web_install_result(
            tmp_path,
            load_web_runtime_config(data_root),
            (True, "runtime ready", 0),
            (True, "installed unit", 0),
            (True, "daemon reloaded", 0),
            (True, "enabled armactl-web.service", 0),
        )

    def fake_start() -> ServiceResult:
        calls.append(("start", None))
        return ServiceResult(True, "started armactl-web.service", 0)

    monkeypatch.setattr(web_service, "install_web_service", fake_install)
    monkeypatch.setattr(web_service, "start_web_service", fake_start)
    _stub_web_health_ready(monkeypatch)

    result = invoke_web(
        "--data-root",
        str(tmp_path),
        "--access",
        "lan",
        "--owner",
        "Admin",
        input_text=f"{password}\n{password}\n",
    )

    config = load_web_runtime_config(tmp_path)
    user = get_user_by_username(config.db_path, "admin")

    assert result.exit_code == 0
    assert config.bind_host == "0.0.0.0"
    assert config.bind_port == WEB_PANEL_DEFAULT_PORT
    assert user is not None
    assert user.role == "owner"
    assert verify_user_password(config.db_path, "admin", password) is True
    assert calls == [("install", tmp_path), ("start", None)]
    assert "armactl web setup" in result.output
    assert "Web panel setup complete." in result.output
    assert "Access:         local network" in result.output
    assert f"Bind:           0.0.0.0:{WEB_PANEL_DEFAULT_PORT}" in result.output
    assert f"URL:            http://<server-ip>:{WEB_PANEL_DEFAULT_PORT}" in result.output
    assert "Owner:          created admin" in result.output
    assert "Auto-start:     enabled after install" in result.output
    assert "Start now:      started" in result.output
    assert password not in result.output
    assert config.session_secret not in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_quickstart_prompt_defaults_to_local_access(tmp_path: Path, monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    password = "first owner password"
    calls: list[str] = []

    def fake_install(data_root: Path | None = None):
        calls.append("install")
        return _web_install_result(
            tmp_path,
            load_web_runtime_config(data_root),
            (True, "runtime ready", 0),
            (True, "installed unit", 0),
            (True, "daemon reloaded", 0),
            (True, "enabled armactl-web.service", 0),
        )

    def fake_start() -> ServiceResult:
        calls.append("start")
        return ServiceResult(True, "started armactl-web.service", 0)

    monkeypatch.setattr(web_service, "install_web_service", fake_install)
    monkeypatch.setattr(web_service, "start_web_service", fake_start)
    _stub_web_health_ready(monkeypatch)

    result = invoke_web(
        "--data-root",
        str(tmp_path),
        "--owner",
        "owner",
        input_text=f"\n{password}\n{password}\n",
    )

    config = load_web_runtime_config(tmp_path)

    assert result.exit_code == 0
    assert calls == ["install", "start"]
    assert config.bind_host == "127.0.0.1"
    assert "Access:         local machine" in result.output
    assert f"URL:            http://127.0.0.1:{WEB_PANEL_DEFAULT_PORT}" in result.output

def test_web_quickstart_existing_owner_does_not_prompt_for_password(tmp_path: Path, monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service
    from armactl.web.auth.setup import setup_owner_user

    setup_owner_user(tmp_path, "owner", "existing owner password")
    calls: list[str] = []

    def fake_install(data_root: Path | None = None):
        calls.append("install")
        return _web_install_result(
            tmp_path,
            load_web_runtime_config(data_root),
            (True, "runtime ready", 0),
            (True, "installed unit", 0),
            (True, "daemon reloaded", 0),
            (True, "enabled armactl-web.service", 0),
        )

    def fake_start() -> ServiceResult:
        calls.append("start")
        return ServiceResult(True, "started armactl-web.service", 0)

    monkeypatch.setattr(web_service, "install_web_service", fake_install)
    monkeypatch.setattr(web_service, "start_web_service", fake_start)
    _stub_web_health_ready(monkeypatch)

    result = invoke_web("--data-root", str(tmp_path), "--access", "local", input_text="")

    assert result.exit_code == 0
    assert calls == ["install", "start"]
    assert "Owner user already configured." in result.output
    assert "Owner password" not in result.output
    assert "Owner:          already configured" in result.output
    assert f"URL:            http://127.0.0.1:{WEB_PANEL_DEFAULT_PORT}" in result.output


def test_web_quickstart_install_failure_does_not_start_service(tmp_path: Path, monkeypatch):
    from armactl.web import service as web_service

    password = "first owner password"
    calls: list[str] = []

    def fake_install(data_root: Path | None = None):
        calls.append("install")
        return _web_install_result(
            tmp_path,
            load_web_runtime_config(data_root),
            (False, "runtime dependency missing", 7),
        )

    def fake_start():
        raise AssertionError("start_web_service should not run after install failure")

    monkeypatch.setattr(web_service, "install_web_service", fake_install)
    monkeypatch.setattr(web_service, "start_web_service", fake_start)
    _stub_web_health_ready(monkeypatch)

    result = invoke_web(
        "--data-root",
        str(tmp_path),
        "--access",
        "local",
        "--owner",
        "owner",
        input_text=f"{password}\n{password}\n",
    )

    assert result.exit_code == 7
    assert calls == ["install"]
    assert "Web panel setup incomplete." in result.output
    assert "Start now:      not attempted" in result.output
    assert "runtime dependency missing" in result.output
    assert "Traceback" not in result.output

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


def test_web_service_help_exists():
    result = invoke_web("service", "--help")

    assert result.exit_code == 0
    assert "Manage the production armactl web systemd service." in result.output
    assert "install" in result.output
    assert "start" in result.output
    assert "status" in result.output


def test_web_service_install_prints_safe_summary(tmp_path: Path, monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    config = ensure_web_runtime(tmp_path)
    install_result = web_service.WebServiceInstallResult(
        config=config,
        service_name="armactl-web.service",
        service_path=tmp_path / "systemd" / "armactl-web.service",
        results=(
            ServiceResult(True, "runtime ready"),
            ServiceResult(True, "installed unit"),
            ServiceResult(True, "daemon reloaded"),
            ServiceResult(True, "enabled armactl-web.service"),
        ),
    )

    def fake_install(data_root: Path | None = None):
        assert data_root == tmp_path
        return install_result

    monkeypatch.setattr(web_service, "install_web_service", fake_install)

    result = invoke_web("service", "install", "--data-root", str(tmp_path))

    assert result.exit_code == 0
    assert "Web service install prepared." in result.output
    assert "Service:        armactl-web.service" in result.output
    assert str(tmp_path / "systemd" / "armactl-web.service") in result.output
    assert str(tmp_path / "web" / "web.env") in result.output
    assert f"127.0.0.1:{WEB_PANEL_DEFAULT_PORT}" in result.output
    assert "Auto-start:     enabled after install" in result.output
    assert "Start now:      no" in result.output
    assert config.session_secret not in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_service_install_rejects_reserved_port_before_systemd(tmp_path: Path):
    env_path = tmp_path / "web" / "web.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "ARMACTL_WEB_BIND_HOST=127.0.0.1\n"
        "ARMACTL_WEB_BIND_PORT=2001\n"
        "ARMACTL_WEB_HTTPS_REQUIRED=false\n"
        "ARMACTL_WEB_SESSION_SECRET=not-a-real-test-secret\n",
        encoding="utf-8",
    )

    result = invoke_web("service", "install", "--data-root", str(tmp_path))

    assert result.exit_code == 1
    assert "Port 2001 is reserved for Arma game default." in result.output
    assert "Traceback" not in result.output


def test_web_service_lifecycle_cli_calls_service_helpers(monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    calls: list[str] = []

    def result_for(action: str):
        def wrapped() -> ServiceResult:
            calls.append(action)
            return ServiceResult(True, f"{action} armactl-web.service")

        return wrapped

    def restart_result() -> web_service.WebServiceRestartResult:
        calls.append("restart")
        return web_service.WebServiceRestartResult(
            systemctl_result=ServiceResult(True, "restart armactl-web.service"),
            http_result=ServiceResult(True, "http ready"),
            readiness_result=ServiceResult(True, "schema ready"),
            success=True,
            message="restart and health ok",
            exit_code=0,
        )

    monkeypatch.setattr(web_service, "start_web_service", result_for("start"))
    monkeypatch.setattr(web_service, "stop_web_service", result_for("stop"))
    monkeypatch.setattr(
        web_service,
        "restart_web_service_and_wait_for_readiness",
        restart_result,
    )
    monkeypatch.setattr(web_service, "enable_web_service", result_for("enable"))
    monkeypatch.setattr(web_service, "disable_web_service", result_for("disable"))
    monkeypatch.setattr(
        web_service,
        "check_web_http_health",
        lambda timeout_seconds=0.0: ServiceResult(True, "http ready"),
    )
    monkeypatch.setattr(
        web_service,
        "check_web_http_readiness",
        lambda timeout_seconds=0.0: ServiceResult(True, "schema ready"),
    )

    for command in ("start", "stop", "restart", "enable", "disable"):
        result = invoke_web("service", command)

        assert result.exit_code == 0
        assert f"Web service {command}." in result.output
        assert f"{command} armactl-web.service" in result.output
        if command == "restart":
            assert "schema ready" in result.output

    assert calls == ["start", "stop", "restart", "enable", "disable"]


def test_web_service_restart_cli_reports_health_failure(monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    monkeypatch.setattr(
        web_service,
        "restart_web_service_and_wait_for_readiness",
        lambda: web_service.WebServiceRestartResult(
            systemctl_result=ServiceResult(True, "systemctl restart ok", 0),
            http_result=ServiceResult(False, "http health timed out", 1),
            success=False,
            message="Web service restart command succeeded, but HTTP health wait failed.",
            exit_code=1,
        ),
    )

    result = invoke_web("service", "restart")

    assert result.exit_code == 1
    assert "Web service restart." in result.output
    assert "✓ systemctl restart ok" in result.output
    assert "✗ http health timed out" in result.output
    assert "Web service restart command succeeded, but HTTP health wait failed." in result.output
    assert "Traceback" not in result.output


def test_web_service_restart_cli_systemctl_failure_stays_failure(monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    monkeypatch.setattr(
        web_service,
        "restart_web_service_and_wait_for_readiness",
        lambda: web_service.WebServiceRestartResult(
            systemctl_result=ServiceResult(False, "systemctl restart failed", 7),
            http_result=ServiceResult(True, "http ready", 0),
            success=False,
            message=(
                "Web service restart command failed; HTTP health was not used as success proof."
            ),
            exit_code=7,
        ),
    )

    result = invoke_web("service", "restart")

    assert result.exit_code == 7
    assert "✗ systemctl restart failed" in result.output
    assert "✓ http ready" in result.output
    assert "HTTP health was not used as success proof" in result.output


def test_web_service_restart_cli_redacts_health_exception_noise(monkeypatch):
    from armactl.service_manager import ServiceResult
    from armactl.web import service as web_service

    def fake_restart(service_name: str, *, timeout_seconds: int | None = None) -> ServiceResult:
        assert service_name == "armactl-web.service"
        assert timeout_seconds == web_service.WEB_SERVICE_RESTART_SYSTEMCTL_TIMEOUT_SECONDS
        return ServiceResult(True, "systemctl restart ok", 0)

    def fail_health(*, timeout_seconds: float = 0.0) -> ServiceResult:
        raise RuntimeError("password=super-secret at /home/deus/projects/armactl/web.env")

    monkeypatch.setattr(web_service, "restart_service", fake_restart)
    monkeypatch.setattr(web_service, "check_web_http_health", fail_health)

    result = invoke_web("service", "restart")

    assert result.exit_code == 1
    assert "Web HTTP health wait failed:" in result.output
    assert "password=***" in result.output
    assert "super-secret" not in result.output
    assert "/home/deus" not in result.output
    assert "Traceback" not in result.output


def test_web_service_status_prints_safe_summary(tmp_path: Path, monkeypatch):
    from armactl.web import service as web_service

    config = ensure_web_runtime(tmp_path)

    def fake_status(data_root: Path | None = None) -> dict:
        assert data_root == tmp_path
        return {
            "service_name": "armactl-web.service",
            "service_file": "/etc/systemd/system/armactl-web.service",
            "installed": True,
            "active": True,
            "enabled": True,
            "active_state": "active",
            "main_pid": 321,
            "runtime": {"success": True, "message": "runtime ready", "exit_code": 0},
            "http": {"success": True, "message": "http ready", "exit_code": 0},
            "readiness": {"success": True, "message": "schema ready", "exit_code": 0},
            "config": {
                "available": True,
                "data_root": str(config.data_root),
                "runtime_dir": str(config.runtime_dir),
                "env_path": str(config.env_path),
                "bind_host": config.bind_host,
                "bind_port": config.bind_port,
                "https_required": config.https_required,
            },
        }

    monkeypatch.setattr(web_service, "get_web_service_status", fake_status)

    result = invoke_web("service", "status", "--data-root", str(tmp_path))

    assert result.exit_code == 0
    assert "Web service status." in result.output
    assert "Service:        armactl-web.service" in result.output
    assert "Installed:      yes" in result.output
    assert "Active:         yes" in result.output
    assert "Enabled:        yes" in result.output
    assert "PID:            321" in result.output
    assert f"Bind:           127.0.0.1:{WEB_PANEL_DEFAULT_PORT}" in result.output
    assert "schema ready" in result.output
    assert "HTTP check:     ✓ http ready" in result.output
    assert config.session_secret not in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_service_install_does_not_import_tui_or_textual(tmp_path: Path, monkeypatch):
    _forget_modules("armactl.tui", "textual")
    env_path = tmp_path / "web" / "web.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "ARMACTL_WEB_BIND_HOST=127.0.0.1\n"
        "ARMACTL_WEB_BIND_PORT=2001\n"
        "ARMACTL_WEB_HTTPS_REQUIRED=false\n"
        "ARMACTL_WEB_SESSION_SECRET=not-a-real-test-secret\n",
        encoding="utf-8",
    )
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, FORBIDDEN_IMPORT_PREFIXES):
            blocked_imports.append(name)
            raise AssertionError(f"web service imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = invoke_web("service", "install", "--data-root", str(tmp_path))

    assert result.exit_code == 1
    assert "Port 2001 is reserved" in result.output
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
