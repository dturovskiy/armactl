"""First-setup helpers for web auth users."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from armactl.web.auth.models import UserAlreadyExistsError, UserRecord, WebAuthError
from armactl.web.auth.users import OWNER_ROLE, create_owner_user
from armactl.web.runtime import WebRuntimeConfig, ensure_web_runtime


@dataclass(frozen=True)
class OwnerSetupResult:
    """Result of creating the first web owner user."""

    config: WebRuntimeConfig
    user: UserRecord


def owner_user_exists(db_path: Path) -> bool:
    """Return whether the web runtime already has an owner user."""
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM web_users
                WHERE role = ?
                LIMIT 1
                """,
                (OWNER_ROLE,),
            ).fetchone()
    except sqlite3.Error as e:
        raise WebAuthError("Failed to read web owner user.") from e

    return row is not None


def setup_owner_user(
    data_root: Path | None,
    username: str,
    password: str,
) -> OwnerSetupResult:
    """Ensure web runtime exists and create the initial owner user."""
    config = ensure_web_runtime(data_root)
    if owner_user_exists(config.db_path):
        raise UserAlreadyExistsError("Web owner user already exists.")

    user = create_owner_user(config.db_path, username, password)
    return OwnerSetupResult(config=config, user=user)
