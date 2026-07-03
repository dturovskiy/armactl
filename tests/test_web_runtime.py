"""Tests for the web runtime storage/config/database foundation."""

from __future__ import annotations

import sqlite3
import stat
from dataclasses import replace
from pathlib import Path

import pytest

import armactl.web.runtime.config as runtime_config
from armactl.ports import WEB_PANEL_DEFAULT_PORT
from armactl.web.runtime import (
    WebRuntimeConfigError,
    ensure_web_db,
    ensure_web_runtime,
    load_web_runtime_config,
    save_web_runtime_config,
    web_audit_log_file,
    web_db_file,
    web_env_file,
    web_runtime_dir,
)
from armactl.web.runtime.db import WEB_SCHEMA_VERSION
from armactl.web.runtime.job_store_maintenance import (
    JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY,
)

FORBIDDEN_IMPORT_PREFIXES = ("armactl.tui", "textual")


def _sqlite_tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    return {row[0] for row in rows}


def _sqlite_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _sqlite_indexes(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index'
            """
        ).fetchall()
    return {row[0] for row in rows}


def _schema_meta(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT key, value
            FROM web_schema_meta
            """
        ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _schema_version(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT value
            FROM web_schema_meta
            WHERE key = 'schema_version'
            """
        ).fetchone()
    assert row is not None
    return row[0]


def test_web_runtime_paths_are_under_web_dir_not_default_instance(tmp_path: Path):
    assert web_runtime_dir(tmp_path) == tmp_path / "web"
    assert web_env_file(tmp_path) == tmp_path / "web" / "web.env"
    assert web_db_file(tmp_path) == tmp_path / "web" / "web.db"
    assert web_audit_log_file(tmp_path) == tmp_path / "logs" / "web" / "audit.log"

    assert web_runtime_dir(tmp_path) != tmp_path / "default"
    assert "default" not in web_runtime_dir(tmp_path).parts
    assert "default" not in web_audit_log_file(tmp_path).parts


def test_ensure_web_runtime_creates_private_env_file(tmp_path: Path):
    config = ensure_web_runtime(tmp_path)

    assert config.env_path == tmp_path / "web" / "web.env"
    assert config.env_path.exists()
    assert stat.S_IMODE(config.env_path.stat().st_mode) == 0o600


def test_session_secret_is_generated_and_stable_after_reload(tmp_path: Path):
    created = ensure_web_runtime(tmp_path)
    reloaded = load_web_runtime_config(tmp_path)

    assert created.session_secret
    assert len(created.session_secret) >= 32
    assert reloaded.session_secret == created.session_secret


def test_ensure_web_runtime_persists_missing_session_secret(tmp_path: Path):
    runtime_dir = tmp_path / "web"
    runtime_dir.mkdir()
    env_path = runtime_dir / "web.env"
    env_path.write_text(
        "\n".join(
            [
                "ARMACTL_WEB_BIND_HOST=127.0.0.1",
                f"ARMACTL_WEB_BIND_PORT={WEB_PANEL_DEFAULT_PORT}",
                "ARMACTL_WEB_HTTPS_REQUIRED=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    created = ensure_web_runtime(tmp_path)
    reloaded = load_web_runtime_config(tmp_path)
    text = env_path.read_text(encoding="utf-8")

    assert created.session_secret
    assert reloaded.session_secret == created.session_secret
    assert "ARMACTL_WEB_SESSION_SECRET=" in text


def test_ensure_web_runtime_persists_missing_cookie_namespace(tmp_path: Path):
    runtime_dir = tmp_path / "web"
    runtime_dir.mkdir()
    env_path = runtime_dir / "web.env"
    env_path.write_text(
        "\n".join(
            [
                "ARMACTL_WEB_BIND_HOST=127.0.0.1",
                f"ARMACTL_WEB_BIND_PORT={WEB_PANEL_DEFAULT_PORT}",
                "ARMACTL_WEB_HTTPS_REQUIRED=false",
                "ARMACTL_WEB_SESSION_SECRET=test-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    created = ensure_web_runtime(tmp_path)
    reloaded = load_web_runtime_config(tmp_path)
    text = env_path.read_text(encoding="utf-8")

    assert created.cookie_namespace
    assert reloaded.cookie_namespace == created.cookie_namespace
    assert "ARMACTL_WEB_COOKIE_NAMESPACE=" in text


def test_config_roundtrip_preserves_bind_and_https_settings(tmp_path: Path):
    original = ensure_web_runtime(tmp_path)
    updated = replace(
        original,
        bind_host="0.0.0.0",
        bind_port=WEB_PANEL_DEFAULT_PORT + 1,
        https_required=True,
    )

    save_web_runtime_config(updated)
    reloaded = load_web_runtime_config(tmp_path)

    assert reloaded.bind_host == "0.0.0.0"
    assert reloaded.bind_port == WEB_PANEL_DEFAULT_PORT + 1
    assert reloaded.https_required is True
    assert reloaded.session_secret == original.session_secret


def test_https_required_defaults_false_for_localhost_reverse_proxy_model(tmp_path: Path):
    config = ensure_web_runtime(tmp_path)
    text = config.env_path.read_text(encoding="utf-8")

    assert config.bind_host == "127.0.0.1"
    assert config.https_required is False
    assert "ARMACTL_WEB_HTTPS_REQUIRED=false" in text


def test_ensure_web_db_creates_schema_metadata(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"

    created_path = ensure_web_db(db_path)

    assert created_path == db_path
    assert db_path.exists()
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert _schema_version(db_path) == WEB_SCHEMA_VERSION


def test_ensure_web_db_migrates_v7_minimal_jobs_before_maintenance(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE web_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_schema_meta(key, value)
            VALUES ('schema_version', '7')
            """
        )
        connection.execute(
            """
            CREATE TABLE web_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_by_username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO web_jobs(
                kind,
                status,
                requested_by_username,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'owner', ?, ?)
            """,
            (
                (
                    "server:install",
                    "queued",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
                (
                    "server:install",
                    "running",
                    "2026-01-01T00:00:01+00:00",
                    "2026-01-01T00:00:01+00:00",
                ),
            ),
        )

    ensure_web_db(db_path)

    assert _schema_version(db_path) == WEB_SCHEMA_VERSION
    assert {
        "instance",
        "progress_current",
        "progress_total",
        "current_step",
        "result_message",
        "stdout_tail",
        "stderr_tail",
        "error_message",
        "error_class",
        "started_at",
        "finished_at",
    } <= _sqlite_columns(db_path, "web_jobs")
    assert "idx_web_jobs_active_lookup" in _sqlite_indexes(db_path)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT status, instance, result_message, stdout_tail, stderr_tail
            FROM web_jobs
            ORDER BY id
            """
        ).fetchall()
    assert rows[0] == ("queued", "default", "", "", "")
    assert rows[1] == ("running", "default", "", "", "")

    first_meta = _schema_meta(db_path)
    assert JOB_STORE_DUPLICATE_ACTIVE_REPAIR_COUNT_META_KEY not in first_meta
    first_rows = rows
    ensure_web_db(db_path)

    with sqlite3.connect(db_path) as connection:
        second_rows = connection.execute(
            """
            SELECT status, instance, result_message, stdout_tail, stderr_tail
            FROM web_jobs
            ORDER BY id
            """
        ).fetchall()
    assert second_rows == first_rows
    assert _schema_meta(db_path) == first_meta


def test_ensure_web_runtime_creates_env_db_and_auth_tables(tmp_path: Path):
    config = ensure_web_runtime(tmp_path)

    assert config.runtime_dir == tmp_path / "web"
    assert config.env_path.exists()
    assert config.db_path.exists()
    assert _schema_version(config.db_path) == WEB_SCHEMA_VERSION

    tables = _sqlite_tables(config.db_path)
    assert "web_schema_meta" in tables
    assert "web_users" in tables
    assert "web_sessions" in tables
    assert "web_csrf_tokens" in tables
    assert "web_jobs" in tables
    assert "web_login_rate_limits" in tables
    assert "web_pending_restarts" in tables
    assert "web_pending_work" in tables
    assert tables.isdisjoint(
        {
            "auth",
            "csrf",
            "csrf_tokens",
            "sessions",
            "users",
            "web_auth",
        }
    )


def test_config_rejects_invalid_ports_through_web_port_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runtime_dir = tmp_path / "web"
    runtime_dir.mkdir()
    env_path = runtime_dir / "web.env"
    env_path.write_text(
        "\n".join(
            [
                "ARMACTL_WEB_BIND_HOST=127.0.0.1",
                "ARMACTL_WEB_BIND_PORT=8767",
                "ARMACTL_WEB_HTTPS_REQUIRED=false",
                "ARMACTL_WEB_SESSION_SECRET=test-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[int] = []

    def fake_validate_web_port(port: int) -> None:
        calls.append(port)
        raise ValueError("blocked by shared validator")

    monkeypatch.setattr(runtime_config, "validate_web_port", fake_validate_web_port)

    with pytest.raises(WebRuntimeConfigError, match="blocked by shared validator"):
        runtime_config.load_web_runtime_config(tmp_path)

    assert calls == [8767]


def test_config_rejects_reserved_arma_game_port(tmp_path: Path):
    runtime_dir = tmp_path / "web"
    runtime_dir.mkdir()
    (runtime_dir / "web.env").write_text(
        "\n".join(
            [
                "ARMACTL_WEB_BIND_HOST=127.0.0.1",
                "ARMACTL_WEB_BIND_PORT=2001",
                "ARMACTL_WEB_HTTPS_REQUIRED=false",
                "ARMACTL_WEB_SESSION_SECRET=test-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(WebRuntimeConfigError, match="Port 2001 is reserved"):
        load_web_runtime_config(tmp_path)


def test_config_rejects_invalid_cookie_namespace(tmp_path: Path):
    runtime_dir = tmp_path / "web"
    runtime_dir.mkdir()
    (runtime_dir / "web.env").write_text(
        "\n".join(
            [
                "ARMACTL_WEB_BIND_HOST=127.0.0.1",
                f"ARMACTL_WEB_BIND_PORT={WEB_PANEL_DEFAULT_PORT}",
                "ARMACTL_WEB_HTTPS_REQUIRED=false",
                "ARMACTL_WEB_COOKIE_NAMESPACE=bad namespace",
                "ARMACTL_WEB_SESSION_SECRET=test-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(WebRuntimeConfigError, match="COOKIE_NAMESPACE"):
        load_web_runtime_config(tmp_path)


def test_web_runtime_package_import_does_not_import_tui_or_textual(
    assert_import_does_not_import_modules,
):
    assert_import_does_not_import_modules(
        "armactl.web.runtime",
        FORBIDDEN_IMPORT_PREFIXES,
    )
