"""Focused tests for the authenticated read-only native ban-list page."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from web_route_helpers import _client, _csrf_cookie_name, _form_token, _login

import armactl.rcon as rcon
from armactl.web.auth.permissions import PLAYERS_MODERATE, PLAYERS_VIEW
from armactl.web.auth.setup import setup_owner_user

NATIVE_BAN_HEADER = "Bans: [BanID] ; [Player UID] ; [Duration]"


def _result(
    *,
    page: int = 1,
    status: str = rcon.NATIVE_BAN_STATUS_COMPLETE,
    entries: tuple[rcon.NativeBanEntry, ...] = (),
    error_code: str = "",
    error: str = "",
    has_previous: bool = False,
    has_next: bool = False,
) -> rcon.NativeBanListResult:
    return rcon.NativeBanListResult(
        requested_page=page,
        available=status != rcon.NATIVE_BAN_STATUS_UNAVAILABLE,
        complete=status == rcon.NATIVE_BAN_STATUS_COMPLETE,
        status=status,
        entries=entries,
        error_code=error_code,
        error=error,
        has_previous=has_previous,
        has_next=has_next,
    )


def _authed_app_client(tmp_path: Path):
    from armactl.web.app import create_app

    password = "native ban list password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    client = _client(app)
    response = _login(client, "owner", password)
    assert response.status_code == 303
    return app, client


def _patch_result(monkeypatch, result: rcon.NativeBanListResult, calls: list | None = None):
    from armactl.web.services import native_banlist

    def load(instance: str, *, page: int) -> rcon.NativeBanListResult:
        if calls is not None:
            calls.append((instance, page))
        return result

    monkeypatch.setattr(native_banlist, "load_native_ban_list", load)


def test_native_ban_list_requires_authentication(tmp_path: Path, monkeypatch) -> None:
    from armactl.web.app import create_app
    from armactl.web.services import native_banlist

    monkeypatch.setattr(native_banlist, "load_native_ban_list", pytest.fail)
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_players_view_without_moderation_permission_gets_403(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
) -> None:
    from armactl.web.services import native_banlist

    set_web_owner_permissions({PLAYERS_VIEW})
    monkeypatch.setattr(native_banlist, "load_native_ban_list", pytest.fail)
    _app, client = _authed_app_client(tmp_path)

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_moderation_permission_alone_can_read_native_ban_page(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
) -> None:
    set_web_owner_permissions({PLAYERS_MODERATE})
    calls: list[tuple[str, int]] = []
    _patch_result(monkeypatch, _result(), calls)
    _app, client = _authed_app_client(tmp_path)

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 200
    assert "Native server ban list" in response.text
    assert "Reforger RCON" in response.text
    assert "Requested page" in response.text
    assert calls == [("default", 1)]


def test_ban_list_tab_is_visible_only_with_moderation_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
) -> None:
    set_web_owner_permissions({PLAYERS_VIEW})
    _app, client = _authed_app_client(tmp_path)
    player_pages = (
        "/players",
        "/players/known",
        "/players/history",
        "/players/sessions",
    )

    for path in player_pages:
        without_permission = client.get(path, follow_redirects=False)
        assert without_permission.status_code == 200
        assert 'href="/players/bans"' not in without_permission.text
        assert "Ban list · Planned" not in without_permission.text

    set_web_owner_permissions({PLAYERS_VIEW, PLAYERS_MODERATE})
    for path in player_pages:
        with_permission = client.get(path, follow_redirects=False)
        assert with_permission.status_code == 200
        assert 'href="/players/bans"' in with_permission.text
        assert "Ban list · Planned" not in with_permission.text


@pytest.mark.parametrize("page", ("0", "101", "-1", "abc", "１"))
def test_native_ban_page_parameter_is_strictly_bounded(
    page: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.services import native_banlist

    monkeypatch.setattr(native_banlist, "load_native_ban_list", pytest.fail)
    _app, client = _authed_app_client(tmp_path)

    response = client.get(f"/players/bans?page={page}", follow_redirects=False)

    assert response.status_code == 400
    assert response.text == "Invalid ban-list page."


def test_native_ban_page_renders_rows_durations_and_navigation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entries = (
        rcon.NativeBanEntry(
            native_ban_id="41",
            player_uid="21761a7f-c9b4-4bff-8375-b4b43abb95ec",
            duration_seconds=0,
        ),
        rcon.NativeBanEntry(
            native_ban_id="42",
            player_uid="718c1fdb-7990-41c8-9c4d-1914dbec1681",
            duration_seconds=3600,
        ),
    )
    _patch_result(
        monkeypatch,
        _result(
            page=2,
            entries=entries,
            has_previous=True,
            has_next=True,
        ),
    )
    _app, client = _authed_app_client(tmp_path)

    response = client.get("/players/bans?page=2", follow_redirects=False)

    assert response.status_code == 200
    assert "21761a7f-c9b4-4bff-8375-b4b43abb95ec" in response.text
    assert "0 seconds (native permanent value)" in response.text
    assert "3600 seconds" in response.text
    assert 'href="/players/bans?page=1"' in response.text
    assert 'href="/players/bans?page=3"' in response.text


def test_unavailable_native_response_is_not_rendered_as_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw = "unexpected 198.51.100.10:19999 password=raw-secret #ban list"
    result = rcon._parse_native_ban_list_response(raw, requested_page=1)
    _patch_result(monkeypatch, result)
    _app, client = _authed_app_client(tmp_path)

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 200
    assert "Native ban list unavailable." in response.text
    assert "Native server ban list is empty on this page." not in response.text
    for forbidden in (
        raw,
        "raw-secret",
        "198.51.100.10",
        "19999",
        "#ban list",
        "password",
    ):
        assert forbidden not in response.text


def test_partial_native_response_is_clearly_labelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    result = rcon._parse_native_ban_list_response(
        "\n".join(
            (
                NATIVE_BAN_HEADER,
                "1 ; 21761a7f-c9b4-4bff-8375-b4b43abb95ec ; 3600",
                "2 ; truncated",
            )
        ),
        requested_page=1,
    )
    _patch_result(monkeypatch, result)
    _app, client = _authed_app_client(tmp_path)

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 200
    assert "Partial native ban list." in response.text
    assert "not authoritative as a complete list" in response.text
    assert "21761a7f-c9b4-4bff-8375-b4b43abb95ec" in response.text
    assert "Native server ban list is empty on this page." not in response.text


def test_complete_empty_native_page_has_explicit_empty_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_result(monkeypatch, _result())
    _app, client = _authed_app_client(tmp_path)

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 200
    assert "Native server ban list is empty on this page." in response.text
    assert "Native ban list unavailable." not in response.text


def test_native_ban_surface_has_no_mutation_controls_or_routes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_result(monkeypatch, _result())
    app, client = _authed_app_client(tmp_path)

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 200
    for forbidden in (
        'action="/players/bans',
        "#ban create",
        "#ban remove",
        "#kick",
        ">Unban<",
        ">Kick<",
        "Edit reason",
    ):
        assert forbidden not in response.text
    matching_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "").startswith("/players/bans")
    ]
    assert [(route.path, route.methods) for route in matching_routes] == [
        ("/players/bans", {"GET"})
    ]
    assert not any(
        getattr(route, "path", "").startswith(("/rcon", "/console"))
        for route in app.routes
    )


def test_native_ban_get_creates_no_ban_or_player_storage_and_no_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_result(monkeypatch, _result())
    _app, client = _authed_app_client(tmp_path)
    players_db = tmp_path / "default" / "players.db"
    audit_log = tmp_path / "logs" / "web" / "audit.log"
    assert not players_db.exists()
    assert not audit_log.exists()
    web_db = tmp_path / "web" / "web.db"
    with sqlite3.connect(web_db) as connection:
        job_count_before = int(
            connection.execute("SELECT COUNT(*) FROM web_jobs").fetchone()[0]
        )

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 200
    assert not players_db.exists()
    assert not audit_log.exists()
    with sqlite3.connect(web_db) as connection:
        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        job_count = int(connection.execute("SELECT COUNT(*) FROM web_jobs").fetchone()[0])
    assert not any("ban" in table_name.casefold() for table_name in table_names)
    assert job_count == job_count_before


def test_native_ban_page_refreshes_action_csrf_when_cookie_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_result(monkeypatch, _result())
    _app, client = _authed_app_client(tmp_path)
    client.cookies.delete(_csrf_cookie_name(client))

    response = client.get("/players/bans", follow_redirects=False)

    assert response.status_code == 200
    csrf_token = _form_token(response.text)
    assert csrf_token
    assert response.cookies.get(_csrf_cookie_name(client)) == csrf_token

    logout = client.post(
        "/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"


def test_native_ban_service_parses_page_and_calls_typed_adapter(monkeypatch) -> None:
    from armactl.web.services import native_banlist

    calls: list[tuple[str, int]] = []
    expected = _result(page=7)

    def query(instance: str, *, page: int):
        calls.append((instance, page))
        return expected

    monkeypatch.setattr(rcon, "query_native_ban_list", query)

    assert native_banlist.parse_page_parameter(" 7 ") == 7
    assert native_banlist.parse_page_parameter("0") is None
    assert native_banlist.parse_page_parameter("101") is None
    assert native_banlist.load_native_ban_list("default", page=7) is expected
    assert calls == [("default", 7)]
