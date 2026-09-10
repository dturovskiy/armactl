"""Read-only server incident history page model."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from armactl import incident_monitor, metrics, paths

INCIDENT_HISTORY_DAYS = 30
INCIDENT_HISTORY_LIMIT = 50
INCIDENT_HISTORY_LOG_LIMIT = 120


def load_incidents_page(
    instance: str,
    *,
    data_root: Path,
) -> dict[str, Any]:
    """Load bounded historical crash evidence without mutating the server."""
    incidents = metrics.query_recent_server_incidents(
        paths.config_dir(instance, data_root),
        max_incidents=INCIDENT_HISTORY_LIMIT,
        max_age_seconds=INCIDENT_HISTORY_DAYS * 24 * 60 * 60,
        max_log_files=INCIDENT_HISTORY_LOG_LIMIT,
    )
    rows: list[dict[str, Any]] = []
    for incident in incidents:
        row = asdict(incident)
        incident_id = str(row.get("incident_id") or "")
        row["artifact_links"] = [
            {
                "name": name,
                "href": (
                    f"/incidents/{quote(incident_id, safe='')}/artifact/"
                    f"{quote(name, safe='/')}"
                ),
            }
            for name in row.get("artifacts", ())
            if incident_id and isinstance(name, str)
        ]
        rows.append(row)
    return {
        "instance": instance,
        "incidents": rows,
        "count": len(incidents),
        "history_days": INCIDENT_HISTORY_DAYS,
        "history_limit": INCIDENT_HISTORY_LIMIT,
        "collector": incident_monitor.read_monitor_status(instance, data_root=data_root),
    }
