"""SQLite storage foundation for armactl web-specific data."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

WEB_SCHEMA_VERSION = "17"
PRIVATE_FILE_MODE = 0o600
_LEGACY_DEFAULT_TIMESTAMP = "1970-01-01T00:00:00+00:00"

Migration = Callable[[sqlite3.Connection], None]


def _ensure_private_db_file(db_path: Path) -> None:
    if db_path.exists():
        return

    fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    os.close(fd)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    rows = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table_name)})"
    ).fetchall()
    return {str(row[1]) for row in rows}


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    existing_columns = _table_columns(connection, table_name)
    quoted_table = _quote_identifier(table_name)
    for column_name, column_ddl in columns:
        if column_name not in existing_columns:
            connection.execute(f"ALTER TABLE {quoted_table} ADD COLUMN {column_ddl}")


def _ensure_web_schema_meta_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS web_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _ensure_web_users_schema(connection: sqlite3.Connection) -> None:
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
    _ensure_columns(
        connection,
        "web_users",
        (
            ("username", "username TEXT NOT NULL DEFAULT 'unknown'"),
            ("password_hash", "password_hash TEXT NOT NULL DEFAULT 'legacy'"),
            ("role", "role TEXT NOT NULL DEFAULT 'owner' CHECK(role = 'owner')"),
            (
                "is_active",
                "is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))",
            ),
            (
                "created_at",
                f"created_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
        ),
    )


def _ensure_web_sessions_schema(connection: sqlite3.Connection) -> None:
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
    _ensure_columns(
        connection,
        "web_sessions",
        (
            ("user_id", "user_id INTEGER NOT NULL DEFAULT 0"),
            ("token_digest", "token_digest TEXT NOT NULL DEFAULT 'legacy'"),
            (
                "is_revoked",
                "is_revoked INTEGER NOT NULL DEFAULT 0 CHECK(is_revoked IN (0, 1))",
            ),
            (
                "expires_at",
                f"expires_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "created_at",
                f"created_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "last_seen_at",
                f"last_seen_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("revoked_at", "revoked_at TEXT"),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_sessions_user_id
        ON web_sessions(user_id)
        """
    )


def _ensure_web_csrf_schema(connection: sqlite3.Connection) -> None:
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
    _ensure_columns(
        connection,
        "web_csrf_tokens",
        (
            ("session_id", "session_id INTEGER NOT NULL DEFAULT 0"),
            ("token_digest", "token_digest TEXT NOT NULL DEFAULT 'legacy'"),
            (
                "expires_at",
                f"expires_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "created_at",
                f"created_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_csrf_tokens_session_id
        ON web_csrf_tokens(session_id)
        """
    )


_WEB_JOBS_COPY_COLUMNS = (
    "id",
    "kind",
    "status",
    "requested_by_user_id",
    "requested_by_username",
    "instance",
    "progress_current",
    "progress_total",
    "current_step",
    "result_message",
    "stdout_tail",
    "stderr_tail",
    "error_message",
    "error_class",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "worker_id",
    "worker_started_at",
    "worker_heartbeat_at",
    "worker_lease_expires_at",
)


def _ensure_web_jobs_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS web_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(length(trim(kind)) > 0),
            status TEXT NOT NULL CHECK(status IN (
                'queued',
                'running',
                'succeeded',
                'warning',
                'failed',
                'cancelled',
                'abandoned'
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
            worker_id TEXT NOT NULL DEFAULT '',
            worker_started_at TEXT,
            worker_heartbeat_at TEXT,
            worker_lease_expires_at TEXT,
            FOREIGN KEY(requested_by_user_id) REFERENCES web_users(id) ON DELETE SET NULL
        )
        """
    )
    _ensure_columns(
        connection,
        "web_jobs",
        (
            ("kind", "kind TEXT NOT NULL DEFAULT 'legacy'"),
            ("status", "status TEXT NOT NULL DEFAULT 'queued'"),
            ("requested_by_user_id", "requested_by_user_id INTEGER"),
            (
                "requested_by_username",
                (
                    "requested_by_username TEXT NOT NULL DEFAULT 'unknown' "
                    "CHECK(length(trim(requested_by_username)) > 0)"
                ),
            ),
            (
                "instance",
                (
                    "instance TEXT NOT NULL DEFAULT 'default' "
                    "CHECK(length(trim(instance)) > 0)"
                ),
            ),
            (
                "progress_current",
                "progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0)",
            ),
            (
                "progress_total",
                "progress_total INTEGER NOT NULL DEFAULT 0 CHECK(progress_total >= 0)",
            ),
            ("current_step", "current_step TEXT NOT NULL DEFAULT ''"),
            ("result_message", "result_message TEXT NOT NULL DEFAULT ''"),
            ("stdout_tail", "stdout_tail TEXT NOT NULL DEFAULT ''"),
            ("stderr_tail", "stderr_tail TEXT NOT NULL DEFAULT ''"),
            ("error_message", "error_message TEXT NOT NULL DEFAULT ''"),
            ("error_class", "error_class TEXT NOT NULL DEFAULT ''"),
            (
                "created_at",
                f"created_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("started_at", "started_at TEXT"),
            ("finished_at", "finished_at TEXT"),
            ("worker_id", "worker_id TEXT NOT NULL DEFAULT ''"),
            ("worker_started_at", "worker_started_at TEXT"),
            ("worker_heartbeat_at", "worker_heartbeat_at TEXT"),
            ("worker_lease_expires_at", "worker_lease_expires_at TEXT"),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_jobs_created_at
        ON web_jobs(created_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_jobs_active_lookup
        ON web_jobs(kind, instance, status, created_at, id)
        WHERE status IN ('queued', 'running')
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
        CREATE INDEX IF NOT EXISTS idx_web_jobs_running_lease
        ON web_jobs(worker_lease_expires_at, id)
        WHERE status = 'running'
        """
    )


def _web_jobs_status_check_allows_abandoned(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = ?
          AND name = ?
        """,
        ("table", "web_jobs"),
    ).fetchone()
    if row is None:
        return True
    return 'abandoned' in str(row[0] or "")


def _web_jobs_status_check_allows_warning(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = ?
          AND name = ?
        """,
        ("table", "web_jobs"),
    ).fetchone()
    if row is None:
        return True
    sql = str(row[0] or "")
    return "'warning'" in sql or '"warning"' in sql


def _rebuild_web_jobs_with_current_status_check(connection: sqlite3.Connection) -> None:
    columns = ", ".join(_quote_identifier(column) for column in _WEB_JOBS_COPY_COLUMNS)
    foreign_keys_enabled = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("DROP TABLE IF EXISTS web_jobs_new")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_jobs_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(length(trim(kind)) > 0),
                status TEXT NOT NULL CHECK(status IN (
                    'queued',
                    'running',
                    'succeeded',
                    'warning',
                    'failed',
                    'cancelled',
                    'abandoned'
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
                worker_id TEXT NOT NULL DEFAULT '',
                worker_started_at TEXT,
                worker_heartbeat_at TEXT,
                worker_lease_expires_at TEXT,
                FOREIGN KEY(requested_by_user_id) REFERENCES web_users(id) ON DELETE SET NULL
            )
            """
        )
        connection.execute(
            f"INSERT INTO web_jobs_new ({columns}) SELECT {columns} FROM web_jobs"
        )
        connection.execute("DROP TABLE web_jobs")
        connection.execute("ALTER TABLE web_jobs_new RENAME TO web_jobs")
        _ensure_web_jobs_schema(connection)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError(
                "web.db foreign key check failed while migrating web_jobs."
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if foreign_keys_enabled:
            connection.execute("PRAGMA foreign_keys = ON")


def _ensure_web_server_version_checks_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS web_server_version_checks (
            instance TEXT PRIMARY KEY CHECK(length(trim(instance)) > 0),
            installed TEXT NOT NULL DEFAULT '',
            latest TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT 'public',
            check_state TEXT NOT NULL CHECK(length(trim(check_state)) > 0),
            failure_reason TEXT NOT NULL DEFAULT '',
            checked_at TEXT NOT NULL CHECK(length(checked_at) > 0),
            updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
            source TEXT NOT NULL DEFAULT '',
            job_id INTEGER,
            FOREIGN KEY(job_id) REFERENCES web_jobs(id) ON DELETE SET NULL
        )
        """
    )
    _ensure_columns(
        connection,
        "web_server_version_checks",
        (
            (
                "instance",
                (
                    "instance TEXT NOT NULL DEFAULT 'default' "
                    "CHECK(length(trim(instance)) > 0)"
                ),
            ),
            ("installed", "installed TEXT NOT NULL DEFAULT ''"),
            ("latest", "latest TEXT NOT NULL DEFAULT ''"),
            ("branch", "branch TEXT NOT NULL DEFAULT 'public'"),
            (
                "check_state",
                "check_state TEXT NOT NULL DEFAULT 'unknown' CHECK(length(trim(check_state)) > 0)",
            ),
            ("failure_reason", "failure_reason TEXT NOT NULL DEFAULT ''"),
            (
                "checked_at",
                f"checked_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("source", "source TEXT NOT NULL DEFAULT ''"),
            ("job_id", "job_id INTEGER"),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_server_version_checks_updated_at
        ON web_server_version_checks(updated_at)
        """
    )


def _ensure_web_current_roster_cache_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS web_current_roster_cache (
            instance TEXT PRIMARY KEY CHECK(length(trim(instance)) > 0),
            collected_at TEXT NOT NULL CHECK(length(collected_at) > 0),
            updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            observed_count INTEGER,
            count_source TEXT NOT NULL DEFAULT '',
            roster_available INTEGER NOT NULL DEFAULT 0
                CHECK(roster_available IN (0, 1)),
            roster_configured INTEGER NOT NULL DEFAULT 0
                CHECK(roster_configured IN (0, 1))
        )
        """
    )
    _ensure_columns(
        connection,
        "web_current_roster_cache",
        (
            (
                "instance",
                (
                    "instance TEXT NOT NULL DEFAULT 'default' "
                    "CHECK(length(trim(instance)) > 0)"
                ),
            ),
            (
                "collected_at",
                f"collected_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("source", "source TEXT NOT NULL DEFAULT ''"),
            ("status", "status TEXT NOT NULL DEFAULT ''"),
            ("error", "error TEXT NOT NULL DEFAULT ''"),
            ("observed_count", "observed_count INTEGER"),
            ("count_source", "count_source TEXT NOT NULL DEFAULT ''"),
            (
                "roster_available",
                (
                    "roster_available INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(roster_available IN (0, 1))"
                ),
            ),
            (
                "roster_configured",
                (
                    "roster_configured INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(roster_configured IN (0, 1))"
                ),
            ),
        ),
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS web_current_roster_cache_players (
            instance TEXT NOT NULL CHECK(length(trim(instance)) > 0),
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
            display_name TEXT NOT NULL DEFAULT '',
            reliable_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(instance, ordinal),
            FOREIGN KEY(instance) REFERENCES web_current_roster_cache(instance)
                ON DELETE CASCADE
        )
        """
    )
    _ensure_columns(
        connection,
        "web_current_roster_cache_players",
        (
            (
                "instance",
                (
                    "instance TEXT NOT NULL DEFAULT 'default' "
                    "CHECK(length(trim(instance)) > 0)"
                ),
            ),
            ("ordinal", "ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0)"),
            ("display_name", "display_name TEXT NOT NULL DEFAULT ''"),
            ("reliable_id", "reliable_id TEXT NOT NULL DEFAULT ''"),
            ("source", "source TEXT NOT NULL DEFAULT ''"),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_current_roster_cache_updated_at
        ON web_current_roster_cache(updated_at)
        """
    )


def _ensure_web_login_rate_limits_schema(connection: sqlite3.Connection) -> None:
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
    _ensure_columns(
        connection,
        "web_login_rate_limits",
        (
            ("key_digest", "key_digest TEXT NOT NULL DEFAULT 'legacy'"),
            (
                "failure_count",
                "failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0)",
            ),
            (
                "window_started_at",
                f"window_started_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("locked_until", "locked_until TEXT NOT NULL DEFAULT ''"),
            (
                "last_failed_at",
                f"last_failed_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_login_rate_limits_updated_at
        ON web_login_rate_limits(updated_at)
        """
    )


def _ensure_web_pending_restarts_schema(connection: sqlite3.Connection) -> None:
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
    _ensure_columns(
        connection,
        "web_pending_restarts",
        (
            (
                "instance",
                (
                    "instance TEXT NOT NULL DEFAULT 'default' "
                    "CHECK(length(trim(instance)) > 0)"
                ),
            ),
            (
                "reason",
                "reason TEXT NOT NULL DEFAULT 'config' CHECK(length(trim(reason)) > 0)",
            ),
            (
                "source_action",
                (
                    "source_action TEXT NOT NULL DEFAULT 'legacy' "
                    "CHECK(length(trim(source_action)) > 0)"
                ),
            ),
            ("details", "details TEXT NOT NULL DEFAULT ''"),
            (
                "created_at",
                f"created_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "created_by_username",
                (
                    "created_by_username TEXT NOT NULL DEFAULT 'unknown' "
                    "CHECK(length(trim(created_by_username)) > 0)"
                ),
            ),
            ("baseline_fingerprint", "baseline_fingerprint TEXT NOT NULL DEFAULT ''"),
            ("current_fingerprint", "current_fingerprint TEXT NOT NULL DEFAULT ''"),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_pending_restarts_updated_at
        ON web_pending_restarts(updated_at)
        """
    )


def _ensure_web_pending_work_schema(connection: sqlite3.Connection) -> None:
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
            baseline_fingerprint TEXT NOT NULL DEFAULT '',
            current_fingerprint TEXT NOT NULL DEFAULT '',
            UNIQUE(instance, kind, resolution_action)
        )
        """
    )
    _ensure_columns(
        connection,
        "web_pending_work",
        (
            (
                "instance",
                (
                    "instance TEXT NOT NULL DEFAULT 'default' "
                    "CHECK(length(trim(instance)) > 0)"
                ),
            ),
            ("kind", "kind TEXT NOT NULL DEFAULT 'saved' CHECK(length(trim(kind)) > 0)"),
            (
                "source_path",
                (
                    "source_path TEXT NOT NULL DEFAULT '/dashboard' "
                    "CHECK(length(trim(source_path)) > 0)"
                ),
            ),
            ("source_action", "source_action TEXT NOT NULL DEFAULT ''"),
            (
                "title",
                (
                    "title TEXT NOT NULL DEFAULT 'Saved changes' "
                    "CHECK(length(trim(title)) > 0)"
                ),
            ),
            ("details", "details TEXT NOT NULL DEFAULT ''"),
            (
                "resolution_action",
                (
                    "resolution_action TEXT NOT NULL DEFAULT 'restart game server' "
                    "CHECK(length(trim(resolution_action)) > 0)"
                ),
            ),
            (
                "created_at",
                f"created_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "created_by_username",
                (
                    "created_by_username TEXT NOT NULL DEFAULT 'unknown' "
                    "CHECK(length(trim(created_by_username)) > 0)"
                ),
            ),
            ("baseline_fingerprint", "baseline_fingerprint TEXT NOT NULL DEFAULT ''"),
            ("current_fingerprint", "current_fingerprint TEXT NOT NULL DEFAULT ''"),
        ),
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


def _ensure_web_player_session_scheduler_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS web_player_session_scheduler_state (
            instance TEXT NOT NULL CHECK(length(trim(instance)) > 0),
            job_kind TEXT NOT NULL CHECK(length(trim(job_kind)) > 0),
            last_attempt_at TEXT NOT NULL DEFAULT '',
            last_success_at TEXT NOT NULL DEFAULT '',
            last_failure_at TEXT NOT NULL DEFAULT '',
            next_due_at TEXT NOT NULL DEFAULT '',
            failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
            updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
            PRIMARY KEY(instance, job_kind)
        )
        """
    )
    _ensure_columns(
        connection,
        "web_player_session_scheduler_state",
        (
            (
                "instance",
                (
                    "instance TEXT NOT NULL DEFAULT 'default' "
                    "CHECK(length(trim(instance)) > 0)"
                ),
            ),
            (
                "job_kind",
                "job_kind TEXT NOT NULL DEFAULT 'legacy' CHECK(length(trim(job_kind)) > 0)",
            ),
            ("last_attempt_at", "last_attempt_at TEXT NOT NULL DEFAULT ''"),
            ("last_success_at", "last_success_at TEXT NOT NULL DEFAULT ''"),
            ("last_failure_at", "last_failure_at TEXT NOT NULL DEFAULT ''"),
            ("next_due_at", "next_due_at TEXT NOT NULL DEFAULT ''"),
            (
                "failure_count",
                "failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0)",
            ),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_player_session_scheduler_due
        ON web_player_session_scheduler_state(instance, next_due_at, job_kind)
        """
    )


def _ensure_web_moderation_verifications_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS web_moderation_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance TEXT NOT NULL CHECK(length(trim(instance)) > 0),
            action TEXT NOT NULL CHECK(action IN ('ban', 'unban')),
            reliable_identity TEXT NOT NULL
                CHECK(length(trim(reliable_identity)) > 0),
            reason_class TEXT NOT NULL
                CHECK(reason_class IN ('none', 'provided')),
            verification_state TEXT NOT NULL CHECK(verification_state IN (
                'pending_verification',
                'pending_outcome_audit',
                'resolved_changed',
                'resolved_noop',
                'resolved_failed'
            )),
            created_at TEXT NOT NULL CHECK(length(created_at) > 0),
            updated_at TEXT NOT NULL CHECK(length(updated_at) > 0)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_moderation_verifications_pending
        ON web_moderation_verifications(
            instance,
            verification_state,
            updated_at,
            id
        )
        """
    )


def _backfill_pending_work_from_restarts(connection: sqlite3.Connection) -> None:
    if not (
        _table_exists(connection, "web_pending_restarts")
        and _table_exists(connection, "web_pending_work")
    ):
        return

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


def _ensure_auth_schema(connection: sqlite3.Connection) -> None:
    _ensure_web_schema_meta_table(connection)
    _ensure_web_users_schema(connection)
    _ensure_web_sessions_schema(connection)
    _ensure_web_csrf_schema(connection)


def _ensure_current_web_schema(connection: sqlite3.Connection) -> None:
    _ensure_auth_schema(connection)
    _ensure_web_jobs_schema(connection)
    _ensure_web_server_version_checks_schema(connection)
    _ensure_web_current_roster_cache_schema(connection)
    _ensure_web_login_rate_limits_schema(connection)
    _ensure_web_pending_restarts_schema(connection)
    _ensure_web_pending_work_schema(connection)
    _ensure_web_player_session_scheduler_schema(connection)
    _ensure_web_moderation_verifications_schema(connection)
    _backfill_pending_work_from_restarts(connection)


def _migration_1_auth_schema(connection: sqlite3.Connection) -> None:
    _ensure_auth_schema(connection)


def _migration_2_jobs_schema(connection: sqlite3.Connection) -> None:
    _ensure_web_jobs_schema(connection)


def _migration_3_login_rate_limits(connection: sqlite3.Connection) -> None:
    _ensure_web_login_rate_limits_schema(connection)


def _migration_4_pending_restarts(connection: sqlite3.Connection) -> None:
    _ensure_web_pending_restarts_schema(connection)


def _migration_5_pending_work(connection: sqlite3.Connection) -> None:
    _ensure_web_pending_work_schema(connection)


def _migration_6_pending_work_backfill(connection: sqlite3.Connection) -> None:
    _backfill_pending_work_from_restarts(connection)


def _migration_7_schema_indexes(connection: sqlite3.Connection) -> None:
    _ensure_web_jobs_schema(connection)
    _ensure_web_login_rate_limits_schema(connection)
    _ensure_web_pending_restarts_schema(connection)
    _ensure_web_pending_work_schema(connection)


def _migration_8_current_schema_compatibility(connection: sqlite3.Connection) -> None:
    _ensure_current_web_schema(connection)


def _migration_9_pending_work_fingerprints(connection: sqlite3.Connection) -> None:
    _ensure_web_pending_work_schema(connection)


def _migration_10_server_version_checks(connection: sqlite3.Connection) -> None:
    _ensure_web_server_version_checks_schema(connection)


def _migration_11_current_roster_cache(connection: sqlite3.Connection) -> None:
    _ensure_web_current_roster_cache_schema(connection)


def _migration_12_current_roster_cache_counts(connection: sqlite3.Connection) -> None:
    _ensure_web_current_roster_cache_schema(connection)
    connection.execute(
        """
        UPDATE web_current_roster_cache
        SET observed_count = (
            SELECT COUNT(*)
            FROM web_current_roster_cache_players
            WHERE web_current_roster_cache_players.instance =
                web_current_roster_cache.instance
        )
        WHERE observed_count IS NULL
        """
    )


def _migration_13_player_session_scheduler_state(
    connection: sqlite3.Connection,
) -> None:
    _ensure_web_player_session_scheduler_schema(connection)


def _migration_14_web_job_worker_lease(connection: sqlite3.Connection) -> None:
    _ensure_web_jobs_schema(connection)


def _migration_15_web_job_abandoned_status(connection: sqlite3.Connection) -> None:
    _ensure_web_jobs_schema(connection)
    if _web_jobs_status_check_allows_abandoned(connection):
        return
    _rebuild_web_jobs_with_current_status_check(connection)


def _migration_16_moderation_verifications(connection: sqlite3.Connection) -> None:
    _ensure_web_moderation_verifications_schema(connection)


def _migration_17_web_job_warning_status(connection: sqlite3.Connection) -> None:
    _ensure_web_jobs_schema(connection)
    if _web_jobs_status_check_allows_warning(connection):
        return
    _rebuild_web_jobs_with_current_status_check(connection)


_WEB_SCHEMA_MIGRATIONS: tuple[tuple[int, Migration], ...] = (
    (1, _migration_1_auth_schema),
    (2, _migration_2_jobs_schema),
    (3, _migration_3_login_rate_limits),
    (4, _migration_4_pending_restarts),
    (5, _migration_5_pending_work),
    (6, _migration_6_pending_work_backfill),
    (7, _migration_7_schema_indexes),
    (8, _migration_8_current_schema_compatibility),
    (9, _migration_9_pending_work_fingerprints),
    (10, _migration_10_server_version_checks),
    (11, _migration_11_current_roster_cache),
    (12, _migration_12_current_roster_cache_counts),
    (13, _migration_13_player_session_scheduler_state),
    (14, _migration_14_web_job_worker_lease),
    (15, _migration_15_web_job_abandoned_status),
    (16, _migration_16_moderation_verifications),
    (17, _migration_17_web_job_warning_status),
)

def _read_schema_version(connection: sqlite3.Connection) -> int:
    _ensure_web_schema_meta_table(connection)
    row = connection.execute(
        """
        SELECT value
        FROM web_schema_meta
        WHERE key = 'schema_version'
        """
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(str(row[0]))
    except ValueError:
        return 0


def _write_schema_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        """
        INSERT INTO web_schema_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("schema_version", str(version)),
    )


def _run_web_db_migrations(connection: sqlite3.Connection) -> None:
    target_version = int(WEB_SCHEMA_VERSION)
    current_version = _read_schema_version(connection)
    if current_version > target_version:
        raise RuntimeError(
            f"web.db schema version {current_version} is newer than supported "
            f"version {target_version}."
        )

    for version, migration in _WEB_SCHEMA_MIGRATIONS:
        if version <= current_version:
            continue
        migration(connection)
        _write_schema_version(connection, version)


def ensure_web_db(db_path: Path) -> Path:
    """Create/open `web.db` and migrate the web runtime schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_private_db_file(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _run_web_db_migrations(connection)
        _backfill_pending_work_from_restarts(connection)

    db_path.chmod(PRIVATE_FILE_MODE)
    return db_path
