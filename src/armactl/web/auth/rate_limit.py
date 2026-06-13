"""SQLite-backed login throttling for the web panel."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from armactl.web.auth.models import InvalidAuthInputError, WebAuthError
from armactl.web.auth.users import normalize_username
from armactl.web.runtime import ensure_web_db

MAX_FAILED_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60
RETENTION_SECONDS = 24 * 60 * 60
UNKNOWN_CLIENT_IP = "unknown"
_KEY_SEPARATOR = "\0"


@dataclass(frozen=True)
class LoginRateLimitStatus:
    """Current login throttle state for a normalized IP/username pair."""

    allowed: bool
    key_digest: str = field(repr=False)
    failure_count: int = 0
    retry_after_seconds: int = 0
    locked_until: str = ""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _retry_after(now: datetime, locked_until: datetime | None) -> int:
    if locked_until is None or locked_until <= now:
        return 0
    return max(1, int((locked_until - now).total_seconds()))


def _normalize_key_part(value: str) -> str:
    return str(value or "").strip() or UNKNOWN_CLIENT_IP


def _normalize_username_for_key(username: str) -> str:
    try:
        return normalize_username(username)
    except InvalidAuthInputError:
        return ""


def make_login_rate_limit_key(
    session_secret: str,
    client_ip: str,
    username: str,
) -> str:
    """Return a stable HMAC digest for the client IP and normalized username."""
    if not str(session_secret or "").strip():
        raise WebAuthError("Web session secret is not configured.")

    key_material = _KEY_SEPARATOR.join(
        (
            _normalize_key_part(client_ip),
            _normalize_username_for_key(username),
        )
    )
    return hmac.new(
        session_secret.encode("utf-8"),
        key_material.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _connect(db_path: Path) -> sqlite3.Connection:
    try:
        ensure_web_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection
    except (OSError, sqlite3.Error) as error:
        raise WebAuthError("Login rate limit storage is unavailable.") from error


def _fetch_row(connection: sqlite3.Connection, key_digest: str) -> sqlite3.Row | None:
    try:
        return connection.execute(
            "SELECT * FROM web_login_rate_limits WHERE key_digest = ?",
            (key_digest,),
        ).fetchone()
    except sqlite3.Error as error:
        raise WebAuthError("Login rate limit state cannot be read.") from error


def _prune_old_rows(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    retention_seconds: int,
) -> None:
    cutoff = _format_timestamp(now - timedelta(seconds=retention_seconds))
    try:
        connection.execute(
            "DELETE FROM web_login_rate_limits WHERE updated_at < ?",
            (cutoff,),
        )
    except sqlite3.Error as error:
        raise WebAuthError("Login rate limit state cannot be pruned.") from error


def _status_from_row(
    row: sqlite3.Row | None,
    *,
    key_digest: str,
    now: datetime,
    window_seconds: int,
) -> LoginRateLimitStatus:
    if row is None:
        return LoginRateLimitStatus(allowed=True, key_digest=key_digest)

    failure_count = int(row["failure_count"] or 0)
    window_started_at = _parse_timestamp(row["window_started_at"])
    locked_until = _parse_timestamp(row["locked_until"])
    if locked_until is not None and locked_until > now:
        return LoginRateLimitStatus(
            allowed=False,
            key_digest=key_digest,
            failure_count=failure_count,
            retry_after_seconds=_retry_after(now, locked_until),
            locked_until=_format_timestamp(locked_until),
        )

    if window_started_at is None or now - window_started_at >= timedelta(seconds=window_seconds):
        return LoginRateLimitStatus(allowed=True, key_digest=key_digest)

    return LoginRateLimitStatus(
        allowed=True,
        key_digest=key_digest,
        failure_count=failure_count,
    )


def check_login_allowed(
    db_path: Path,
    session_secret: str,
    client_ip: str,
    username: str,
    *,
    now: datetime | None = None,
    window_seconds: int = WINDOW_SECONDS,
) -> LoginRateLimitStatus:
    """Return whether a login attempt is currently allowed."""
    current_time = now or _utc_now()
    key_digest = make_login_rate_limit_key(session_secret, client_ip, username)
    with _connect(db_path) as connection:
        row = _fetch_row(connection, key_digest)
    return _status_from_row(
        row,
        key_digest=key_digest,
        now=current_time,
        window_seconds=window_seconds,
    )


def record_login_failure(
    db_path: Path,
    session_secret: str,
    client_ip: str,
    username: str,
    *,
    now: datetime | None = None,
    max_failed_attempts: int = MAX_FAILED_ATTEMPTS,
    window_seconds: int = WINDOW_SECONDS,
    lockout_seconds: int = LOCKOUT_SECONDS,
    retention_seconds: int = RETENTION_SECONDS,
) -> LoginRateLimitStatus:
    """Record a failed login attempt and return the updated throttle state."""
    current_time = now or _utc_now()
    key_digest = make_login_rate_limit_key(session_secret, client_ip, username)
    with _connect(db_path) as connection:
        _prune_old_rows(
            connection,
            now=current_time,
            retention_seconds=retention_seconds,
        )
        row = _fetch_row(connection, key_digest)
        existing = _status_from_row(
            row,
            key_digest=key_digest,
            now=current_time,
            window_seconds=window_seconds,
        )
        if not existing.allowed:
            return existing

        window_started_at = current_time
        failure_count = 1
        if row is not None:
            parsed_window = _parse_timestamp(row["window_started_at"])
            if (
                parsed_window is not None
                and current_time - parsed_window < timedelta(seconds=window_seconds)
            ):
                window_started_at = parsed_window
                failure_count = int(row["failure_count"] or 0) + 1

        locked_until = ""
        allowed = True
        retry_after_seconds = 0
        if failure_count >= max_failed_attempts:
            locked_at = current_time + timedelta(seconds=lockout_seconds)
            locked_until = _format_timestamp(locked_at)
            allowed = False
            retry_after_seconds = _retry_after(current_time, locked_at)

        timestamp = _format_timestamp(current_time)
        try:
            connection.execute(
                """
                INSERT INTO web_login_rate_limits(
                    key_digest,
                    failure_count,
                    window_started_at,
                    locked_until,
                    last_failed_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_digest) DO UPDATE SET
                    failure_count = excluded.failure_count,
                    window_started_at = excluded.window_started_at,
                    locked_until = excluded.locked_until,
                    last_failed_at = excluded.last_failed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    key_digest,
                    failure_count,
                    _format_timestamp(window_started_at),
                    locked_until,
                    timestamp,
                    timestamp,
                ),
            )
        except sqlite3.Error as error:
            raise WebAuthError("Login rate limit state cannot be written.") from error

    return LoginRateLimitStatus(
        allowed=allowed,
        key_digest=key_digest,
        failure_count=failure_count,
        retry_after_seconds=retry_after_seconds,
        locked_until=locked_until,
    )


def clear_login_failures(
    db_path: Path,
    session_secret: str,
    client_ip: str,
    username: str,
) -> None:
    """Clear throttle state after a successful login."""
    key_digest = make_login_rate_limit_key(session_secret, client_ip, username)
    with _connect(db_path) as connection:
        try:
            connection.execute(
                "DELETE FROM web_login_rate_limits WHERE key_digest = ?",
                (key_digest,),
            )
        except sqlite3.Error as error:
            raise WebAuthError("Login rate limit state cannot be cleared.") from error
