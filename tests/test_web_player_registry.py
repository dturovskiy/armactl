"""Tests for persistent web player registry foundation."""

from __future__ import annotations

import re
import sqlite3
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.permissions import PLAYERS_VIEW
from armactl.web.auth.setup import setup_owner_user
from armactl.web.services.player_moderation import ModerationPlayer, PlayerModerationPanel


def _client(app):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(app)


def _form_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _login(client, username: str, password: str):
    form_response = client.get("/login")
    csrf_token = _form_token(form_response.text)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf_token},
        follow_redirects=False,
    )


def _reliable_player(
    name: str = "Alpha",
    reliable_id: str = "ABCDEF1234567890",
) -> ModerationPlayer:
    return ModerationPlayer(
        display_name=name,
        identity_id=reliable_id,
        admin_reference=reliable_id,
        source="rcon.guid",
        status="active",
        last_seen="online now",
    )


def _unreliable_player(name: str = "Slot Only") -> ModerationPlayer:
    return ModerationPlayer(
        display_name=name,
        identity_id="",
        admin_reference="",
        source="rcon.roster",
        status="active",
        last_seen="online now",
    )


def _panel(*players: ModerationPlayer) -> PlayerModerationPanel:
    return PlayerModerationPanel(
        available=True,
        query="",
        players=tuple(players),
        total_count=len(players),
        filtered_count=len(players),
        source="rcon.roster",
        status="available",
        error="",
    )


def _patch_route_global(app, module_name: str, monkeypatch, name: str, value) -> None:
    patched = False
    module = sys.modules.get(module_name)
    if module is not None and hasattr(module, name):
        monkeypatch.setattr(module, name, value)
        patched = True
    for route in getattr(app, "routes", []):
        endpoint = getattr(route, "endpoint", None)
        globals_dict = getattr(endpoint, "__globals__", None)
        if isinstance(globals_dict, dict) and name in globals_dict:
            monkeypatch.setitem(globals_dict, name, value)
            patched = True
    assert patched, f"route global was not patched: {module_name}.{name}"


def _authed_client(tmp_path: Path, monkeypatch, *, db_path: Path | None = None):
    from armactl.web.app import create_app
    from armactl.web.routes import players as players_route

    setup_owner_user(tmp_path, "owner", "owner players password")
    app = create_app(data_root=tmp_path)
    if db_path is not None:
        _patch_route_global(
            app,
            players_route.__name__,
            monkeypatch,
            "player_registry",
            SimpleNamespace(
                **{
                    name: getattr(players_route.player_registry, name)
                    for name in (
                        "ensure_player_registry_db",
                        "record_current_players_snapshot",
                        "list_known_players",
                        "get_known_player",
                        "list_player_names",
                        "PlayerSnapshotResult",
                    )
                },
                player_registry_db_path=lambda instance, data_root=None: db_path,
            ),
        )
    client = _client(app)
    login_response = _login(client, "owner", "owner players password")
    assert login_response.status_code == 303
    return client


def _players_csrf_token(client) -> str:
    response = client.get("/players", follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def test_registry_db_creation_has_no_ip_columns(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    player_registry.ensure_player_registry_db(db_path)

    assert db_path.is_file()
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for table in ("players", "player_names")
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
    assert "ip" not in columns
    assert "ip_address" not in columns
    assert "token" not in columns
    assert "password" not in columns


def test_record_snapshot_stores_reliable_players_and_ignores_unreliable(
    tmp_path: Path,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    result = player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("Alpha token=raw-secret"), _unreliable_player()],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    players = player_registry.list_known_players(db_path)

    assert result.stored_count == 1
    assert result.ignored_count == 1
    assert len(players) == 1
    assert players[0].reliable_id == "ABCDEF1234567890"
    assert players[0].current_name == "Alpha token=***"
    assert players[0].seen_count == 1


def test_record_snapshot_updates_name_history(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("Alpha")],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("Bravo")],
        observed_at="2026-06-16T12:05:00+00:00",
    )

    player = player_registry.get_known_player(db_path, "ABCDEF1234567890")
    names = player_registry.list_player_names(db_path, "ABCDEF1234567890")

    assert player is not None
    assert player.current_name == "Bravo"
    assert player.first_seen_at == "2026-06-16T12:00:00+00:00"
    assert player.last_seen_at == "2026-06-16T12:05:00+00:00"
    assert player.seen_count == 2
    assert [name.name for name in names] == ["Bravo", "Alpha"]


def test_list_known_players_searches_by_name_and_id(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [
            _reliable_player("Alpha", "ABCDEF1234567890"),
            _reliable_player("Bravo", "BBBBBBBBBBBBBBBB"),
        ],
        observed_at="2026-06-16T12:00:00+00:00",
    )

    by_name = player_registry.list_known_players(db_path, query="brav")
    by_id = player_registry.list_known_players(db_path, query="cdef")

    assert [player.current_name for player in by_name] == ["Bravo"]
    assert [player.current_name for player in by_id] == ["Alpha"]


def test_players_route_requires_authentication(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    get_response = client.get("/players", follow_redirects=False)
    post_response = client.post("/players/refresh", data={}, follow_redirects=False)

    assert get_response.status_code == 303
    assert get_response.headers["location"] == "/login"
    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/login"


def test_players_route_requires_players_view_permission(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import players as players_route

    setup_owner_user(tmp_path, "owner", "owner players password")
    app = create_app(data_root=tmp_path)
    _patch_route_global(
        app,
        players_route.__name__,
        monkeypatch,
        "require_permission",
        lambda current, permission: False,
    )
    client = _client(app)
    _login(client, "owner", "owner players password")

    response = client.get("/players", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert not PLAYERS_VIEW.endswith(":manage")


def test_players_route_html_escapes_player_names(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.record_current_players_snapshot(
        db_path,
        [_reliable_player("<Alpha & Co>")],
        observed_at="2026-06-16T12:00:00+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch, db_path=db_path)

    response = client.get("/players", follow_redirects=False)

    assert response.status_code == 200
    assert "&lt;Alpha &amp; Co&gt;" in response.text
    assert "<Alpha & Co>" not in response.text


def test_players_refresh_requires_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    client = _authed_client(tmp_path, monkeypatch, db_path=db_path)

    response = client.post(
        "/players/refresh",
        data={"csrf_token": "wrong-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert player_registry.list_known_players(db_path) == []


def test_players_refresh_records_reliable_current_players(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.routes import players as players_route
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    setup_owner_user(tmp_path, "owner", "owner players password")
    app = create_app(data_root=tmp_path)
    _patch_route_global(
        app,
        players_route.__name__,
        monkeypatch,
        "player_registry",
        SimpleNamespace(
            **{
                name: getattr(players_route.player_registry, name)
                for name in (
                    "ensure_player_registry_db",
                    "record_current_players_snapshot",
                    "list_known_players",
                    "get_known_player",
                    "list_player_names",
                    "PlayerSnapshotResult",
                )
            },
            player_registry_db_path=lambda instance, data_root=None: db_path,
        ),
    )
    _patch_route_global(
        app,
        players_route.__name__,
        monkeypatch,
        "player_moderation",
        SimpleNamespace(
            load_player_moderation_panel=lambda instance: _panel(
                _reliable_player("Alpha"),
                _unreliable_player(),
            )
        ),
    )
    client = _client(app)
    _login(client, "owner", "owner players password")
    csrf_token = _players_csrf_token(client)

    response = client.post(
        "/players/refresh",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Recorded 1 reliable player(s); ignored 1 unreliable row(s)." in response.text
    known = player_registry.list_known_players(db_path)
    assert [(player.reliable_id, player.current_name) for player in known] == [
        ("ABCDEF1234567890", "Alpha")
    ]


def test_players_refresh_uses_runtime_data_root(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import players as players_route
    from armactl.web.services import player_registry

    setup_owner_user(tmp_path, "owner", "owner players password")
    app = create_app(data_root=tmp_path)
    _patch_route_global(
        app,
        players_route.__name__,
        monkeypatch,
        "player_moderation",
        SimpleNamespace(
            load_player_moderation_panel=lambda instance: _panel(_reliable_player("Alpha"))
        ),
    )
    client = _client(app)
    _login(client, "owner", "owner players password")
    csrf_token = _players_csrf_token(client)

    response = client.post(
        "/players/refresh",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    db_path = tmp_path / "default" / "players.db"
    assert response.status_code == 200
    assert db_path.is_file()
    assert [player.current_name for player in player_registry.list_known_players(db_path)] == [
        "Alpha"
    ]
