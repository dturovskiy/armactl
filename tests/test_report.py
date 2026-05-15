"""Tests for diagnostic support report generation."""

from __future__ import annotations

from click.testing import CliRunner

from armactl import report
from armactl.cli import main
from armactl.state import PortInfo, ServerState


def test_build_report_redacts_secrets_and_keeps_sections(monkeypatch):
    state = ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        service_exists=True,
        timer_exists=True,
        server_running=True,
        instance_root="/srv/armactl-data/default",
        install_dir="/srv/armactl-data/default/server",
        config_path="/srv/armactl-data/default/config/config.json",
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )

    monkeypatch.setattr(report, "discover", lambda instance, save=False: state)
    monkeypatch.setattr(
        report,
        "get_service_status",
        lambda name: {"active": True, "main_pid": 1234, "secret": "token=123456:SECRET"},
    )
    monkeypatch.setattr(report, "get_timer_status", lambda name: {"enabled": True})

    def command_runner(cmd: list[str], timeout: int) -> str:
        return (
            "$ " + " ".join(cmd)
            + "\npassword=supersecret\nARMACTL_BOT_TOKEN=123456:ABCDEFSECRET"
        )

    text = report.build_report(
        "default",
        include_journal=False,
        command_runner=command_runner,
    )

    assert "== armactl report ==" in text
    assert "== process ==" in text
    assert "== server FPS telemetry ==" in text
    assert "supersecret" not in text
    assert "123456:ABCDEFSECRET" not in text
    assert "password=***" in text
    assert "ARMACTL_BOT_TOKEN=***" in text


def test_cli_report_command_prints_report(monkeypatch):
    monkeypatch.setattr(
        "armactl.report.build_report",
        lambda instance, lines, include_journal: (
            f"report for {instance} {lines} {include_journal}\n"
        ),
    )

    result = CliRunner().invoke(main, ["--instance", "default", "report", "--no-journal"])

    assert result.exit_code == 0
    assert result.output == "report for default 120 False\n"
