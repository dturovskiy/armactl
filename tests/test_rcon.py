"""Tests for BattlEye RCON helpers."""

from __future__ import annotations

import zlib
from unittest.mock import patch

import armactl.rcon as rcon
from armactl.state import PortInfo, ServerState


class _FakeSocket:
    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendto(self, payload: bytes, address) -> None:
        self.payload = payload
        self.address = address

    def close(self) -> None:
        pass


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


def test_parse_player_lines_ignores_battleye_noise():
    response = """
Logged In! Client ID: #0
Processing Command: #players
; 0109fcf5-a861-4002-881e-8a497c59797c ; MisanTropiC#DivisioN (#1)
""".strip()

    entries = rcon._parse_player_lines(response)

    assert len(entries) == 1
    assert entries[0].name == "MisanTropiC#DivisioN"
    assert entries[0].player_id == "1"
    assert entries[0].guid == "0109fcf5-a861-4002-881e-8a497c59797c"


def test_parse_player_lines_supports_legacy_numeric_format():
    response = "17 Denis"

    entries = rcon._parse_player_lines(response)

    assert entries == [
        rcon.PlayerEntry(name="Denis", player_id="17", raw="17 Denis")
    ]


def test_parse_player_lines_ignores_players_header_lines():
    response = """
Players on server:
Players: 1
""".strip()

    entries = rcon._parse_player_lines(response)

    assert entries == []


def test_parse_player_lines_keeps_unknown_nonempty_lines_as_fallback():
    response = "Some Unexpected Line"

    entries = rcon._parse_player_lines(response)

    assert len(entries) == 1
    assert entries[0].name == "Some Unexpected Line"
    assert entries[0].player_id is None
    assert entries[0].guid is None


def test_parse_player_lines_handles_incomplete_reforger_output_without_slot():
    response = "; 0109fcf5-a861-4002-881e-8a497c59797c ; Name Without Slot"

    entries = rcon._parse_player_lines(response)

    assert len(entries) == 1
    assert entries[0].name == "Name Without Slot"
    assert entries[0].player_id is None
    assert entries[0].guid == "0109fcf5-a861-4002-881e-8a497c59797c"


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


def test_send_command_uses_server_messages_when_command_packets_are_empty() -> None:
    with patch("armactl.rcon.socket.socket", return_value=_FakeSocket()):
        session = rcon._RconSession("127.0.0.1", 19999, "secret", timeout=1.0)

    with patch.object(
        session,
        "_recv_payload",
        side_effect=[
            bytes([rcon.BE_SERVER_MESSAGE, 7]) + b"17 Denis\n18 Vova",
            TimeoutError(),
        ],
    ):
        response = session.send_command("#players")

    assert response == "17 Denis\n18 Vova"


def test_query_player_roster_falls_back_to_plain_players_command() -> None:
    state = ServerState(
        server_running=True,
        config_exists=True,
        config_path="/tmp/config.json",
        ports=PortInfo(rcon=19999),
    )
    config = {"rcon": {"address": "127.0.0.1", "port": 19999, "password": "secret"}}

    session = type(
        "FakeSession",
        (),
        {
            "login": lambda self: None,
            "send_command": lambda self, command: (
                (_ for _ in ()).throw(rcon.RconError("RCON command timed out."))
                if command == "#players"
                else "17 Denis\n18 Vova"
            ),
            "logout": lambda self: None,
            "close": lambda self: None,
        },
    )()

    with (
        patch("armactl.rcon.discover", return_value=state),
        patch("armactl.rcon.load_config", return_value=config),
        patch("armactl.rcon._RconSession", return_value=session),
    ):
        roster = rcon.query_player_roster("default")

    assert roster.available is True
    assert [entry.name for entry in roster.entries] == ["Denis", "Vova"]
