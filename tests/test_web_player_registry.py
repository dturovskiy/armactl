"""Tests for persistent web player registry foundation."""

from __future__ import annotations

import json
import re
import sqlite3
import warnings
from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.permissions import PLAYERS_VIEW
from armactl.web.auth.setup import setup_owner_user
from armactl.web.services.player_registry import PlayerObservation
from armactl.web.services.player_sources import CurrentPlayer, CurrentPlayerRoster

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


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
) -> PlayerObservation:
    return PlayerObservation(
        reliable_id=reliable_id,
        display_name=name,
        source="rcon.guid",
    )


def _unreliable_player(name: str = "Slot Only") -> PlayerObservation:
    return PlayerObservation(
        reliable_id="",
        display_name=name,
        source="rcon.roster",
    )


def _current_player(
    name: str = "Alpha",
    reliable_id: str = "ABCDEF1234567890",
) -> CurrentPlayer:
    return CurrentPlayer(
        display_name=name,
        reliable_id=reliable_id,
        admin_reference=reliable_id,
        source="rcon.guid",
    )


def _unreliable_current_player(name: str = "Slot Only") -> CurrentPlayer:
    return CurrentPlayer(
        display_name=name,
        reliable_id="",
        admin_reference="",
        source="rcon.roster",
    )


def _roster(*players: CurrentPlayer) -> CurrentPlayerRoster:
    return CurrentPlayerRoster(
        available=True,
        players=tuple(players),
        total_count=len(players),
        source="rcon.roster",
        status="available",
        error="",
    )


def _parse_log_event(line: str, **kwargs):
    from armactl import player_log_events

    event = player_log_events.parse_player_log_event(line, **kwargs)
    assert event is not None
    return event


def _registry_schema_version(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT value
            FROM player_registry_schema_meta
            WHERE key = 'schema_version'
            """
        ).fetchone()
    assert row is not None
    return str(row[0])


def _sqlite_tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _sqlite_indexes(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index'
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def _authed_client(tmp_path: Path, monkeypatch, *, db_path: Path | None = None):
    from armactl.web.app import create_app
    from armactl.web.services import player_registry

    setup_owner_user(tmp_path, "owner", "owner players password")
    if db_path is not None:
        monkeypatch.setattr(
            player_registry,
            "player_registry_db_path",
            lambda instance, data_root=None: db_path,
        )
    app = create_app(data_root=tmp_path)
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


def test_registry_db_creation_has_schema_metadata(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    player_registry.ensure_player_registry_db(db_path)

    assert "player_registry_schema_meta" in _sqlite_tables(db_path)
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert "idx_player_names_name" in _sqlite_indexes(db_path)


def test_registry_db_migrates_existing_minimal_schema_idempotently(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE players (
                reliable_id TEXT PRIMARY KEY,
                current_name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE player_names (
                reliable_id TEXT NOT NULL,
                name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (reliable_id, name)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO players(
                reliable_id,
                current_name,
                first_seen_at,
                last_seen_at,
                seen_count
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "ABCDEF1234567890",
                "Alpha",
                "2026-06-16T12:00:00+00:00",
                "2026-06-16T12:00:00+00:00",
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO player_names(
                reliable_id,
                name,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "ABCDEF1234567890",
                "Alpha",
                "2026-06-16T12:00:00+00:00",
                "2026-06-16T12:00:00+00:00",
            ),
        )
    db_path.chmod(0o644)

    player_registry.ensure_player_registry_db(db_path)

    assert db_path.stat().st_mode & 0o777 == 0o600
    assert _registry_schema_version(db_path) == player_registry.PLAYER_REGISTRY_SCHEMA_VERSION
    assert "last_source" in _sqlite_columns(db_path, "players")
    assert "seen_count" in _sqlite_columns(db_path, "player_names")
    assert "ip_address" not in _sqlite_columns(db_path, "players")
    assert "idx_player_names_name" in _sqlite_indexes(db_path)
    assert "player_log_events" in _sqlite_tables(db_path)
    assert "idx_player_log_events_observed_at" in _sqlite_indexes(db_path)
    assert player_registry.list_known_players(db_path)[0].last_source == "unknown"
    assert player_registry.list_player_names(db_path, "ABCDEF1234567890")[0].seen_count == 1

    with sqlite3.connect(db_path) as connection:
        first_snapshot = (
            connection.execute("SELECT * FROM players ORDER BY reliable_id").fetchall(),
            connection.execute("SELECT * FROM player_names ORDER BY reliable_id, name").fetchall(),
            connection.execute("SELECT * FROM player_registry_schema_meta ORDER BY key").fetchall(),
        )
    player_registry.ensure_player_registry_db(db_path)

    with sqlite3.connect(db_path) as connection:
        second_snapshot = (
            connection.execute("SELECT * FROM players ORDER BY reliable_id").fetchall(),
            connection.execute("SELECT * FROM player_names ORDER BY reliable_id, name").fetchall(),
            connection.execute("SELECT * FROM player_registry_schema_meta ORDER BY key").fetchall(),
        )
    assert second_snapshot == first_snapshot


def test_registry_db_creation_uses_private_file_mode(tmp_path: Path):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"

    player_registry.ensure_player_registry_db(db_path)

    assert db_path.stat().st_mode & 0o777 == 0o600

    db_path.chmod(0o644)
    player_registry.ensure_player_registry_db(db_path)

    assert db_path.stat().st_mode & 0o777 == 0o600


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
    history_response = client.get("/players/history", follow_redirects=False)
    post_response = client.post("/players/refresh", data={}, follow_redirects=False)

    assert get_response.status_code == 303
    assert get_response.headers["location"] == "/login"
    assert history_response.status_code == 303
    assert history_response.headers["location"] == "/login"
    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/login"


def test_players_route_requires_players_view_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.services import player_actions

    setup_owner_user(tmp_path, "owner", "owner players password")
    set_web_owner_permissions(set())
    monkeypatch.setattr(player_actions, "refresh_registry_and_audit", AssertionError)
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", "owner players password")

    response = client.get("/players", follow_redirects=False)
    history_response = client.get("/players/history", follow_redirects=False)
    post_response = client.post(
        "/players/refresh",
        data={"csrf_token": "unused"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert history_response.status_code == 403
    assert history_response.text == "Permission denied."
    assert post_response.status_code == 403
    assert post_response.text == "Permission denied."
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


def test_player_history_route_renders_empty_state_and_migrates_v1_db(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "default" / "players.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE player_registry_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO player_registry_schema_meta(key, value)
            VALUES ('schema_version', '1')
            """
        )
        connection.execute(
            """
            CREATE TABLE players (
                reliable_id TEXT PRIMARY KEY,
                current_name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL,
                last_source TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE player_names (
                reliable_id TEXT NOT NULL,
                name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL,
                PRIMARY KEY (reliable_id, name)
            )
            """
        )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history", follow_redirects=False)

    assert response.status_code == 200
    assert "No player history events recorded yet." in response.text
    assert "player_log_events" in _sqlite_tables(db_path)
    assert _registry_schema_version(db_path) == "2"


def test_player_history_route_renders_stored_rows_without_raw_sources(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    raw_auth_line = (
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha 203.0.113.9"
    )
    parsed_events = [
        _parse_log_event(
            raw_auth_line,
            observed_at="2026-01-01T12:00:01+00:00",
            raw_source_ref=(
                "/home/deus/armactl-data/default/config/logs/run/"
                "console-198.51.100.7.log:203.0.113.9:1"
            ),
        ),
        _parse_log_event(
            "NETWORK : ### Updating player: PlayerId=7, Name=Alpha One, "
            f"rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
            observed_at="2026-01-01T12:00:02+00:00",
            raw_source_ref="journal:update",
        ),
        _parse_log_event(
            "SCRIPT : INFO: Faction: player Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) "
            "has joined faction #US_Army (US)",
            observed_at="2026-01-01T12:00:03+00:00",
            raw_source_ref="journal:faction",
        ),
        _parse_log_event(
            "SCRIPT : INFO: KILL TK: Bravo Two "
            f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
            "at <4 5 6> was killed by Alpha One "
            f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
            "who was at that time at <4 5 7> [2.2m away from the corpse]. "
            "With last inflicted damage type Bullet to the 'LeftArm' hit zone",
            observed_at="2026-01-01T12:00:04+00:00",
            raw_source_ref="journal:teamkill",
        ),
    ]
    player_registry.ingest_player_log_events(
        db_path,
        parsed_events,
        ingested_at="2026-01-01T12:00:05+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history", follow_redirects=False)

    assert response.status_code == 200
    html = response.text
    assert "2026-01-01 12:00 UTC" in html
    assert "player_authenticated" in html
    assert "player_update" in html
    assert "faction_join" in html
    assert "teamkill" in html
    assert "Alpha ***" in html
    assert "Alpha One" in html
    assert "Bravo Two" in html
    assert PLAYER_ALPHA_ID in html
    assert PLAYER_BRAVO_ID in html
    assert "US_Army" in html
    assert "US" in html
    assert "Bullet" in html
    assert "LeftArm" in html
    assert "2.2 m" in html
    assert "console-***.log:***" in html
    assert "/home/deus" not in html
    assert "armactl-data" not in html
    assert "198.51.100.7" not in html
    assert "203.0.113.9" not in html
    assert raw_auth_line not in html


def test_player_history_route_ignores_unknown_event_type_filter(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry

    db_path = tmp_path / "default" / "players.db"
    player_registry.ingest_player_log_events(
        db_path,
        [
            _parse_log_event(
                "BACKEND : Authenticated player: "
                f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
                observed_at="2026-01-01T12:00:01+00:00",
                raw_source_ref="journal:alpha-auth",
            )
        ],
        ingested_at="2026-01-01T12:00:05+00:00",
    )
    client = _authed_client(tmp_path, monkeypatch)

    response = client.get("/players/history?event_type=not-real", follow_redirects=False)

    assert response.status_code == 200
    assert "player_authenticated" in response.text
    assert "not-real" not in response.text


def test_players_refresh_requires_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.services import player_actions, player_registry

    db_path = tmp_path / "default" / "players.db"
    client = _authed_client(tmp_path, monkeypatch, db_path=db_path)
    monkeypatch.setattr(player_actions, "refresh_registry_and_audit", AssertionError)

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
    from armactl.web.services import player_registry, player_sources

    db_path = tmp_path / "default" / "players.db"
    setup_owner_user(tmp_path, "owner", "owner players password")
    monkeypatch.setattr(
        player_registry,
        "player_registry_db_path",
        lambda instance, data_root=None: db_path,
    )
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(
            _current_player("Alpha"),
            _unreliable_current_player(),
        ),
    )
    app = create_app(data_root=tmp_path)
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
    from armactl.web.services import player_registry, player_sources

    setup_owner_user(tmp_path, "owner", "owner players password")
    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Alpha")),
    )
    app = create_app(data_root=tmp_path)
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


def test_players_refresh_route_delegates_persistence_and_audit_to_service(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import player_actions, player_registry

    setup_owner_user(tmp_path, "owner", "owner players password")
    db_path = tmp_path / "default" / "players.db"
    calls: dict[str, object] = {}

    def refresh_registry_and_audit(instance, **kwargs):
        calls["instance"] = instance
        calls.update(kwargs)
        return player_actions.PlayerRefreshResult(stored_count=1, ignored_count=0)

    monkeypatch.setattr(
        player_actions,
        "refresh_registry_and_audit",
        refresh_registry_and_audit,
    )
    monkeypatch.setattr(
        player_registry,
        "player_registry_db_path",
        lambda instance, data_root=None: db_path,
    )
    monkeypatch.setattr(player_registry, "list_known_players", lambda db_path, query="": [])
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", "owner players password")
    csrf_token = _players_csrf_token(client)

    response = client.post(
        "/players/refresh",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert calls["instance"] == "default"
    assert calls["data_root"] == tmp_path
    assert calls["audit_log_path"] == tmp_path / "logs" / "web" / "audit.log"
    assert calls["username"] == "owner"
    assert "Recorded 1 reliable player(s); ignored 0 unreliable row(s)." in response.text
    assert not db_path.exists()


def test_players_refresh_intent_audit_failure_aborts_roster_load_and_registry_write(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_registry, player_sources
    from armactl.web.services.audit import AuditLogError

    calls = {"roster": 0, "registry": 0}

    def fail_intent(*args, **kwargs):
        if (kwargs.get("details") or {}).get("phase") == "intent":
            raise AuditLogError("disk full token=raw-audit-secret")
        raise AssertionError("unexpected outcome audit")

    def load_roster(instance):
        calls["roster"] += 1
        raise AssertionError("roster should not load")

    def record_snapshot(db_path, observations):
        calls["registry"] += 1
        raise AssertionError("registry should not write")

    monkeypatch.setattr(player_actions, "append_audit_event", fail_intent)
    monkeypatch.setattr(player_sources, "load_current_player_roster", load_roster)
    monkeypatch.setattr(
        player_registry,
        "record_current_players_snapshot",
        record_snapshot,
    )

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is False
    assert result.intent_audited is False
    assert result.backend_success is False
    assert result.audit_written is False
    assert result.message == "Player registry refresh was not run because audit logging failed."
    assert "raw-audit-secret" not in result.audit_error
    assert calls == {"roster": 0, "registry": 0}
    assert not (tmp_path / "default" / "players.db").exists()


def test_players_refresh_success_writes_outcome_audit_success(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_registry, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(
            _current_player("Alpha token=raw-player-secret"),
            _unreliable_current_player("Slot token=raw-slot-secret"),
        ),
    )

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is True
    assert result.backend_success is True
    assert result.audit_written is True
    assert result.stored_count == 1
    assert result.ignored_count == 1
    assert result.reliable_count == 1
    assert [
        player.current_name
        for player in player_registry.list_known_players(tmp_path / "default" / "players.db")
    ] == ["Alpha token=***"]
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    outcome = events[1]
    assert outcome["action"] == "players.refresh"
    assert outcome["instance"] == "default"
    assert outcome["success"] is True
    assert outcome["message"] == "Player registry refreshed."
    assert outcome["details"] == {
        "phase": "outcome",
        "action": "players.refresh",
        "instance": "default",
        "source": "rcon.roster",
        "reliable_count": "1",
        "recorded_count": "1",
        "ignored_count": "1",
    }
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "Alpha" not in audit_text
    assert "raw-player-secret" not in audit_text
    assert "raw-slot-secret" not in audit_text


def test_players_refresh_source_failure_writes_failure_audit_and_renders_controlled_message(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_sources

    def fail_roster(instance):
        raise RuntimeError("roster unavailable token=raw-roster-secret")

    monkeypatch.setattr(player_sources, "load_current_player_roster", fail_roster)
    client = _authed_client(tmp_path, monkeypatch)
    csrf_token = _players_csrf_token(client)

    response = client.post(
        "/players/refresh",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Player registry refresh failed while loading current players." in response.text
    assert "raw-roster-secret" not in response.text
    assert not (tmp_path / "default" / "players.db").exists()
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    outcome = events[1]
    assert outcome["success"] is False
    assert outcome["message"] == "Player registry refresh failed while loading current players."
    assert outcome["details"]["source"] == "unavailable"
    assert outcome["details"]["reliable_count"] == "0"
    assert outcome["details"]["recorded_count"] == "0"
    assert outcome["details"]["reason_class"] == "RuntimeError"
    assert outcome["details"]["reason_message"] == (
        "Player registry refresh failed while loading current players."
    )
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "raw-roster-secret" not in audit_text


def test_players_refresh_registry_failure_writes_failure_audit_and_renders_controlled_message(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_registry, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Alpha token=raw-player-secret")),
    )

    def fail_record(db_path, observations):
        tuple(observations)
        raise OSError("write failed token=raw-registry-secret")

    monkeypatch.setattr(
        player_registry,
        "record_current_players_snapshot",
        fail_record,
    )
    client = _authed_client(tmp_path, monkeypatch)
    csrf_token = _players_csrf_token(client)

    response = client.post(
        "/players/refresh",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Player registry refresh failed while saving current players." in response.text
    assert "raw-registry-secret" not in response.text
    assert not (tmp_path / "default" / "players.db").exists()
    events = _audit_events(tmp_path)
    outcome = events[1]
    assert outcome["success"] is False
    assert outcome["message"] == "Player registry refresh failed while saving current players."
    assert outcome["details"]["source"] == "rcon.roster"
    assert outcome["details"]["reliable_count"] == "1"
    assert outcome["details"]["recorded_count"] == "0"
    assert outcome["details"]["reason_class"] == "OSError"
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert "Alpha" not in audit_text
    assert "raw-player-secret" not in audit_text
    assert "raw-registry-secret" not in audit_text


def test_players_refresh_successful_persist_reports_outcome_audit_failure(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_registry, player_sources
    from armactl.web.services.audit import AuditLogError, append_audit_event

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: _roster(_current_player("Alpha token=raw-player-secret")),
    )

    def fail_success_outcome(audit_log_path, **kwargs):
        is_success_outcome = (
            kwargs.get("success") is True
            and (kwargs.get("details") or {}).get("phase") == "outcome"
        )
        if is_success_outcome:
            raise AuditLogError("disk full token=raw-outcome-secret")
        return append_audit_event(audit_log_path, **kwargs)

    monkeypatch.setattr(player_actions, "append_audit_event", fail_success_outcome)
    client = _authed_client(tmp_path, monkeypatch)
    csrf_token = _players_csrf_token(client)

    response = client.post(
        "/players/refresh",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert "Player registry refreshed, but audit logging failed." in response.text
    assert "Recorded 1 reliable player(s); ignored 0 unreliable row(s)." in response.text
    assert "raw-outcome-secret" not in response.text
    known = player_registry.list_known_players(tmp_path / "default" / "players.db")
    assert [(player.reliable_id, player.current_name) for player in known] == [
        ("ABCDEF1234567890", "Alpha token=***")
    ]
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent"]


def test_players_refresh_failure_outcome_audit_failure_is_controlled(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_sources
    from armactl.web.services.audit import AuditLogError, append_audit_event

    def fail_roster(instance):
        raise RuntimeError("roster unavailable token=raw-roster-secret")

    def fail_failure_outcome(audit_log_path, **kwargs):
        is_failure_outcome = (
            kwargs.get("success") is False
            and (kwargs.get("details") or {}).get("phase") == "outcome"
        )
        if is_failure_outcome:
            raise AuditLogError("disk full token=raw-audit-secret")
        return append_audit_event(audit_log_path, **kwargs)

    monkeypatch.setattr(player_sources, "load_current_player_roster", fail_roster)
    monkeypatch.setattr(player_actions, "append_audit_event", fail_failure_outcome)

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is False
    assert result.backend_success is False
    assert result.audit_written is False
    assert result.message == "Player registry refresh failed, and audit logging also failed."
    assert "raw-audit-secret" not in result.audit_error
    assert "raw-roster-secret" not in result.message
    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent"]


def test_players_refresh_audit_details_exclude_display_names_and_raw_secrets(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import player_actions, player_sources

    monkeypatch.setattr(
        player_sources,
        "load_current_player_roster",
        lambda instance: CurrentPlayerRoster(
            available=True,
            players=(
                _current_player("Alpha token=raw-player-secret"),
                _unreliable_current_player("Slot token=raw-slot-secret"),
            ),
            total_count=2,
            source="rcon.roster token=raw-source-secret",
            status="available",
            error="",
        ),
    )

    result = player_actions.refresh_registry_and_audit(
        "default",
        data_root=tmp_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
    )

    assert result.success is True
    outcome_details = _audit_events(tmp_path)[1]["details"]
    assert outcome_details == {
        "phase": "outcome",
        "action": "players.refresh",
        "instance": "default",
        "source": "rcon.roster token=***",
        "reliable_count": "1",
        "recorded_count": "1",
        "ignored_count": "1",
    }
    encoded_details = json.dumps(outcome_details, sort_keys=True)
    assert "Alpha" not in encoded_details
    assert "Slot" not in encoded_details
    assert "raw-player-secret" not in encoded_details
    assert "raw-slot-secret" not in encoded_details
    assert "raw-source-secret" not in encoded_details


def test_player_registry_tests_do_not_use_route_global_monkeypatch_pattern():
    forbidden = (
        "__" + "globals__",
        "endpoint." + "__" + "globals__",
        "app." + "router",
        "dependency_" + "overrides",
    )
    test_root = Path(__file__).parent
    for test_path in test_root.glob("test_*.py"):
        source = test_path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in source, f"{pattern} found in {test_path}"
