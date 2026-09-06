"""Read-only server incident history page model."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from armactl import metrics, paths

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
    return {
        "instance": instance,
        "incidents": [asdict(incident) for incident in incidents],
        "count": len(incidents),
        "history_days": INCIDENT_HISTORY_DAYS,
        "history_limit": INCIDENT_HISTORY_LIMIT,
    }
