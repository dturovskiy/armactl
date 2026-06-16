"""Persistent pending-restart marker for web-visible operator state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.runtime.db import ensure_web_db

REASON_CONFIG = "config"
REASON_ADMINS = "admins"
REASON_MODS = "mods"
MAX_DETAILS_LENGTH = 500


@dataclass(frozen=True)
class PendingRestart:
    """One pending restart marker for changes saved through web."""

    instance: str
    reason: str
    source_action: str
    details: str
    created_at: str
    updated_at: str
    created_by_username: str

    @property
    def reason_label(self) -> str:
        return {
            REASON_CONFIG: "Config changes",
            REASON_ADMINS: "Admin changes",
            REASON_MODS: "Mod changes",
        }.get(self.reason, "Saved changes")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, *, max_length: int = MAX_DETAILS_LENGTH) -> str:
    redacted = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(redacted) > max_length:
        return f"{redacted[:max_length]}..."
    return redacted


def _normalize_instance(instance: str) -> str:
    return (instance or paths.DEFAULT_INSTANCE_NAME).strip() or paths.DEFAULT_INSTANCE_NAME


def mark_pending_restart(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    reason: str,
    source_action: str,
    username: str,
    details: object = "",
) -> PendingRestart:
    """Create or update the marker telling operators a restart is pending."""
    ensure_web_db(db_path)
    normalized_instance = _normalize_instance(instance)
    safe_reason = _safe_text(reason, max_length=80)
    safe_source_action = _safe_text(source_action, max_length=120)
    safe_username = _safe_text(username, max_length=120) or "unknown"
    safe_details = _safe_text(details)
    now = _utc_timestamp()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO web_pending_restarts(
                instance,
                reason,
                source_action,
                details,
                created_at,
                updated_at,
                created_by_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance) DO UPDATE SET
                reason = excluded.reason,
                source_action = excluded.source_action,
                details = excluded.details,
                updated_at = excluded.updated_at,
                created_by_username = excluded.created_by_username
            """,
            (
                normalized_instance,
                safe_reason,
                safe_source_action,
                safe_details,
                now,
                now,
                safe_username,
            ),
        )
    marker = get_pending_restart(db_path, instance=normalized_instance)
    assert marker is not None
    return marker


def _row_to_pending_restart(row: sqlite3.Row) -> PendingRestart:
    return PendingRestart(
        instance=row["instance"],
        reason=row["reason"],
        source_action=row["source_action"],
        details=row["details"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by_username=row["created_by_username"],
    )


def get_pending_restart(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PendingRestart | None:
    """Return the pending restart marker for an instance, if present."""
    ensure_web_db(db_path)
    normalized_instance = _normalize_instance(instance)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT instance, reason, source_action, details, created_at, updated_at,
                   created_by_username
            FROM web_pending_restarts
            WHERE instance = ?
            """,
            (normalized_instance,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_pending_restart(row)


def clear_pending_restart(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> bool:
    """Clear the marker after a successful server restart."""
    ensure_web_db(db_path)
    normalized_instance = _normalize_instance(instance)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM web_pending_restarts WHERE instance = ?",
            (normalized_instance,),
        )
    return cursor.rowcount > 0
