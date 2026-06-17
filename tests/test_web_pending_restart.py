"""Tests for web pending operator work."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from armactl.web.runtime import ensure_web_db
from armactl.web.services.pending_work import (
    KIND_ADMINS,
    KIND_CONFIG,
    KIND_MODS,
    RESOLUTION_RESTART_GAME_SERVER,
    clear_pending_work,
    clear_restart_pending_fallback,
    clear_restart_pending_work,
    fallback_pending_work_path,
    get_pending_work,
    list_fallback_pending_work,
    list_pending_work,
    list_pending_work_with_fallback,
    mark_restart_pending,
    mark_restart_pending_fallback,
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


def test_fallback_pending_work_roundtrip_is_private_and_redacted(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"

    item = mark_restart_pending_fallback(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner token=raw-user-secret",
        details=(
            "max_players password=raw-password token=raw-token "
            "ARMACTL_WEB_SESSION_SECRET=session-secret"
        ),
    )

    sidecar_path = fallback_pending_work_path(db_path)
    assert sidecar_path.is_file()
    assert sidecar_path.stat().st_mode & 0o777 == 0o600
    sidecar_text = sidecar_path.read_text(encoding="utf-8")
    assert item.is_fallback is True
    assert item.storage_label == "Fallback storage"
    assert item.source_path == "/config"
    assert item.source_action == "config.save"
    assert item.resolution_action == RESOLUTION_RESTART_GAME_SERVER
    assert "raw-password" not in item.details
    assert "raw-token" not in item.details
    assert "session-secret" not in item.details
    assert "raw-user-secret" not in item.created_by_username
    assert "raw-password" not in sidecar_text
    assert "raw-token" not in sidecar_text
    assert "session-secret" not in sidecar_text
    assert "raw-user-secret" not in sidecar_text
    assert list_pending_work_with_fallback(db_path) == [item]


def test_normal_pending_work_suppresses_duplicate_fallback_marker(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    fallback = mark_restart_pending_fallback(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details="fallback detail",
    )
    normal = mark_restart_pending(
        db_path,
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details="normal detail",
    )

    items = list_pending_work_with_fallback(db_path)

    assert fallback.is_fallback is True
    assert items == [normal]
    assert items[0].is_fallback is False


def test_clear_restart_pending_work_clears_fallback_marker(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    mark_restart_pending_fallback(
        db_path,
        kind=KIND_MODS,
        source_action="mod.add",
        username="owner",
        details="CCCCCCCCCCCCCCCC",
    )

    assert list_fallback_pending_work(db_path)

    assert clear_restart_pending_work(db_path) == 1
    assert list_fallback_pending_work(db_path) == []
    assert clear_restart_pending_fallback(db_path) == 0


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
