"""SQLite-backed session primitives for the web panel."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from armactl.web.auth.models import InvalidAuthInputError, UserRecord, WebAuthError
from armactl.web.auth.tokens import digest_token, generate_token, token_digest_matches
from armactl.web.runtime.db import ensure_web_db

DEFAULT_SESSION_TTL_SECONDS = 60 * 60 * 24 * 7


@dataclass(frozen=True)
class SessionRecord:
    """Persisted web session metadata without the raw bearer token."""

    id: int
    user_id: int
    token_digest: str = field(repr=False)
    expires_at: str
    created_at: str
    last_seen_at: str
    is_revoked: bool = False
    revoked_at: str | None = None


@dataclass(frozen=True)
class SessionCreation:
    """New session result containing the raw token for the caller once."""

    session: SessionRecord
    token: str = field(repr=False)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.isoformat()


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_future_timestamp(value: str, now: datetime) -> bool:
    parsed = _parse_timestamp(value)
    return parsed is not None and parsed > now


def _require_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidAuthInputError(f"{field_name} must be a positive integer.")
    return value


def _connect(db_path: Path) -> sqlite3.Connection:
    try:
        ensure_web_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
    except (OSError, sqlite3.Error) as e:
        raise WebAuthError("Failed to open web auth database.") from e

    connection.row_factory = sqlite3.Row
    return connection


def _record_from_row(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        user_id=row["user_id"],
        token_digest=row["token_digest"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        is_revoked=bool(row["is_revoked"]),
        revoked_at=row["revoked_at"],
    )


def _user_record_from_row(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _ensure_active_user(connection: sqlite3.Connection, user_id: int) -> None:
    row = connection.execute(
        """
        SELECT 1
        FROM web_users
        WHERE id = ?
          AND is_active = 1
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise InvalidAuthInputError("Active web user is required.")


def create_session(
    db_path: Path,
    user_id: int,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> SessionCreation:
    """Create a session for an active user, returning the raw token once."""
    normalized_user_id = _require_positive_int(user_id, "user_id")
    normalized_ttl = _require_positive_int(ttl_seconds, "ttl_seconds")
    now = _utc_now()
    now_text = _format_timestamp(now)
    expires_at = _format_timestamp(now + timedelta(seconds=normalized_ttl))
    token = generate_token()
    token_digest = digest_token(token)
    if token_digest is None:
        raise WebAuthError("Failed to create web session.")

    try:
        with _connect(db_path) as connection:
            _ensure_active_user(connection, normalized_user_id)
            cursor = connection.execute(
                """
                INSERT INTO web_sessions (
                    user_id,
                    token_digest,
                    is_revoked,
                    expires_at,
                    created_at,
                    last_seen_at
                )
                VALUES (?, ?, 0, ?, ?, ?)
                """,
                (normalized_user_id, token_digest, expires_at, now_text, now_text),
            )
            session_id = cursor.lastrowid
            if session_id is None:
                raise WebAuthError("Failed to create web session.")
    except sqlite3.Error as e:
        raise WebAuthError("Failed to create web session.") from e

    return SessionCreation(
        session=SessionRecord(
            id=session_id,
            user_id=normalized_user_id,
            token_digest=token_digest,
            expires_at=expires_at,
            created_at=now_text,
            last_seen_at=now_text,
        ),
        token=token,
    )


def validate_session(db_path: Path, token: str) -> SessionRecord | None:
    """Return an active, unexpired session for a raw token, if valid."""
    token_digest = digest_token(token)
    if token_digest is None:
        return None

    now = _utc_now()
    now_text = _format_timestamp(now)

    try:
        with _connect(db_path) as connection:
            row = connection.execute(
                """
                SELECT s.id,
                       s.user_id,
                       s.token_digest,
                       s.is_revoked,
                       s.expires_at,
                       s.created_at,
                       s.last_seen_at,
                       s.revoked_at,
                       u.is_active AS user_is_active
                FROM web_sessions AS s
                JOIN web_users AS u ON u.id = s.user_id
                WHERE s.token_digest = ?
                """,
                (token_digest,),
            ).fetchone()
            if row is None:
                return None
            if not token_digest_matches(row["token_digest"], token_digest):
                return None
            if row["is_revoked"] or not row["user_is_active"]:
                return None
            if not _is_future_timestamp(row["expires_at"], now):
                return None

            connection.execute(
                """
                UPDATE web_sessions
                SET last_seen_at = ?
                WHERE id = ?
                """,
                (now_text, row["id"]),
            )
    except sqlite3.Error as e:
        raise WebAuthError("Failed to validate web session.") from e

    return SessionRecord(
        id=row["id"],
        user_id=row["user_id"],
        token_digest=row["token_digest"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        last_seen_at=now_text,
        is_revoked=False,
        revoked_at=row["revoked_at"],
    )


def get_session_user(db_path: Path, token: str) -> UserRecord | None:
    """Return the active user for a valid session token, if present."""
    session = validate_session(db_path, token)
    if session is None:
        return None

    try:
        with _connect(db_path) as connection:
            row = connection.execute(
                """
                SELECT id, username, password_hash, role, is_active, created_at, updated_at
                FROM web_users
                WHERE id = ?
                  AND is_active = 1
                """,
                (session.user_id,),
            ).fetchone()
    except sqlite3.Error as e:
        raise WebAuthError("Failed to read web session user.") from e

    if row is None:
        return None
    return _user_record_from_row(row)


def revoke_session(db_path: Path, session_id: int) -> bool:
    """Mark a session revoked."""
    normalized_session_id = _require_positive_int(session_id, "session_id")
    now_text = _format_timestamp(_utc_now())

    try:
        with _connect(db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE web_sessions
                SET is_revoked = 1,
                    revoked_at = ?,
                    last_seen_at = ?
                WHERE id = ?
                  AND is_revoked = 0
                """,
                (now_text, now_text, normalized_session_id),
            )
    except sqlite3.Error as e:
        raise WebAuthError("Failed to revoke web session.") from e

    return cursor.rowcount > 0


def delete_session(db_path: Path, session_id: int) -> bool:
    """Delete a session row."""
    normalized_session_id = _require_positive_int(session_id, "session_id")

    try:
        with _connect(db_path) as connection:
            cursor = connection.execute(
                """
                DELETE FROM web_sessions
                WHERE id = ?
                """,
                (normalized_session_id,),
            )
    except sqlite3.Error as e:
        raise WebAuthError("Failed to delete web session.") from e

    return cursor.rowcount > 0
