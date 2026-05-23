"""Tests for unified player view source selection."""

from __future__ import annotations

from unittest.mock import patch

from armactl.a2s import PlayerStatus
from armactl.player_view import query_player_view
from armactl.rcon import PlayerEntry, PlayerRoster
from armactl.state import PortInfo, ServerState


def _running_state() -> ServerState:
    return ServerState(server_running=True, ports=PortInfo(a2s=17777, rcon=19999))


def test_player_view_prefers_available_rcon_roster_count_over_a2s_ghost_count() -> None:
    with (
        patch(
            "armactl.player_view.query_player_status",
            return_value=PlayerStatus(
                available=True,
                host="127.0.0.1",
                port=17777,
                player_count=2,
                max_players=128,
            ),
        ),
        patch(
            "armactl.player_view.query_player_roster",
            return_value=PlayerRoster(
                available=True,
                configured=True,
                host="127.0.0.1",
                port=19999,
                entries=[PlayerEntry(name="deus", player_id="1")],
            ),
        ),
    ):
        view = query_player_view("default", state=_running_state(), include_roster=True)

    assert view.current == 1
    assert view.max_players == 128
    assert view.count_source == "rcon"
    assert view.player_lines == ["deus"]
    assert view.a2s_count == 2
    assert view.warning == "A2S reports 2 player(s), but RCON roster has 1."


def test_player_view_uses_empty_available_rcon_roster_as_zero_players() -> None:
    with (
        patch(
            "armactl.player_view.query_player_status",
            return_value=PlayerStatus(
                available=True,
                host="127.0.0.1",
                port=17777,
                player_count=2,
                max_players=128,
            ),
        ),
        patch(
            "armactl.player_view.query_player_roster",
            return_value=PlayerRoster(
                available=True,
                configured=True,
                host="127.0.0.1",
                port=19999,
                entries=[],
            ),
        ),
    ):
        view = query_player_view("default", state=_running_state(), include_roster=True)

    assert view.current == 0
    assert view.count_source == "rcon"
    assert view.player_lines == []
    assert view.warning == "A2S reports 2 player(s), but RCON roster has 0."


def test_player_view_falls_back_to_a2s_when_rcon_is_unavailable() -> None:
    with (
        patch(
            "armactl.player_view.query_player_status",
            return_value=PlayerStatus(
                available=True,
                host="127.0.0.1",
                port=17777,
                player_count=2,
                max_players=128,
            ),
        ),
        patch(
            "armactl.player_view.query_player_roster",
            return_value=PlayerRoster(
                available=False,
                configured=True,
                host="127.0.0.1",
                port=19999,
                error="RCON command timed out.",
            ),
        ),
    ):
        view = query_player_view("default", state=_running_state(), include_roster=True)

    assert view.current == 2
    assert view.count_source == "a2s"
    assert view.roster_error == "RCON command timed out."
