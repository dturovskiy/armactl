"""Tests for the read-only server incident history page model."""

from __future__ import annotations

import json
from pathlib import Path

from web_route_helpers import _client, _login

from armactl.metrics import ServerIncident
from armactl.web.auth.setup import setup_owner_user
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


def test_incident_page_links_only_collector_allowlisted_artifacts(tmp_path: Path) -> None:
    from armactl.web.app import create_app

    password = "owner incident password"
    setup_owner_user(tmp_path, "owner", password)
    bundle = tmp_path / "default" / "incidents" / "20260909T141310Z-memory"
    (bundle / "engine").mkdir(parents=True)
    (bundle / "journal.log").write_text("double free or corruption (!prev)\n", encoding="utf-8")
    (bundle / "engine" / "error.log").write_text(
        "Application hangs (force crash) 301 s\n", encoding="utf-8"
    )
    (bundle / "secret.txt").write_text("not allowlisted\n", encoding="utf-8")
    (bundle / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": bundle.name,
                "occurred_at": "2026-09-09T14:13:10+00:00",
                "captured_at": "2026-09-09T14:13:20+00:00",
                "kind": "memory_corruption",
                "severity": "error",
                "summary": "Native memory corruption",
                "suspect": "Enfusion native heap / addon-triggered engine path",
                "confidence": "high",
                "reason": "Allocator reported a double free.",
                "evidence": ["double free or corruption (!prev)"],
                "confirmed": True,
                "pid": 38222,
                "artifacts": ["journal.log", "engine/error.log"],
                "bundle": f"incidents/{bundle.name}",
            }
        ),
        encoding="utf-8",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    page = client.get("/incidents", follow_redirects=False)
    allowed = client.get(
        f"/incidents/{bundle.name}/artifact/engine/error.log",
        follow_redirects=False,
    )
    denied = client.get(
        f"/incidents/{bundle.name}/artifact/secret.txt",
        follow_redirects=False,
    )

    assert page.status_code == 200
    assert f"/incidents/{bundle.name}/artifact/journal.log" in page.text
    assert f"/incidents/{bundle.name}/artifact/engine/error.log" in page.text
    assert "secret.txt" not in page.text
    assert allowed.status_code == 200
    assert "Application hangs" in allowed.text
    assert allowed.headers["cache-control"] == "no-store"
    assert denied.status_code == 404
