"""CLI coverage for the supervised incident evidence monitor."""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from armactl.cli import main
from armactl.metrics import ServerIncident


def _incident(**overrides) -> ServerIncident:
    values = {
        "occurred_at": "2026-09-13T16:18:11+00:00",
        "kind": "hang",
        "severity": "error",
        "summary": "Enfusion main game thread stall",
        "suspect": "Active addon stack",
        "confidence": "medium",
        "reason": "The engine watchdog forced a crash after 301 seconds.",
        "evidence": ("Application hangs (force crash) 301 s",),
        "source": "collector",
        "incident_id": "20260913T161811Z-hang-abc123",
        "captured_at": "2026-09-13T16:18:13+00:00",
        "bundle": "/private/runtime/path/must-not-render",
        "confirmed": True,
        "pid": 1234,
        "artifacts": ("journal.log", "runtime.json"),
    }
    values.update(overrides)
    return ServerIncident(**values)


def test_incidents_list_uses_bounded_shared_history(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setattr(
        "armactl.metrics.query_recent_server_incidents",
        lambda config_dir, **kwargs: calls.append((config_dir, kwargs)) or (_incident(),),
    )

    result = CliRunner().invoke(
        main,
        [
            "incidents",
            "list",
            "--limit",
            "7",
            "--days",
            "14",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            tmp_path / "default" / "config",
            {
                "max_incidents": 7,
                "max_age_seconds": 14 * 24 * 60 * 60,
                "max_log_files": 7,
            },
        )
    ]
    assert "20260913T161811Z-hang-abc123" in result.output
    assert "Enfusion main game thread stall" in result.output
    assert "Active addon stack" in result.output
    assert "/private/runtime/path" not in result.output


def test_incidents_list_json_excludes_bundle_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "armactl.metrics.query_recent_server_incidents",
        lambda *args, **kwargs: (_incident(),),
    )

    result = CliRunner().invoke(
        main,
        [
            "--json-output",
            "incidents",
            "list",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert '"count": 1' in result.output
    assert '"artifacts": [' in result.output
    assert '"bundle"' not in result.output
    assert "/private/runtime/path" not in result.output


def test_incidents_show_renders_bounded_explanation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "armactl.metrics.query_recent_server_incidents",
        lambda *args, **kwargs: (_incident(),),
    )

    result = CliRunner().invoke(
        main,
        [
            "incidents",
            "show",
            "20260913T161811Z-hang-abc123",
            "--data-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Application hangs (force crash) 301 s" in result.output
    assert "journal.log" in result.output
    assert "The engine watchdog forced a crash" in result.output
    assert "/private/runtime/path" not in result.output


def test_incidents_show_fails_closed_for_unknown_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "armactl.metrics.query_recent_server_incidents",
        lambda *args, **kwargs: (),
    )

    result = CliRunner().invoke(
        main,
        ["incidents", "show", "missing", "--data-root", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Incident was not found in the retained window." in result.output


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
