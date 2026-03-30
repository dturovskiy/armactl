"""Minimal BattlEye RCON helpers used for player roster status."""

from __future__ import annotations

import re
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
BE_PACKET_TYPES = {BE_LOGIN, BE_COMMAND, BE_SERVER_MESSAGE}


RCON_NOISE_PREFIXES = (
    "logged in! client id:",
    "processing command:",
)

PLAYER_SLOT_SUFFIX_RE = re.compile(r"\s*\(#(?P<player_id>\d+)\)\s*$")
GUID_LIKE_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")


class RconError(Exception):
    """Raised when a BattlEye RCON action fails."""


@dataclass
class PlayerEntry:
    """One best-effort player entry from a roster query."""

    name: str
    player_id: str | None = None
    guid: str | None = None
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
        trimmed_payload = payload.rstrip(b"\x00")
        if trimmed_payload != payload:
            trimmed_checksum = zlib.crc32(trimmed_payload) & 0xFFFFFFFF
            if trimmed_checksum == expected_checksum:
                return trimmed_payload
        if trimmed_payload and trimmed_payload[0] in BE_PACKET_TYPES:
            return trimmed_payload
        raise RconError("Invalid BattlEye packet checksum.")
    return payload


def _parse_reforger_player_line(line: str) -> PlayerEntry | None:
    """Parse Arma Reforger semicolon-delimited roster lines when possible."""
    if ";" not in line:
        return None

    normalized = line.strip()
    parts = [part.strip() for part in normalized.split(";") if part.strip()]
    if len(parts) < 2:
        return None

    guid_index: int | None = None
    guid: str | None = None
    for index, part in enumerate(parts):
        candidate = part.lstrip("#").strip()
        if GUID_LIKE_RE.fullmatch(candidate):
            guid_index = index
            guid = candidate
            break

    if guid_index is None or guid is None:
        return None

    trailing_parts = [part.strip() for part in parts[guid_index + 1 :] if part.strip()]
    if not trailing_parts:
        return None

    tail = trailing_parts[-1]
    player_id = None
    slot_match = PLAYER_SLOT_SUFFIX_RE.search(tail)
    if slot_match:
        player_id = slot_match.group("player_id")
        tail = PLAYER_SLOT_SUFFIX_RE.sub("", tail).strip()

    if not tail:
        return None

    return PlayerEntry(
        name=tail,
        player_id=player_id,
        guid=guid,
        raw=line,
    )


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
        server_messages: list[str] = []

        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.05)
            try:
                payload = self._recv_payload(timeout=remaining)
            except TimeoutError:
                break
            if not payload:
                continue

            if payload[0] == BE_SERVER_MESSAGE and len(payload) >= 2:
                self._ack_server_message(payload[1])
                message_text = payload[2:].decode("utf-8", errors="replace").strip()
                if message_text:
                    server_messages.append(message_text)
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
            if server_messages:
                return "\n".join(server_messages).strip()
            raise RconError("RCON command timed out.")

        if expected_parts is None:
            expected_parts = len(parts) or 1

        command_text = (
            b"".join(parts.get(index, b"") for index in range(expected_parts))
            .decode("utf-8", errors="replace")
            .strip()
        )
        server_text = "\n".join(server_messages).strip()
        if server_text and not command_text:
            return server_text
        if server_text and command_text and server_text not in command_text:
            return f"{command_text}\n{server_text}".strip()
        return command_text


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

        if lowered.startswith(RCON_NOISE_PREFIXES):
            continue

        if line.startswith("#"):
            continue

        reforger_entry = _parse_reforger_player_line(line)
        if reforger_entry is not None:
            entries.append(reforger_entry)
            continue

        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            entries.append(
                PlayerEntry(
                    name=parts[1].strip(),
                    player_id=parts[0],
                    raw=line,
                )
            )
            continue

        entries.append(PlayerEntry(name=line, raw=line))

    return entries



def _query_player_entries(session: _RconSession) -> list[PlayerEntry]:
    """Try the most likely roster commands and return the first non-empty parse."""
    last_error: str = ""
    for command in ("#players", "players"):
        try:
            response = session.send_command(command)
        except RconError as error:
            last_error = str(error)
            continue

        entries = _parse_player_lines(response)
        if entries:
            return entries
        if response:
            last_error = response

    if last_error:
        raise RconError(last_error)
    return []


def query_player_roster(instance: str, timeout: float = 5.0) -> PlayerRoster:
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
        entries = _query_player_entries(session)
        return PlayerRoster(True, True, host, port, entries=entries)
    except (OSError, RconError) as error:
        return PlayerRoster(False, True, host, port, error=str(error))
    finally:
        session.logout()
        session.close()
