"""Persistent pending operator work for web-visible runtime state."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.runtime.db import ensure_web_db

KIND_CONFIG = "config"
KIND_ADMINS = "admins"
KIND_MODS = "mods"
KIND_SCHEDULE = "schedule"
RESOLUTION_RESTART_GAME_SERVER = "restart game server"
DEFAULT_PENDING_WORK_LIMIT = 50
MAX_TEXT_LENGTH = 500
MAX_SHORT_TEXT_LENGTH = 160

_SOURCE_PATHS = {
    KIND_CONFIG: "/config",
    KIND_ADMINS: "/admins",
    KIND_MODS: "/mods",
    KIND_SCHEDULE: "/schedule",
}
_KIND_LABELS = {
    KIND_CONFIG: "Config changes",
    KIND_ADMINS: "Admin changes",
    KIND_MODS: "Mod changes",
    KIND_SCHEDULE: "Schedule changes",
}
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b((?:ARMACTL_[A-Z0-9_]*SECRET|session_secret|secret|api_key)\s*[=:]\s*)([^\s,;]+)"
)


@dataclass(frozen=True)
class PendingWorkItem:
    """One saved operator action waiting for a human resolution step."""

    id: int
    instance: str
    kind: str
    source_path: str
    source_action: str
    title: str
    details: str
    resolution_action: str
    created_at: str
    updated_at: str
    created_by_username: str

    @property
    def kind_label(self) -> str:
        return _KIND_LABELS.get(self.kind, "Saved changes")

    @property
    def resolution_action_label(self) -> str:
        if self.resolution_action == RESOLUTION_RESTART_GAME_SERVER:
            return "Restart game server"
        return self.resolution_action


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, *, max_length: int = MAX_TEXT_LENGTH) -> str:
    text = redact_sensitive_text(value)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1***", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = "".join(char if ord(char) >= 32 else " " for char in text)
    text = text.strip()
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def _normalize_instance(instance: str) -> str:
    return (instance or paths.DEFAULT_INSTANCE_NAME).strip() or paths.DEFAULT_INSTANCE_NAME


def _default_source_path(kind: str) -> str:
    return _SOURCE_PATHS.get(kind, "/dashboard")


def _default_title(kind: str) -> str:
    return _KIND_LABELS.get(kind, "Saved changes")


def _safe_source_path(value: object) -> str:
    source_path = _safe_text(value, max_length=MAX_SHORT_TEXT_LENGTH)
    if not source_path.startswith("/") or source_path.startswith("//"):
        return "/dashboard"
    return source_path


def _connect(db_path: Path) -> sqlite3.Connection:
    ensure_web_db(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_item(row: sqlite3.Row) -> PendingWorkItem:
    return PendingWorkItem(
        id=row["id"],
        instance=row["instance"],
        kind=row["kind"],
        source_path=row["source_path"],
        source_action=row["source_action"],
        title=row["title"],
        details=row["details"],
        resolution_action=row["resolution_action"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by_username=row["created_by_username"],
    )


def upsert_pending_work(
    db_path: Path,
    *,
    kind: str,
    source_path: str,
    title: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    resolution_action: str = RESOLUTION_RESTART_GAME_SERVER,
) -> PendingWorkItem:
    """Create or update one pending work item without erasing other categories."""
    normalized_instance = _normalize_instance(instance)
    safe_kind = _safe_text(kind, max_length=80) or "saved"
    safe_source_path = _safe_source_path(source_path)
    safe_source_action = _safe_text(source_action, max_length=MAX_SHORT_TEXT_LENGTH)
    safe_title = _safe_text(title, max_length=MAX_SHORT_TEXT_LENGTH) or "Saved changes"
    safe_username = _safe_text(username, max_length=MAX_SHORT_TEXT_LENGTH) or "unknown"
    safe_details = _safe_text(details)
    safe_resolution = (
        _safe_text(resolution_action, max_length=MAX_SHORT_TEXT_LENGTH)
        or RESOLUTION_RESTART_GAME_SERVER
    )
    now = _utc_timestamp()

    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO web_pending_work(
                instance,
                kind,
                source_path,
                source_action,
                title,
                details,
                resolution_action,
                created_at,
                updated_at,
                created_by_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance, kind, resolution_action) DO UPDATE SET
                source_path = excluded.source_path,
                source_action = excluded.source_action,
                title = excluded.title,
                details = excluded.details,
                updated_at = excluded.updated_at,
                created_by_username = excluded.created_by_username
            """,
            (
                normalized_instance,
                safe_kind,
                safe_source_path,
                safe_source_action,
                safe_title,
                safe_details,
                safe_resolution,
                now,
                now,
                safe_username,
            ),
        )

    item = get_pending_work(
        db_path,
        instance=normalized_instance,
        kind=safe_kind,
        resolution_action=safe_resolution,
    )
    assert item is not None
    return item


def mark_restart_pending(
    db_path: Path,
    *,
    kind: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    source_path: str | None = None,
    title: str | None = None,
) -> PendingWorkItem:
    """Record saved work that needs a game server restart to apply."""
    return upsert_pending_work(
        db_path,
        kind=kind,
        source_path=source_path or _default_source_path(kind),
        source_action=source_action,
        title=title or _default_title(kind),
        username=username,
        details=details,
        instance=instance,
        resolution_action=RESOLUTION_RESTART_GAME_SERVER,
    )


def get_pending_work(
    db_path: Path,
    *,
    kind: str,
    resolution_action: str = RESOLUTION_RESTART_GAME_SERVER,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PendingWorkItem | None:
    """Return one pending work item for an instance/category/resolution."""
    normalized_instance = _normalize_instance(instance)
    with _connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT id, instance, kind, source_path, source_action, title, details,
                   resolution_action, created_at, updated_at, created_by_username
            FROM web_pending_work
            WHERE instance = ? AND kind = ? AND resolution_action = ?
            """,
            (normalized_instance, kind, resolution_action),
        ).fetchone()
    if row is None:
        return None
    return _row_to_item(row)


def list_pending_work(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    limit: int = DEFAULT_PENDING_WORK_LIMIT,
) -> list[PendingWorkItem]:
    """List pending operator work newest-first for one instance."""
    normalized_instance = _normalize_instance(instance)
    bounded_limit = max(1, min(limit, DEFAULT_PENDING_WORK_LIMIT))
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, instance, kind, source_path, source_action, title, details,
                   resolution_action, created_at, updated_at, created_by_username
            FROM web_pending_work
            WHERE instance = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_instance, bounded_limit),
        ).fetchall()
    return [_row_to_item(row) for row in rows]


def clear_pending_work(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    resolution_action: str | None = None,
) -> int:
    """Clear pending work for an instance, optionally scoped to one resolution."""
    normalized_instance = _normalize_instance(instance)
    with _connect(db_path) as connection:
        if resolution_action is None:
            cursor = connection.execute(
                "DELETE FROM web_pending_work WHERE instance = ?",
                (normalized_instance,),
            )
        else:
            cursor = connection.execute(
                """
                DELETE FROM web_pending_work
                WHERE instance = ? AND resolution_action = ?
                """,
                (normalized_instance, resolution_action),
            )
    return cursor.rowcount


def clear_restart_pending_work(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> int:
    """Clear only pending work resolved by a successful game server restart."""
    normalized_instance = _normalize_instance(instance)
    with _connect(db_path) as connection:
        work_cursor = connection.execute(
            """
            DELETE FROM web_pending_work
            WHERE instance = ? AND resolution_action = ?
            """,
            (normalized_instance, RESOLUTION_RESTART_GAME_SERVER),
        )
        legacy_cursor = connection.execute(
            "DELETE FROM web_pending_restarts WHERE instance = ?",
            (normalized_instance,),
        )
    return work_cursor.rowcount + legacy_cursor.rowcount
