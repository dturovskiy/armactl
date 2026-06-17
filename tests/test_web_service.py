from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from armactl.service_manager import ServiceResult
from armactl.web.runtime import ensure_web_runtime

FORBIDDEN_IMPORT_PREFIXES = ("armactl.tui", "textual")


def _copy_web_template(project_root: Path) -> None:
    templates_dir = project_root / "templates"
    templates_dir.mkdir(parents=True)
    source = Path("templates/armactl-web.service.j2")
    (templates_dir / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def test_render_web_service_unit_is_safe_and_uses_runtime_paths(tmp_path: Path):
    from armactl.web import service

    project_root = tmp_path / "project"
    python_bin = project_root / ".venv" / "bin" / "python"
    home_dir = tmp_path / "home" / "operator"
    _copy_web_template(project_root)
    config = ensure_web_runtime(tmp_path / "data")

    unit = service.render_web_service_unit(
        config,
        project_root=project_root,
        python_bin=python_bin,
        user="operator",
        home_dir=home_dir,
    )

    assert "Description=armactl Web Panel" in unit
    assert "After=network-online.target" in unit
    assert "Wants=network-online.target" in unit
    assert f"RequiresMountsFor={project_root} {config.data_root} {config.runtime_dir}" in unit
    assert "User=operator" in unit
    assert f"WorkingDirectory={project_root}" in unit
    assert f"ExecStart={python_bin} -m armactl web run --data-root {config.data_root}" in unit
    assert "Restart=always" in unit
    assert "RestartSec=15" in unit
    assert f"Environment=HOME={home_dir}" in unit
    assert "Environment=USER=operator" in unit
    assert "Environment=PYTHONUNBUFFERED=1" in unit
    assert f"Environment=ARMACTL_WEB_DATA_ROOT={config.data_root}" in unit
    assert config.session_secret not in unit
    assert "ARMACTL_WEB_SESSION_SECRET" not in unit


def test_install_web_service_uses_systemd_helpers_without_starting(tmp_path: Path, monkeypatch):
    from armactl.web import service

    systemd_dir = tmp_path / "systemd"
    installed: dict[str, object] = {}
    enable_calls: list[str] = []

    def fake_install(source: Path, destination: Path) -> ServiceResult:
        installed["destination"] = destination
        installed["content"] = source.read_text(encoding="utf-8")
        return ServiceResult(True, f"installed {destination.name}")

    def fake_enable(service_name: str) -> ServiceResult:
        enable_calls.append(service_name)
        return ServiceResult(True, f"enabled {service_name}")

    monkeypatch.setattr(service.paths, "SYSTEMD_DIR", systemd_dir)
    monkeypatch.setattr(
        service,
        "check_web_service_runtime",
        lambda project_root=None: ServiceResult(True, "runtime ready"),
    )
    monkeypatch.setattr(service, "install_systemd_unit_file", fake_install)
    monkeypatch.setattr(service, "daemon_reload", lambda: ServiceResult(True, "reloaded"))
    monkeypatch.setattr(service, "enable_service", fake_enable)
    monkeypatch.setattr(
        service,
        "start_service",
        lambda name: pytest.fail("install must not start"),
    )

    result = service.install_web_service(tmp_path)

    assert result.service_name == "armactl-web.service"
    assert result.service_path == systemd_dir / "armactl-web.service"
    assert installed["destination"] == systemd_dir / "armactl-web.service"
    assert "ExecStart=" in installed["content"]
    assert enable_calls == ["armactl-web.service"]
    assert [item.success for item in result.results] == [True, True, True, True]


def test_install_web_service_rejects_reserved_web_port(tmp_path: Path, monkeypatch):
    from armactl.web import service

    env_path = tmp_path / "web" / "web.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "ARMACTL_WEB_BIND_HOST=127.0.0.1\n"
        "ARMACTL_WEB_BIND_PORT=2001\n"
        "ARMACTL_WEB_HTTPS_REQUIRED=false\n"
        "ARMACTL_WEB_SESSION_SECRET=not-a-real-test-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        service,
        "install_systemd_unit_file",
        lambda *args: pytest.fail("no install"),
    )

    with pytest.raises(Exception, match="Port 2001 is reserved") as exc_info:
        service.install_web_service(tmp_path)

    assert exc_info.value.__class__.__name__ == "WebRuntimeConfigError"


def test_web_service_runtime_check_verifies_armactl_import(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web import service

    project_root = tmp_path / "project"
    python_bin = project_root / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.touch()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    result = service.check_web_service_runtime(project_root)

    assert result.success is True
    assert commands == [
        [str(python_bin), "-c", "import armactl, fastapi, uvicorn, argon2, multipart"]
    ]


def test_web_service_lifecycle_helpers_call_fixed_unit(monkeypatch):
    from armactl.web import service

    calls: list[tuple[str, str]] = []

    def record(action: str):
        def wrapped(service_name: str) -> ServiceResult:
            calls.append((action, service_name))
            return ServiceResult(True, f"{action} ok")

        return wrapped

    monkeypatch.setattr(service, "start_service", record("start"))
    monkeypatch.setattr(service, "stop_service", record("stop"))
    monkeypatch.setattr(service, "restart_service", record("restart"))
    monkeypatch.setattr(service, "enable_service", record("enable"))
    monkeypatch.setattr(service, "disable_service", record("disable"))

    assert service.start_web_service().success is True
    assert service.stop_web_service().success is True
    assert service.restart_web_service().success is True
    assert service.enable_web_service().success is True
    assert service.disable_web_service().success is True
    assert calls == [
        ("start", "armactl-web.service"),
        ("stop", "armactl-web.service"),
        ("restart", "armactl-web.service"),
        ("enable", "armactl-web.service"),
        ("disable", "armactl-web.service"),
    ]


def test_web_service_status_uses_service_manager_and_redacts_config(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web import service

    config = ensure_web_runtime(tmp_path)
    monkeypatch.setattr(
        service,
        "get_service_status",
        lambda service_name: {
            "service_name": service_name,
            "active": True,
            "enabled": True,
            "active_state": "active",
            "main_pid": 123,
        },
    )
    monkeypatch.setattr(
        service,
        "check_web_service_runtime",
        lambda project_root=None: ServiceResult(True, "runtime ready"),
    )

    status = service.get_web_service_status(tmp_path)

    assert status["service_name"] == "armactl-web.service"
    assert status["active"] is True
    assert status["enabled"] is True
    assert status["runtime"]["success"] is True
    assert status["config"]["bind_port"] == config.bind_port
    assert status["config"]["exposure_warning"] is None
    assert config.session_secret not in str(status)
    assert "session_secret" not in str(status)


def test_web_service_module_does_not_import_tui_or_textual(
    assert_import_does_not_import_modules,
):
    assert_import_does_not_import_modules(
        "armactl.web.service",
        FORBIDDEN_IMPORT_PREFIXES,
    )
