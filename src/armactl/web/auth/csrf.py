"""SQLite-backed CSRF token primitives for the web panel."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from armactl.web.auth.models import InvalidAuthInputError, WebAuthError
from armactl.web.auth.tokens import digest_token, generate_token, token_digest_matches
from armactl.web.runtime.db import ensure_web_db

DEFAULT_CSRF_TTL_SECONDS = 60 * 60


@dataclass(frozen=True)
class CsrfTokenRecord:
    """Persisted CSRF token metadata without the raw token."""

    id: int
    session_id: int
    token_digest: str = field(repr=False)
    expires_at: str
    created_at: str


@dataclass(frozen=True)
class CsrfTokenCreation:
    """New CSRF token result containing the raw token for the caller once."""

    csrf: CsrfTokenRecord
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


def _record_from_row(row: sqlite3.Row) -> CsrfTokenRecord:
    return CsrfTokenRecord(
        id=row["id"],
        session_id=row["session_id"],
        token_digest=row["token_digest"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


def _active_session_exists(
    connection: sqlite3.Connection,
    session_id: int,
    now: datetime,
) -> bool:
    row = connection.execute(
        """
        SELECT s.expires_at,
               s.is_revoked,
               u.is_active AS user_is_active
        FROM web_sessions AS s
        JOIN web_users AS u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return False
    if row["is_revoked"] or not row["user_is_active"]:
        return False
    return _is_future_timestamp(row["expires_at"], now)


def create_csrf_token(
    db_path: Path,
    session_id: int,
    ttl_seconds: int = DEFAULT_CSRF_TTL_SECONDS,
) -> CsrfTokenCreation:
    """Create a CSRF token bound to an active session."""
    normalized_session_id = _require_positive_int(session_id, "session_id")
    normalized_ttl = _require_positive_int(ttl_seconds, "ttl_seconds")
    now = _utc_now()
    now_text = _format_timestamp(now)
    expires_at = _format_timestamp(now + timedelta(seconds=normalized_ttl))
    token = generate_token()
    token_digest = digest_token(token)
    if token_digest is None:
        raise WebAuthError("Failed to create CSRF token.")

    try:
        with _connect(db_path) as connection:
            if not _active_session_exists(connection, normalized_session_id, now):
                raise InvalidAuthInputError("Active web session is required.")
            cursor = connection.execute(
                """
                INSERT INTO web_csrf_tokens (
                    session_id,
                    token_digest,
                    expires_at,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (normalized_session_id, token_digest, expires_at, now_text),
            )
            csrf_id = cursor.lastrowid
            if csrf_id is None:
                raise WebAuthError("Failed to create CSRF token.")
    except sqlite3.Error as e:
        raise WebAuthError("Failed to create CSRF token.") from e

    return CsrfTokenCreation(
        csrf=CsrfTokenRecord(
            id=csrf_id,
            session_id=normalized_session_id,
            token_digest=token_digest,
            expires_at=expires_at,
            created_at=now_text,
        ),
        token=token,
    )


def validate_csrf_token(db_path: Path, session_id: int, token: str) -> bool:
    """Return whether a CSRF token is valid for an active session."""
    try:
        normalized_session_id = _require_positive_int(session_id, "session_id")
    except InvalidAuthInputError:
        return False

    token_digest = digest_token(token)
    if token_digest is None:
        return False

    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            row = connection.execute(
                """
                SELECT c.id,
                       c.session_id,
                       c.token_digest,
                       c.expires_at,
                       c.created_at,
                       s.expires_at AS session_expires_at,
                       s.is_revoked AS session_is_revoked,
                       u.is_active AS user_is_active
                FROM web_csrf_tokens AS c
                JOIN web_sessions AS s ON s.id = c.session_id
                JOIN web_users AS u ON u.id = s.user_id
                WHERE c.session_id = ?
                  AND c.token_digest = ?
                """,
                (normalized_session_id, token_digest),
            ).fetchone()
            if row is None:
                return False
            if not token_digest_matches(row["token_digest"], token_digest):
                return False
            if row["session_is_revoked"] or not row["user_is_active"]:
                return False
            if not _is_future_timestamp(row["session_expires_at"], now):
                return False
            if not _is_future_timestamp(row["expires_at"], now):
                return False
    except sqlite3.Error as e:
        raise WebAuthError("Failed to validate CSRF token.") from e

    return True
