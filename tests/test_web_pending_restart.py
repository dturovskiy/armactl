"""Tests for web pending-restart operator markers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from armactl.web.runtime import ensure_web_db
from armactl.web.services.pending_restart import (
    REASON_ADMINS,
    REASON_CONFIG,
    clear_pending_restart,
    get_pending_restart,
    mark_pending_restart,
)


def test_pending_restart_marker_roundtrip(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    marker = mark_pending_restart(
        db_path,
        instance="default",
        reason=REASON_CONFIG,
        source_action="config.save",
        username="owner",
        details="max_players",
    )

    loaded = get_pending_restart(db_path, instance="default")

    assert loaded == marker
    assert loaded is not None
    assert loaded.reason == "config"
    assert loaded.source_action == "config.save"
    assert loaded.details == "max_players"
    assert loaded.created_by_username == "owner"


def test_pending_restart_marker_updates_one_row_per_instance(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    first = mark_pending_restart(
        db_path,
        reason=REASON_CONFIG,
        source_action="config.save",
        username="owner",
        details="max_players",
    )

    second = mark_pending_restart(
        db_path,
        reason=REASON_ADMINS,
        source_action="admin.add",
        username="owner",
        details="76561198000000002",
    )

    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at
    assert second.reason == "admins"
    assert second.source_action == "admin.add"
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT count(*) FROM web_pending_restarts").fetchone()[0]
    assert count == 1


def test_pending_restart_marker_redacts_details(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"

    marker = mark_pending_restart(
        db_path,
        reason=REASON_CONFIG,
        source_action="config.save",
        username="owner",
        details="password=raw-secret token=raw-token",
    )

    assert "raw-secret" not in marker.details
    assert "raw-token" not in marker.details
    assert "password=***" in marker.details
    assert "token=***" in marker.details


def test_clear_pending_restart_marker(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    mark_pending_restart(
        db_path,
        reason=REASON_CONFIG,
        source_action="config.save",
        username="owner",
    )

    assert clear_pending_restart(db_path) is True
    assert clear_pending_restart(db_path) is False
    assert get_pending_restart(db_path) is None
