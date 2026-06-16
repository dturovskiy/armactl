"""Tests for web pending operator work."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from armactl.web.runtime import ensure_web_db
from armactl.web.services.pending_work import (
    KIND_ADMINS,
    KIND_CONFIG,
    RESOLUTION_RESTART_GAME_SERVER,
    clear_pending_work,
    clear_restart_pending_work,
    get_pending_work,
    list_pending_work,
    mark_restart_pending,
    upsert_pending_work,
)


def test_pending_work_roundtrip(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    item = mark_restart_pending(
        db_path,
        instance="default",
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details="max_players",
    )

    loaded = get_pending_work(db_path, instance="default", kind=KIND_CONFIG)

    assert loaded == item
    assert loaded is not None
    assert loaded.kind == "config"
    assert loaded.source_path == "/config"
    assert loaded.source_action == "config.save"
    assert loaded.title == "Config changes"
    assert loaded.details == "max_players"
    assert loaded.created_by_username == "owner"
    assert loaded.resolution_action == RESOLUTION_RESTART_GAME_SERVER


def test_pending_work_stacks_categories_and_upserts_same_category(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    first_config = mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details="max_players",
    )
    admin = mark_restart_pending(
        db_path,
        kind=KIND_ADMINS,
        source_action="admin.add",
        username="owner",
        details="76561198000000002",
    )

    updated_config = mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="operator",
        details="scenario_id",
    )

    items = list_pending_work(db_path)
    config = get_pending_work(db_path, kind=KIND_CONFIG)

    assert len(items) == 2
    assert {item.kind for item in items} == {KIND_CONFIG, KIND_ADMINS}
    assert config == updated_config
    assert updated_config.id == first_config.id
    assert updated_config.created_at == first_config.created_at
    assert updated_config.details == "scenario_id"
    assert updated_config.created_by_username == "operator"
    assert admin in items


def test_pending_work_redacts_details(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"

    item = mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details=(
            "password=raw-secret token=raw-token secret=raw-secret-value "
            "ARMACTL_WEB_SESSION_SECRET=session-secret"
        ),
    )

    assert "raw-secret" not in item.details
    assert "raw-token" not in item.details
    assert "session-secret" not in item.details
    assert "password=***" in item.details
    assert "token=***" in item.details
    assert "secret=***" in item.details
    assert "ARMACTL_WEB_SESSION_SECRET=***" in item.details


def test_clear_restart_pending_work_only_clears_restart_items(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
    )
    upsert_pending_work(
        db_path,
        kind="schedule",
        source_path="/schedule",
        source_action="schedule.set",
        title="Schedule changes",
        username="owner",
        resolution_action="reload schedule",
    )

    assert clear_restart_pending_work(db_path) == 1
    remaining = list_pending_work(db_path)
    assert len(remaining) == 1
    assert remaining[0].kind == "schedule"
    assert remaining[0].resolution_action == "reload schedule"
    assert clear_pending_work(db_path) == 1
    assert list_pending_work(db_path) == []



def test_existing_pending_restart_marker_migrates_to_pending_work(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE web_pending_restarts (
                instance TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                source_action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by_username TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_pending_restarts(
                instance,
                reason,
                source_action,
                details,
                created_at,
                updated_at,
                created_by_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "default",
                KIND_CONFIG,
                "config.save",
                "max_players",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "owner",
            ),
        )

    ensure_web_db(db_path)

    item = get_pending_work(db_path, kind=KIND_CONFIG)
    assert item is not None
    assert item.source_path == "/config"
    assert item.title == "Config changes"
    assert item.details == "max_players"



def test_clear_restart_pending_work_clears_legacy_marker_source(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE web_pending_restarts (
                instance TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                source_action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by_username TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_pending_restarts(
                instance,
                reason,
                source_action,
                details,
                created_at,
                updated_at,
                created_by_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "default",
                KIND_CONFIG,
                "config.save",
                "max_players",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "owner",
            ),
        )

    assert get_pending_work(db_path, kind=KIND_CONFIG) is not None

    assert clear_restart_pending_work(db_path) >= 1
    assert list_pending_work(db_path) == []
    with sqlite3.connect(db_path) as connection:
        legacy_count = connection.execute(
            "SELECT count(*) FROM web_pending_restarts"
        ).fetchone()[0]
    assert legacy_count == 0
