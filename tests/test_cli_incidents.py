"""CLI coverage for the supervised incident evidence monitor."""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from armactl.cli import main


def test_incident_monitor_run_requires_explicit_once() -> None:
    result = CliRunner().invoke(main, ["incidents", "monitor", "run"])

    assert result.exit_code == 1
    assert "Only --once is supported" in result.output


def test_incident_monitor_run_reports_storage(monkeypatch, tmp_path) -> None:
    captured = {}
    fake = SimpleNamespace(
        success=True,
        captured=1,
        updated=0,
        ignored=2,
        status_path=str(tmp_path / "default" / "incidents" / "monitor-status.json"),
        error="",
        exit_code=0,
        to_dict=lambda: {"captured": 1},
    )

    def collect(instance, *, data_root):
        captured["instance"] = instance
        captured["data_root"] = data_root
        return fake

    monkeypatch.setattr("armactl.incident_monitor.collect_incidents_once", collect)

    result = CliRunner().invoke(
        main,
        ["incidents", "monitor", "run", "--once", "--data-root", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert captured == {"instance": "default", "data_root": tmp_path}
    assert "captured=1" in result.output
    assert fake.status_path in result.output


def test_incident_monitor_status_supports_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "armactl.incident_monitor_service.get_incident_monitor_status",
        lambda instance, *, data_root: {
            "instance": instance,
            "timer": {"enabled": True},
            "collector": {"storage": str(data_root / instance / "incidents")},
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "--json-output",
            "incidents",
            "monitor",
            "status",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert '"instance": "default"' in result.output
    assert str(tmp_path / "default" / "incidents") in result.output
