"""SQLite storage foundation for armactl web-specific data."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

WEB_SCHEMA_VERSION = "6"
PRIVATE_FILE_MODE = 0o600


def _ensure_private_db_file(db_path: Path) -> None:
    if db_path.exists():
        return

    fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    os.close(fd)


def ensure_web_db(db_path: Path) -> Path:
    """Create/open `web.db` and ensure the web runtime schema exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_private_db_file(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
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
            CREATE TABLE IF NOT EXISTS web_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE CHECK(length(trim(username)) > 0),
                password_hash TEXT NOT NULL CHECK(length(password_hash) > 0),
                role TEXT NOT NULL CHECK(role = 'owner'),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                created_at TEXT NOT NULL CHECK(length(created_at) > 0),
                updated_at TEXT NOT NULL CHECK(length(updated_at) > 0)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_digest TEXT NOT NULL UNIQUE CHECK(length(token_digest) > 0),
                is_revoked INTEGER NOT NULL DEFAULT 0 CHECK(is_revoked IN (0, 1)),
                expires_at TEXT NOT NULL CHECK(length(expires_at) > 0),
                created_at TEXT NOT NULL CHECK(length(created_at) > 0),
                last_seen_at TEXT NOT NULL CHECK(length(last_seen_at) > 0),
                revoked_at TEXT,
                FOREIGN KEY(user_id) REFERENCES web_users(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_sessions_user_id
            ON web_sessions(user_id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_csrf_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                token_digest TEXT NOT NULL UNIQUE CHECK(length(token_digest) > 0),
                expires_at TEXT NOT NULL CHECK(length(expires_at) > 0),
                created_at TEXT NOT NULL CHECK(length(created_at) > 0),
                FOREIGN KEY(session_id) REFERENCES web_sessions(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_csrf_tokens_session_id
            ON web_csrf_tokens(session_id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(length(trim(kind)) > 0),
                status TEXT NOT NULL CHECK(status IN (
                    'queued',
                    'running',
                    'succeeded',
                    'failed',
                    'cancelled'
                )),
                requested_by_user_id INTEGER,
                requested_by_username TEXT NOT NULL CHECK(length(trim(requested_by_username)) > 0),
                instance TEXT NOT NULL DEFAULT 'default' CHECK(length(trim(instance)) > 0),
                progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0),
                progress_total INTEGER NOT NULL DEFAULT 0 CHECK(progress_total >= 0),
                current_step TEXT NOT NULL DEFAULT '',
                result_message TEXT NOT NULL DEFAULT '',
                stdout_tail TEXT NOT NULL DEFAULT '',
                stderr_tail TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                error_class TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL CHECK(length(created_at) > 0),
                updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY(requested_by_user_id) REFERENCES web_users(id) ON DELETE SET NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_jobs_created_at
            ON web_jobs(created_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_jobs_status
            ON web_jobs(status)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_login_rate_limits (
                key_digest TEXT PRIMARY KEY CHECK(length(key_digest) > 0),
                failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
                window_started_at TEXT NOT NULL CHECK(length(window_started_at) > 0),
                locked_until TEXT NOT NULL DEFAULT '',
                last_failed_at TEXT NOT NULL CHECK(length(last_failed_at) > 0),
                updated_at TEXT NOT NULL CHECK(length(updated_at) > 0)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_login_rate_limits_updated_at
            ON web_login_rate_limits(updated_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_pending_restarts (
                instance TEXT PRIMARY KEY CHECK(length(trim(instance)) > 0),
                reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
                source_action TEXT NOT NULL CHECK(length(trim(source_action)) > 0),
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL CHECK(length(created_at) > 0),
                updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
                created_by_username TEXT NOT NULL CHECK(length(trim(created_by_username)) > 0)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_pending_restarts_updated_at
            ON web_pending_restarts(updated_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_pending_work (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT NOT NULL CHECK(length(trim(instance)) > 0),
                kind TEXT NOT NULL CHECK(length(trim(kind)) > 0),
                source_path TEXT NOT NULL CHECK(length(trim(source_path)) > 0),
                source_action TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                details TEXT NOT NULL DEFAULT '',
                resolution_action TEXT NOT NULL CHECK(length(trim(resolution_action)) > 0),
                created_at TEXT NOT NULL CHECK(length(created_at) > 0),
                updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
                created_by_username TEXT NOT NULL CHECK(length(trim(created_by_username)) > 0),
                UNIQUE(instance, kind, resolution_action)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_pending_work_updated_at
            ON web_pending_work(updated_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_pending_work_resolution
            ON web_pending_work(instance, resolution_action)
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO web_pending_work(
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
            )
            SELECT
                instance,
                reason,
                CASE reason
                    WHEN 'config' THEN '/config'
                    WHEN 'admins' THEN '/admins'
                    WHEN 'mods' THEN '/mods'
                    ELSE '/dashboard'
                END,
                source_action,
                CASE reason
                    WHEN 'config' THEN 'Config changes'
                    WHEN 'admins' THEN 'Admin changes'
                    WHEN 'mods' THEN 'Mod changes'
                    ELSE 'Saved changes'
                END,
                details,
                'restart game server',
                created_at,
                updated_at,
                created_by_username
            FROM web_pending_restarts
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
