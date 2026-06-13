"""Tests for web exposure warnings."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from armactl.cli import main
from armactl.ports import WEB_PANEL_DEFAULT_PORT
from armactl.web.security.exposure import (
    EXTERNAL_BIND_WARNING,
    EXTERNAL_BIND_WITHOUT_HTTPS_WARNING,
    get_exposure_warning,
    is_external_bind,
)


def _invoke_web(*args: str):
    return CliRunner().invoke(main, ["web", *args])


def test_localhost_bind_has_no_warning():
    for bind_host in ("127.0.0.1", "::1", "localhost"):
        assert is_external_bind(bind_host) is False
        assert get_exposure_warning(bind_host, https_required=False) is None


def test_unspecified_bind_without_https_gets_strong_warning():
    warning = get_exposure_warning("0.0.0.0", https_required=False)

    assert is_external_bind("0.0.0.0") is True
    assert warning is not None
    assert warning.severity == "danger"
    assert warning.message == EXTERNAL_BIND_WITHOUT_HTTPS_WARNING


def test_external_ip_with_https_gets_soft_warning():
    warning = get_exposure_warning("203.0.113.10", https_required=True)

    assert is_external_bind("203.0.113.10") is True
    assert warning is not None
    assert warning.severity == "warning"
    assert warning.message == EXTERNAL_BIND_WARNING


def test_unknown_hostname_is_treated_as_external():
    warning = get_exposure_warning("example.internal", https_required=False)

    assert is_external_bind("example.internal") is True
    assert warning is not None
    assert warning.message == EXTERNAL_BIND_WITHOUT_HTTPS_WARNING


def test_web_init_external_bind_warning_is_safe(tmp_path: Path):
    result = _invoke_web(
        "init",
        "--data-root",
        str(tmp_path),
        "--host",
        "0.0.0.0",
        "--port",
        str(WEB_PANEL_DEFAULT_PORT + 11),
    )

    assert result.exit_code == 0
    assert "Exposure warning:" in result.output
    assert EXTERNAL_BIND_WITHOUT_HTTPS_WARNING in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_run_external_bind_warning_is_safe(tmp_path: Path, monkeypatch):
    from armactl.web import launcher

    calls = []
    monkeypatch.setattr(launcher, "run_web_foreground", lambda prepared: calls.append(prepared))

    result = _invoke_web(
        "run",
        "--data-root",
        str(tmp_path),
        "--host",
        "0.0.0.0",
        "--port",
        str(WEB_PANEL_DEFAULT_PORT + 12),
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "Exposure warning:" in result.output
    assert EXTERNAL_BIND_WITHOUT_HTTPS_WARNING in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output


def test_web_service_status_external_bind_warning_is_safe(tmp_path: Path, monkeypatch):
    from armactl.web import service as web_service

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
            "config": {
                "available": True,
                "data_root": str(tmp_path),
                "runtime_dir": str(tmp_path / "web"),
                "env_path": str(tmp_path / "web" / "web.env"),
                "bind_host": "0.0.0.0",
                "bind_port": WEB_PANEL_DEFAULT_PORT + 13,
                "https_required": False,
            },
        }

    monkeypatch.setattr(web_service, "get_web_service_status", fake_status)

    result = _invoke_web("service", "status", "--data-root", str(tmp_path))

    assert result.exit_code == 0
    assert "Exposure warning:" in result.output
    assert EXTERNAL_BIND_WITHOUT_HTTPS_WARNING in result.output
    assert "ARMACTL_WEB_SESSION_SECRET" not in result.output
