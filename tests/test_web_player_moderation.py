"""Tests for web player/moderation foundation."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

from starlette.exceptions import StarletteDeprecationWarning
from web_route_helpers import _session_cookie_name

from armactl.player_view import PlayerView
from armactl.rcon import PlayerEntry
from armactl.state import ServerState
from armactl.web.auth.permissions import ADMINS_VIEW
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username
from armactl.web.page_models.players import (
    ModerationPlayer,
    PlayerModerationPanel,
)


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


def _admins_page() -> dict:
    return {
        "instance": "default",
        "available": True,
        "error": "",
        "status": {"lifecycle": "running", "installed": True, "running": True},
        "paths": {"config_path": "/srv/armactl-data/default/config/config.json"},
        "official_count": 0,
        "local_label_count": 0,
        "local_labels_path": "/srv/armactl-data/default/admins-state.json",
        "local_labels_error": "",
        "official_admins": [],
    }


def _state(config_path: Path | None = None) -> ServerState:
    return ServerState(
        server_installed=True,
        config_exists=bool(config_path),
        service_exists=True,
        server_running=True,
        config_path=str(config_path or ""),
    )


def _panel(
    *players: ModerationPlayer,
    query: str = "",
    available: bool = True,
) -> PlayerModerationPanel:
    return PlayerModerationPanel(
        available=available,
        query=query,
        players=tuple(players),
        total_count=len(players),
        filtered_count=len(players),
        source="rcon.roster",
        status="available" if available else "unavailable",
        error="",
    )


def _reliable_player(name: str = "Alpha") -> ModerationPlayer:
    return ModerationPlayer(
        display_name=name,
        identity_id="21761a7f-c9b4-4bff-8375-b4b43abb95ec",
        admin_reference="21761a7f-c9b4-4bff-8375-b4b43abb95ec",
        source="rcon.guid",
        status="active",
        last_seen="online now",
    )


def _readonly_player(name: str = "Observer") -> ModerationPlayer:
    return ModerationPlayer(
        display_name=name,
        identity_id="",
        admin_reference="",
        source="rcon.roster",
        status="active",
        last_seen="online now",
    )


def _patch_admins_page(monkeypatch) -> None:
    from armactl.web.page_models import admins as admins_page_model

    monkeypatch.setattr(admins_page_model, "load_admins_page", lambda instance: _admins_page())


def _patch_player_panel(monkeypatch, load_panel) -> None:
    from armactl.web.page_models import players as players_page_model

    monkeypatch.setattr(players_page_model, "load_player_moderation_panel", load_panel)


def _authed_client(
    tmp_path: Path,
    monkeypatch,
    *,
    panel: PlayerModerationPanel | None = None,
    load_panel=None,
):
    from armactl.web.app import create_app

    password = "owner players password"
    setup_owner_user(tmp_path, "owner", password)
    _patch_admins_page(monkeypatch)
    if load_panel is None:
        selected_panel = panel or _panel(_reliable_player())

        def load_panel(instance: str, query: str = "") -> PlayerModerationPanel:
            return selected_panel

    _patch_player_panel(monkeypatch, load_panel)
    app = create_app(data_root=tmp_path)
    client = _client(app)
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    return client


def _admins_csrf_token(client) -> str:
    response = client.get("/admins", follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def test_player_moderation_uses_full_roster_timeout(monkeypatch):
    from armactl.web.services import player_sources

    calls: dict[str, float] = {}
    monkeypatch.setattr(
        player_sources.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )

    def query_player_view(*args, **kwargs):
        calls["timeout"] = kwargs["timeout"]
        calls["roster_timeout"] = kwargs["roster_timeout"]
        return PlayerView(
            available=True,
            current=0,
            max_players=64,
            entries=(),
            count_source="rcon",
            roster_available=True,
        )

    monkeypatch.setattr(player_sources.player_view, "query_player_view", query_player_view)

    player_sources.load_current_player_roster()

    assert calls == {"timeout": 0.35, "roster_timeout": 1.5}

def test_player_moderation_dto_uses_only_reliable_guid(monkeypatch):
    from armactl.web.page_models import players as players_page_model
    from armactl.web.services import player_sources

    monkeypatch.setattr(
        player_sources.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    monkeypatch.setattr(
        player_sources.player_view,
        "query_player_view",
        lambda *args, **kwargs: PlayerView(
            available=True,
            current=2,
            max_players=64,
            entries=(
                PlayerEntry(
                    name="Alpha token=raw-player-secret",
                    guid="21761a7f-c9b4-4bff-8375-b4b43abb95ec",
                ),
                PlayerEntry(name="Slot Only", player_id="12"),
            ),
            count_source="rcon",
            roster_available=True,
        ),
    )

    panel = players_page_model.load_player_moderation_panel()

    assert panel.available is True
    assert panel.total_count == 2
    reliable, readonly = panel.players
    assert reliable.display_name == "Alpha token=***"
    assert reliable.admin_reference == "21761a7f-c9b4-4bff-8375-b4b43abb95ec"
    assert reliable.can_add_admin is True
    assert readonly.display_name == "Slot Only"
    assert readonly.admin_reference == ""
    assert readonly.can_add_admin is False


def test_player_moderation_filter_matches_name_and_identity(monkeypatch):
    from armactl.web.page_models import players as players_page_model
    from armactl.web.services import player_sources

    monkeypatch.setattr(
        player_sources.discovery,
        "discover",
        lambda instance, save=False: _state(),
    )
    monkeypatch.setattr(
        player_sources.player_view,
        "query_player_view",
        lambda *args, **kwargs: PlayerView(
            available=True,
            current=2,
            max_players=64,
            entries=(
                PlayerEntry(name="Alpha", guid="21761a7f-c9b4-4bff-8375-b4b43abb95ec"),
                PlayerEntry(name="Bravo", guid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            ),
            count_source="rcon",
            roster_available=True,
        ),
    )

    by_name = players_page_model.load_player_moderation_panel(query="brav")
    by_id = players_page_model.load_player_moderation_panel(query="4bff")

    assert [player.display_name for player in by_name.players] == ["Bravo"]
    assert [player.display_name for player in by_id.players] == ["Alpha"]


def test_admins_page_renders_player_moderation_section(tmp_path: Path, monkeypatch):
    client = _authed_client(
        tmp_path,
        monkeypatch,
        panel=_panel(_reliable_player("<Alpha & Co>")),
    )

    response = client.get("/admins", follow_redirects=False)

    assert response.status_code == 200
    assert "Players / Moderation" in response.text
    assert "&lt;Alpha &amp; Co&gt;" in response.text
    assert "<Alpha & Co>" not in response.text
    assert "21761a7f-c9b4-4bff-8375-b4b43abb95ec" in response.text
    assert "Add as admin" in response.text
    assert "Restart the server to apply admin changes." not in response.text


def test_admins_player_search_query_is_passed_to_panel_loader(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[str] = []

    def load_panel(instance: str, query: str = "") -> PlayerModerationPanel:
        calls.append(query)
        player = _reliable_player("Bravo")
        return _panel(player, query=query)

    client = _authed_client(tmp_path, monkeypatch, load_panel=load_panel)

    response = client.get("/admins?player_search=brav", follow_redirects=False)

    assert response.status_code == 200
    assert calls[-1] == "brav"
    assert 'id="players-moderation"' in response.text
    assert 'action="/admins#players-moderation"' in response.text
    assert 'href="/admins#players-moderation"' in response.text
    assert 'value="brav"' in response.text
    assert "Bravo" in response.text


def test_admins_page_distinguishes_unavailable_player_roster(
    tmp_path: Path,
    monkeypatch,
):
    client = _authed_client(tmp_path, monkeypatch, panel=_panel(available=False))

    response = client.get("/admins", follow_redirects=False)

    assert response.status_code == 200
    assert "Current player roster is unavailable." in response.text
    assert "No players are currently online." not in response.text


def test_admins_page_distinguishes_empty_player_roster(
    tmp_path: Path,
    monkeypatch,
):
    client = _authed_client(tmp_path, monkeypatch, panel=_panel())

    response = client.get("/admins", follow_redirects=False)

    assert response.status_code == 200
    assert "No players are currently online." in response.text
    assert "Current player roster is unavailable." not in response.text


def test_admins_page_distinguishes_empty_player_search(
    tmp_path: Path,
    monkeypatch,
):
    client = _authed_client(tmp_path, monkeypatch, panel=_panel(query="missing"))

    response = client.get("/admins?player_search=missing", follow_redirects=False)

    assert response.status_code == 200
    assert "No players match search." in response.text
    assert "Current player roster is unavailable." not in response.text
    assert "No players are currently online." not in response.text


def test_add_admin_from_player_uses_existing_admin_action_flow(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import admin_actions

    config_path = tmp_path / "config.json"
    calls: list[tuple[Path, str, str]] = []
    client = _authed_client(tmp_path, monkeypatch, panel=_panel(_reliable_player()))
    monkeypatch.setattr(
        admin_actions.discovery,
        "discover",
        lambda instance, save=False: _state(config_path),
    )

    def add_admin(path: Path, admin_reference: str, name: str = "") -> bool:
        calls.append((path, admin_reference, name))
        return True

    monkeypatch.setattr(admin_actions.admins_manager, "add_admin", add_admin)
    csrf_token = _admins_csrf_token(client)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "21761a7f-c9b4-4bff-8375-b4b43abb95ec",
            "label": "Alpha",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert calls == [(config_path, "21761a7f-c9b4-4bff-8375-b4b43abb95ec", "Alpha")]
    assert "Admin added." in response.text
    assert "Restart the server to apply admin changes." in response.text
    event = _audit_events(tmp_path)[0]
    assert event["action"] == "admin.add"
    assert event["target"] == "21761a7f-c9b4-4bff-8375-b4b43abb95ec"
    assert event["success"] is True


def test_player_without_reliable_identity_has_no_add_admin_button(
    tmp_path: Path,
    monkeypatch,
):
    client = _authed_client(tmp_path, monkeypatch, panel=_panel(_readonly_player()))

    response = client.get("/admins", follow_redirects=False)

    assert response.status_code == 200
    assert "Observer" in response.text
    assert "Player identity unavailable" in response.text
    assert "Add as admin" not in response.text
    assert "Read-only" in response.text


def test_player_add_admin_requires_manage_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.services import admin_actions

    set_web_owner_permissions({ADMINS_VIEW})
    client = _authed_client(tmp_path, monkeypatch, panel=_panel(_reliable_player()))
    monkeypatch.setattr(admin_actions, "run_admin_action_and_audit", AssertionError)
    csrf_token = _admins_csrf_token(client)

    get_response = client.get("/admins", follow_redirects=False)
    post_response = client.post(
        "/admins/add",
        data={
            "csrf_token": csrf_token,
            "admin_reference": "21761a7f-c9b4-4bff-8375-b4b43abb95ec",
            "label": "Alpha",
        },
        follow_redirects=False,
    )

    assert get_response.status_code == 200
    assert "Add as admin" not in get_response.text
    assert post_response.status_code == 403
    assert post_response.text == "Permission denied."


def test_player_add_admin_requires_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.services import admin_actions

    client = _authed_client(tmp_path, monkeypatch, panel=_panel(_reliable_player()))
    monkeypatch.setattr(admin_actions, "run_admin_action_and_audit", AssertionError)

    response = client.post(
        "/admins/add",
        data={
            "csrf_token": "wrong-token",
            "admin_reference": "21761a7f-c9b4-4bff-8375-b4b43abb95ec",
            "label": "Alpha",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_player_moderation_html_does_not_expose_auth_or_raw_token_secrets(
    tmp_path: Path,
    monkeypatch,
):
    password = "owner secret players password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    from armactl.web.app import create_app

    _patch_admins_page(monkeypatch)
    _patch_player_panel(
        monkeypatch,
        lambda instance, query="": _panel(
            ModerationPlayer(
                display_name="Sneaky token=***",
                identity_id="21761a7f-c9b4-4bff-8375-b4b43abb95ec",
                admin_reference="21761a7f-c9b4-4bff-8375-b4b43abb95ec",
                source="rcon.guid",
                status="active",
                last_seen="online now",
            )
        ),
    )
    app = create_app(data_root=tmp_path)
    client = _client(app)
    login_response = _login(client, "owner", password)
    assert login_response.status_code == 303
    session_token = login_response.cookies.get(_session_cookie_name(client))

    response = client.get("/admins", follow_redirects=False)

    assert response.status_code == 200
    assert "Sneaky token=***" in response.text
    assert "raw-player-secret" not in response.text
    assert password not in response.text
    assert user.password_hash not in response.text
    assert session_token
    assert session_token not in response.text
