"""Durable recovery records for uncertain native moderation outcomes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl.web.runtime.db import ensure_web_db

ACTION_BAN = "ban"
ACTION_UNBAN = "unban"
SUPPORTED_ACTIONS = frozenset({ACTION_BAN, ACTION_UNBAN})

REASON_CLASS_NONE = "none"
REASON_CLASS_PROVIDED = "provided"
SUPPORTED_REASON_CLASSES = frozenset({REASON_CLASS_NONE, REASON_CLASS_PROVIDED})

STATE_PENDING_VERIFICATION = "pending_verification"
STATE_PENDING_OUTCOME_AUDIT = "pending_outcome_audit"
STATE_RESOLVED_CHANGED = "resolved_changed"
STATE_RESOLVED_NOOP = "resolved_noop"
STATE_RESOLVED_FAILED = "resolved_failed"
PENDING_STATES = frozenset({STATE_PENDING_VERIFICATION, STATE_PENDING_OUTCOME_AUDIT})
RESOLVED_STATES = frozenset({STATE_RESOLVED_CHANGED, STATE_RESOLVED_NOOP, STATE_RESOLVED_FAILED})
ALL_STATES = PENDING_STATES | RESOLVED_STATES


class ModerationVerificationError(RuntimeError):
    """Raised when moderation verification storage is unavailable."""


@dataclass(frozen=True)
class ModerationVerificationRecord:
    """One recovery record; never authoritative ban state."""

    id: int
    instance: str
    action: str
    reliable_identity: str
    reason_class: str
    verification_state: str
    created_at: str
    updated_at: str


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_from_row(row: sqlite3.Row) -> ModerationVerificationRecord:
    return ModerationVerificationRecord(
        id=int(row["id"]),
        instance=str(row["instance"]),
        action=str(row["action"]),
        reliable_identity=str(row["reliable_identity"]),
        reason_class=str(row["reason_class"]),
        verification_state=str(row["verification_state"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _validate_record_values(
    *,
    instance: str,
    action: str,
    reliable_identity: str,
    reason_class: str,
    verification_state: str,
) -> None:
    if not instance.strip() or len(instance) > 64:
        raise ValueError("Moderation verification instance is invalid.")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError("Moderation verification action is invalid.")
    if not reliable_identity.strip() or len(reliable_identity) > 128:
        raise ValueError("Moderation verification identity is invalid.")
    if reason_class not in SUPPORTED_REASON_CLASSES:
        raise ValueError("Moderation verification reason class is invalid.")
    if verification_state not in ALL_STATES:
        raise ValueError("Moderation verification state is invalid.")


def create_moderation_verification(
    db_path: Path,
    *,
    instance: str,
    action: str,
    reliable_identity: str,
    reason_class: str,
    verification_state: str = STATE_PENDING_VERIFICATION,
) -> ModerationVerificationRecord:
    """Create one pending recovery record without storing ban truth."""
    _validate_record_values(
        instance=instance,
        action=action,
        reliable_identity=reliable_identity,
        reason_class=reason_class,
        verification_state=verification_state,
    )
    if verification_state not in PENDING_STATES:
        raise ValueError("A new moderation verification must be pending.")
    timestamp = _utc_timestamp()
    try:
        ensure_web_db(Path(db_path))
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                """
                INSERT INTO web_moderation_verifications(
                    instance,
                    action,
                    reliable_identity,
                    reason_class,
                    verification_state,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance,
                    action,
                    reliable_identity,
                    reason_class,
                    verification_state,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM web_moderation_verifications
                WHERE id = ?
                """,
                (int(cursor.lastrowid),),
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise ModerationVerificationError(
            "Moderation verification recovery storage is unavailable."
        ) from exc
    if row is None:
        raise ModerationVerificationError(
            "Moderation verification recovery record was not created."
        )
    return _record_from_row(row)


def get_moderation_verification(
    db_path: Path,
    record_id: int,
) -> ModerationVerificationRecord | None:
    """Return one recovery record without mutating it."""
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
        return None
    try:
        ensure_web_db(Path(db_path))
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT *
                FROM web_moderation_verifications
                WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise ModerationVerificationError(
            "Moderation verification recovery storage is unavailable."
        ) from exc
    return _record_from_row(row) if row is not None else None


def list_pending_moderation_verifications(
    db_path: Path,
    *,
    instance: str | None = None,
    limit: int = 100,
) -> tuple[ModerationVerificationRecord, ...]:
    """List bounded pending recovery records for future operator UI."""
    bounded_limit = max(1, min(int(limit), 100))
    query = """
        SELECT *
        FROM web_moderation_verifications
        WHERE verification_state IN (?, ?)
    """
    parameters: list[object] = [
        STATE_PENDING_VERIFICATION,
        STATE_PENDING_OUTCOME_AUDIT,
    ]
    if instance is not None:
        query += " AND instance = ?"
        parameters.append(instance)
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    parameters.append(bounded_limit)
    try:
        ensure_web_db(Path(db_path))
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, tuple(parameters)).fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise ModerationVerificationError(
            "Moderation verification recovery storage is unavailable."
        ) from exc
    return tuple(_record_from_row(row) for row in rows)


def set_moderation_verification_state(
    db_path: Path,
    record_id: int,
    verification_state: str,
) -> ModerationVerificationRecord | None:
    """Update only the verification state and timestamp of one record."""
    if verification_state not in ALL_STATES:
        raise ValueError("Moderation verification state is invalid.")
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
        return None
    timestamp = _utc_timestamp()
    try:
        ensure_web_db(Path(db_path))
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                UPDATE web_moderation_verifications
                SET verification_state = ?, updated_at = ?
                WHERE id = ?
                """,
                (verification_state, timestamp, record_id),
            )
            row = connection.execute(
                """
                SELECT *
                FROM web_moderation_verifications
                WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise ModerationVerificationError(
            "Moderation verification recovery storage is unavailable."
        ) from exc
    return _record_from_row(row) if row is not None else None
