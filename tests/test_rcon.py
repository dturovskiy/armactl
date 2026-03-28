"""Tests for BattlEye RCON helpers."""

from __future__ import annotations

import zlib
from unittest.mock import patch

import armactl.rcon as rcon
from armactl.state import PortInfo, ServerState


def test_build_and_parse_packet_roundtrip() -> None:
    payload = b"\x01\x00#players"
    packet = rcon._build_packet(payload)

    assert rcon._parse_packet(packet) == payload


def test_parse_packet_tolerates_reforger_checksum_mismatch_on_rcon_payload() -> None:
    payload = b"\x01\x00Denis\nVova\x00"
    wrong_checksum = (zlib.crc32(payload.rstrip(b"\x00")) + 1) & 0xFFFFFFFF
    packet = (
        b"BE"
        + wrong_checksum.to_bytes(4, "little")
        + bytes([rcon.BE_PACKET_TERMINATOR])
        + payload
    )

    assert rcon._parse_packet(packet) == payload.rstrip(b"\x00")


def test_parse_player_lines_extracts_ids_when_possible() -> None:
    response = "Players on server:\n17 Denis\n18 Vova\nObserver"

    entries = rcon._parse_player_lines(response)

    assert entries == [
        rcon.PlayerEntry(name="Denis", player_id="17", raw="17 Denis"),
        rcon.PlayerEntry(name="Vova", player_id="18", raw="18 Vova"),
        rcon.PlayerEntry(name="Observer", player_id=None, raw="Observer"),
    ]


def test_query_player_roster_reports_missing_password() -> None:
    state = ServerState(
        server_running=True,
        config_exists=False,
        ports=PortInfo(rcon=19999),
    )

    with patch("armactl.rcon.discover", return_value=state):
        roster = rcon.query_player_roster("default")

    assert roster.available is False
    assert roster.configured is False
    assert roster.error == "RCON password is not configured."


def test_extract_rcon_host_prefers_local_bind_over_public_address() -> None:
    assert (
        rcon._extract_rcon_host(
            {
                "bindAddress": "10.0.0.25",
                "publicAddress": "203.0.113.55",
                "rcon": {"port": 19999},
            }
        )
        == "10.0.0.25"
    )
    assert (
        rcon._extract_rcon_host(
            {
                "publicAddress": "203.0.113.55",
                "rcon": {"port": 19999},
            }
        )
        == "127.0.0.1"
    )


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
        roster = rcon.query_player_roster("default")

    assert roster.available is True
    assert roster.configured is True
    assert roster.entries == []
