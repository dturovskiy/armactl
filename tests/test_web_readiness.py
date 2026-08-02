"""Readiness endpoint and schema compatibility tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from web_route_helpers import _client

from armactl.web.runtime import ensure_web_runtime
from armactl.web.services import player_registry


def _set_schema_version(db_path: Path, table: str, version: int) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE {table} SET value = ? WHERE key = 'schema_version'",
            (str(version),),
        )


def test_readyz_accepts_current_web_and_player_schemas(tmp_path: Path) -> None:
    from armactl.web.app import create_app

    config = ensure_web_runtime(tmp_path)
    players_db = player_registry.player_registry_db_path(data_root=tmp_path)
    player_registry.ensure_player_registry_db(players_db)

    response = _client(create_app(data_root=tmp_path)).get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "checks": {
            "web_db": {"ok": True, "status": "ready"},
            "players_db": {"ok": True, "status": "ready"},
        },
    }
    assert config.db_path.is_file()


def test_readyz_accepts_missing_optional_players_database_without_creating_it(
    tmp_path: Path,
) -> None:
    from armactl.web.app import create_app

    ensure_web_runtime(tmp_path)
    players_db = player_registry.player_registry_db_path(data_root=tmp_path)

    response = _client(create_app(data_root=tmp_path)).get("/readyz")

    assert response.status_code == 200
    assert response.json()["checks"]["players_db"] == {
        "ok": True,
        "status": "not_configured",
    }
    assert not players_db.exists()


def test_readyz_rejects_newer_player_schema_without_mutating_it(tmp_path: Path) -> None:
    from armactl.web.app import create_app

    ensure_web_runtime(tmp_path)
    players_db = player_registry.player_registry_db_path(data_root=tmp_path)
    player_registry.ensure_player_registry_db(players_db)
    newer_version = int(player_registry.PLAYER_REGISTRY_SCHEMA_VERSION) + 1
    _set_schema_version(
        players_db,
        "player_registry_schema_meta",
        newer_version,
    )

    response = _client(create_app(data_root=tmp_path)).get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["players_db"] == {
        "ok": False,
        "status": "schema_newer",
    }
    with sqlite3.connect(players_db) as connection:
        stored = connection.execute(
            "SELECT value FROM player_registry_schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert stored == (str(newer_version),)


def test_readyz_requires_existing_compatible_web_database(tmp_path: Path) -> None:
    from armactl.web.app import create_app

    response = _client(create_app(data_root=tmp_path)).get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["web_db"] == {
        "ok": False,
        "status": "missing",
    }


def test_healthz_stays_liveness_when_readiness_fails(tmp_path: Path) -> None:
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/readyz").status_code == 503
