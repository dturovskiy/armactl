"""Tests for the read-only server incident history page model."""

from __future__ import annotations

from pathlib import Path

from armactl.metrics import ServerIncident
from armactl.web.page_models import incidents as incidents_page_model


def test_incidents_page_loads_a_larger_bounded_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def recent_incidents(config_dir: Path, **kwargs):
        captured["config_dir"] = config_dir
        captured.update(kwargs)
        return (
            ServerIncident(
                occurred_at="2026-09-06T16:29:42+00:00",
                kind="runtime_crash",
                severity="error",
                summary="Native game crash (crash dump)",
                suspect="ATGM / CLBR weapon stack",
                confidence="high",
                reason="Kornet activity preceded the crash.",
                evidence=("SpawnEntityPrefab Tripod_KORNET.et",),
            ),
        )

    monkeypatch.setattr(
        incidents_page_model.metrics,
        "query_recent_server_incidents",
        recent_incidents,
    )

    page = incidents_page_model.load_incidents_page("default", data_root=tmp_path)

    assert captured == {
        "config_dir": tmp_path / "default" / "config",
        "max_incidents": incidents_page_model.INCIDENT_HISTORY_LIMIT,
        "max_age_seconds": incidents_page_model.INCIDENT_HISTORY_DAYS * 24 * 60 * 60,
        "max_log_files": incidents_page_model.INCIDENT_HISTORY_LOG_LIMIT,
    }
    assert page["count"] == 1
    assert page["history_days"] == 30
    assert page["history_limit"] == 50
    assert page["incidents"][0]["suspect"] == "ATGM / CLBR weapon stack"
