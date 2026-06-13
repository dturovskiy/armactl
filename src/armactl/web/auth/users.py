"""Explicit SQLite helpers for web auth users."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from armactl.web.auth.models import (
    InvalidAuthInputError,
    UserAlreadyExistsError,
    UserRecord,
    WebAuthError,
)
from armactl.web.auth.passwords import hash_password, verify_password
from armactl.web.runtime.db import ensure_web_db

OWNER_ROLE = "owner"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(username: str) -> str:
    if not isinstance(username, str):
        raise InvalidAuthInputError("Username cannot be empty.")

    normalized = username.strip().casefold()
    if not normalized:
        raise InvalidAuthInputError("Username cannot be empty.")
    return normalized


def _normalize_username(username: str) -> str:
    return normalize_username(username)


def _require_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise InvalidAuthInputError("Password cannot be empty.")
    return password


def _connect(db_path: Path) -> sqlite3.Connection:
    try:
        ensure_web_db(db_path)
        connection = sqlite3.connect(db_path)
    except (OSError, sqlite3.Error) as e:
        raise WebAuthError("Failed to open web auth database.") from e

    connection.row_factory = sqlite3.Row
    return connection


def _record_from_row(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_owner_user(db_path: Path, username: str, password: str) -> UserRecord:
    """Create an active owner user in `web.db`."""
    normalized_username = _normalize_username(username)
    password_hash = hash_password(_require_password(password))
    now = _utc_now()

    try:
        with _connect(db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO web_users (
                    username,
                    password_hash,
                    role,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (normalized_username, password_hash, OWNER_ROLE, now, now),
            )
            user_id = cursor.lastrowid
            if user_id is None:
                raise WebAuthError("Failed to create web user.")
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e).upper():
            raise UserAlreadyExistsError("Web user already exists.") from e
        raise WebAuthError("Failed to create web user.") from e
    except sqlite3.Error as e:
        raise WebAuthError("Failed to create web user.") from e

    return UserRecord(
        id=user_id,
        username=normalized_username,
        password_hash=password_hash,
        role=OWNER_ROLE,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def get_user_by_username(db_path: Path, username: str) -> UserRecord | None:
    """Return a normalized web user record by username, if present."""
    normalized_username = _normalize_username(username)

    try:
        with _connect(db_path) as connection:
            row = connection.execute(
                """
                SELECT id, username, password_hash, role, is_active, created_at, updated_at
                FROM web_users
                WHERE username = ?
                """,
                (normalized_username,),
            ).fetchone()
    except sqlite3.Error as e:
        raise WebAuthError("Failed to read web user.") from e

    if row is None:
        return None
    return _record_from_row(row)


def verify_user_password(db_path: Path, username: str, password: str) -> bool:
    """Return whether an active user exists and the password matches."""
    _require_password(password)
    user = get_user_by_username(db_path, username)
    if user is None or not user.is_active:
        return False
    return verify_password(password, user.password_hash)
