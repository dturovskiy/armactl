"""SQLite storage foundation for armactl web-specific data."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

WEB_SCHEMA_VERSION = "1"
PRIVATE_FILE_MODE = 0o600


def _ensure_private_db_file(db_path: Path) -> None:
    if db_path.exists():
        return

    fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    os.close(fd)


def ensure_web_db(db_path: Path) -> Path:
    """Create/open `web.db` and ensure the schema metadata table exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_private_db_file(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_schema_meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("schema_version", WEB_SCHEMA_VERSION),
        )

    db_path.chmod(PRIVATE_FILE_MODE)
    return db_path
