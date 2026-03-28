"""Tests for local A2S querying helpers."""

from __future__ import annotations

from unittest.mock import patch

from armactl.a2s import (
    PlayerStatus,
    parse_a2s_info_response,
    query_player_status,
)
from armactl.state import PortInfo, ServerState


def _sample_a2s_info_packet() -> bytes:
    return (
        b"\xFF\xFF\xFF\xFFI"
        b"\x11"
        b"Test Server\x00"
        b"Everon\x00"
        b"ArmaReforger\x00"
        b"Arma Reforger\x00"
        b"\x34\x12"
        b"\x03"
        b"\x40"
        b"\x00"
        b"d"
        b"l"
        b"\x00"
        b"\x01"
        b"1.0.0\x00"
    )


def test_parse_a2s_info_response_extracts_player_counts() -> None:
    info = parse_a2s_info_response(_sample_a2s_info_packet(), "127.0.0.1", 17777)

    assert info.host == "127.0.0.1"
    assert info.port == 17777
    assert info.server_name == "Test Server"
    assert info.map_name == "Everon"
    assert info.player_count == 3
    assert info.max_players == 64


def test_query_player_status_returns_zero_when_server_is_stopped() -> None:
    state = ServerState(
        server_running=False,
        config_exists=False,
        ports=PortInfo(a2s=17777),
    )

    with patch("armactl.a2s.discover", return_value=state):
        status = query_player_status("default")

    assert status == PlayerStatus(
        available=True,
        host="127.0.0.1",
        port=17777,
        player_count=0,
        max_players=None,
    )


def test_query_player_status_uses_a2s_info_for_running_server() -> None:
    state = ServerState(
        server_running=True,
        config_exists=False,
        ports=PortInfo(a2s=17777),
    )

    with (
        patch("armactl.a2s.discover", return_value=state),
        patch(
            "armactl.a2s.query_a2s_info",
            return_value=parse_a2s_info_response(
                _sample_a2s_info_packet(),
                "127.0.0.1",
                17777,
            ),
        ),
    ):
        status = query_player_status("default")

    assert status.available is True
    assert status.player_count == 3
    assert status.max_players == 64


def test_query_player_status_returns_unavailable_on_query_error() -> None:
    state = ServerState(
        server_running=True,
        config_exists=False,
        ports=PortInfo(a2s=17777),
    )

    with (
        patch("armactl.a2s.discover", return_value=state),
        patch("armactl.a2s.query_a2s_info", side_effect=OSError("timed out")),
    ):
        status = query_player_status("default")

    assert status.available is False
    assert status.player_count is None
    assert status.max_players is None
    assert status.error == "timed out"
