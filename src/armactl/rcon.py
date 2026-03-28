"""Minimal BattlEye RCON helpers used for player roster status."""

from __future__ import annotations

import socket
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

from armactl.config_manager import ConfigError, load_config
from armactl.discovery import discover

BE_PREFIX = b"BE"
BE_PACKET_TERMINATOR = 0xFF
BE_LOGIN = 0x00
BE_COMMAND = 0x01
BE_SERVER_MESSAGE = 0x02


class RconError(Exception):
    """Raised when a BattlEye RCON action fails."""


@dataclass
class PlayerEntry:
    """One best-effort player entry from a roster query."""

    name: str
    player_id: str | None = None
    raw: str = ""


@dataclass
class PlayerRoster:
    """Result of a player roster query."""

    available: bool
    configured: bool
    host: str
    port: int | None
    entries: list[PlayerEntry] = field(default_factory=list)
    error: str = ""


def _extract_rcon_host(config: dict[str, Any]) -> str:
    rcon = config.get("rcon", {})
    if isinstance(rcon, dict):
        address = str(rcon.get("address", "")).strip()
        if address and address not in {"0.0.0.0", "::"}:
            return address

    bind_address = str(config.get("bindAddress", "")).strip()
    if bind_address and bind_address not in {"0.0.0.0", "::", "local"}:
        return bind_address

    return "127.0.0.1"


def _extract_rcon_port(config: dict[str, Any]) -> int:
    rcon = config.get("rcon", {})
    if isinstance(rcon, dict):
        port = rcon.get("port")
        if isinstance(port, int) and port > 0:
            return port
    return 19999


def _extract_rcon_password(config: dict[str, Any]) -> str:
    rcon = config.get("rcon", {})
    if isinstance(rcon, dict):
        password = str(rcon.get("password", "")).strip()
        if password:
            return password
    return ""


def _build_packet(payload: bytes) -> bytes:
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    return (
        BE_PREFIX
        + checksum.to_bytes(4, "little")
        + bytes([BE_PACKET_TERMINATOR])
        + payload
    )


def _parse_packet(data: bytes) -> bytes:
    if len(data) < 8 or data[:2] != BE_PREFIX or data[6] != BE_PACKET_TERMINATOR:
        raise RconError("Invalid BattlEye packet header.")

    expected_checksum = int.from_bytes(data[2:6], "little")
    payload = data[7:]
    actual_checksum = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_checksum != expected_checksum:
        raise RconError("Invalid BattlEye packet checksum.")
    return payload


class _RconSession:
    """Small one-shot BattlEye RCON session."""

    def __init__(self, host: str, port: int, password: str, timeout: float):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sequence = 0
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(timeout)

    def close(self) -> None:
        self.socket.close()

    def _send_payload(self, payload: bytes) -> None:
        self.socket.sendto(_build_packet(payload), (self.host, self.port))

    def _recv_payload(self, timeout: float | None = None) -> bytes:
        if timeout is not None:
            self.socket.settimeout(timeout)
        data, _ = self.socket.recvfrom(65535)
        return _parse_packet(data)

    def login(self) -> None:
        self._send_payload(bytes([BE_LOGIN]) + self.password.encode("ascii"))
        payload = self._recv_payload()
        if len(payload) < 2 or payload[0] != BE_LOGIN or payload[1] != 0x01:
            raise RconError("RCON login failed.")

    def _ack_server_message(self, sequence_number: int) -> None:
        self._send_payload(bytes([BE_SERVER_MESSAGE, sequence_number]))

    def send_command(self, command: str) -> str:
        sequence_number = self.sequence
        self.sequence = (self.sequence + 1) % 256
        self._send_payload(
            bytes([BE_COMMAND, sequence_number]) + command.encode("ascii")
        )

        deadline = time.monotonic() + self.timeout
        parts: dict[int, bytes] = {}
        expected_parts: int | None = None

        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.05)
            payload = self._recv_payload(timeout=remaining)
            if not payload:
                continue

            if payload[0] == BE_SERVER_MESSAGE and len(payload) >= 2:
                self._ack_server_message(payload[1])
                continue

            if payload[0] != BE_COMMAND or len(payload) < 2 or payload[1] != sequence_number:
                continue

            response = payload[2:]
            if len(response) >= 3 and response[0] == 0x00:
                expected_parts = response[1]
                parts[response[2]] = response[3:]
                if expected_parts and len(parts) >= expected_parts:
                    break
                continue

            parts[0] = response
            expected_parts = 1
            break

        if expected_parts is None and not parts:
            raise RconError("RCON command timed out.")

        if expected_parts is None:
            expected_parts = len(parts) or 1

        return (
            b"".join(parts.get(index, b"") for index in range(expected_parts))
            .decode("utf-8", errors="replace")
            .strip()
        )

    def logout(self) -> None:
        try:
            self.send_command("@logout")
        except Exception:
            pass


def _parse_player_lines(response: str) -> list[PlayerEntry]:
    entries: list[PlayerEntry] = []
    for raw_line in response.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lowered = line.lower()
        if lowered in {"players on server:", "players on server"}:
            continue

        if lowered.startswith("players") and ":" in line:
            continue

        if line.startswith("#"):
            continue

        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            entries.append(PlayerEntry(name=parts[1].strip(), player_id=parts[0], raw=line))
            continue

        entries.append(PlayerEntry(name=line, raw=line))
    return entries


def query_player_roster(instance: str, timeout: float = 2.0) -> PlayerRoster:
    """Return a best-effort player roster using configured local RCON."""
    state = discover(instance, save=False)
    host = "127.0.0.1"
    port = state.ports.rcon or 19999
    password = ""

    if state.config_exists and state.config_path:
        try:
            config = load_config(state.config_path)
            host = _extract_rcon_host(config)
            port = _extract_rcon_port(config)
            password = _extract_rcon_password(config)
        except ConfigError as error:
            return PlayerRoster(False, False, host, port, error=str(error))

    if not password:
        return PlayerRoster(
            available=False,
            configured=False,
            host=host,
            port=port,
            error="RCON password is not configured.",
        )

    if not state.server_running:
        return PlayerRoster(True, True, host, port, entries=[])

    session = _RconSession(host, port, password, timeout)
    try:
        session.login()
        response = session.send_command("#players")
        entries = _parse_player_lines(response)
        if not entries and response:
            fallback_response = session.send_command("players")
            fallback_entries = _parse_player_lines(fallback_response)
            if fallback_entries:
                entries = fallback_entries
        return PlayerRoster(True, True, host, port, entries=entries)
    except (OSError, RconError) as error:
        return PlayerRoster(False, True, host, port, error=str(error))
    finally:
        session.logout()
        session.close()
