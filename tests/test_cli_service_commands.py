"""CLI service commands must handle systemd transition states conservatively."""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from armactl.cli import main
from armactl.service_manager import ServiceResult


def test_stop_calls_systemd_even_when_discovery_reports_not_running(monkeypatch):
    """An auto-restart delay is not proof that the systemd unit is stopped."""
    calls: list[str] = []
    monkeypatch.setattr(
        "armactl.cli._get_state",
        lambda ctx: SimpleNamespace(
            server_installed=True,
            server_running=False,
            service_name="armareforger.service",
        ),
    )
    monkeypatch.setattr(
        "armactl.service_manager.stop_service",
        lambda service_name: (
            calls.append(service_name)
            or ServiceResult(True, "stopped", 0)
        ),
    )

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0
    assert calls == ["armareforger.service"]
    assert "Server stopped successfully" in result.output
