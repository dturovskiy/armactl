"""Tests for BattlEye RCON helpers."""

from __future__ import annotations

from unittest.mock import patch

from armactl.rcon import (
    PlayerEntry,
    _build_packet,
    _parse_packet,
    _parse_player_lines,
    query_player_roster,
)
from armactl.state import PortInfo, ServerState


def test_build_and_parse_packet_roundtrip() -> None:
    payload = b"\x01\x00#players"
    packet = _build_packet(payload)

    assert _parse_packet(packet) == payload


def test_parse_player_lines_extracts_ids_when_possible() -> None:
    response = "Players on server:\n17 Denis\n18 Vova\nObserver"

    entries = _parse_player_lines(response)

    assert entries == [
        PlayerEntry(name="Denis", player_id="17", raw="17 Denis"),
        PlayerEntry(name="Vova", player_id="18", raw="18 Vova"),
        PlayerEntry(name="Observer", player_id=None, raw="Observer"),
    ]


def test_query_player_roster_reports_missing_password() -> None:
    state = ServerState(
        server_running=True,
        config_exists=False,
        ports=PortInfo(rcon=19999),
    )

    with patch("armactl.rcon.discover", return_value=state):
        roster = query_player_roster("default")

    assert roster.available is False
    assert roster.configured is False
    assert roster.error == "RCON password is not configured."


def test_query_player_roster_returns_empty_entries_when_server_is_stopped() -> None:
    state = ServerState(
        server_running=False,
        config_exists=True,
        config_path="/tmp/config.json",
        ports=PortInfo(rcon=19999),
    )
    config = {"rcon": {"address": "127.0.0.1", "port": 19999, "password": "secret"}}

    with (
        patch("armactl.rcon.discover", return_value=state),
        patch("armactl.rcon.load_config", return_value=config),
    ):
        roster = query_player_roster("default")

    assert roster.available is True
    assert roster.configured is True
    assert roster.entries == []
