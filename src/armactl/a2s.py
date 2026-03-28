"""Minimal A2S helpers for querying local Arma Reforger server status."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

from armactl.config_manager import ConfigError, load_config
from armactl.discovery import discover
from armactl.state import ServerState

A2S_HEADER = b"\xFF\xFF\xFF\xFF"
A2S_INFO_REQUEST = A2S_HEADER + b"TSource Engine Query\x00"
A2S_CHALLENGE_RESPONSE = 0x41
A2S_INFO_RESPONSE = 0x49


@dataclass
class A2SInfo:
    """Subset of A2S server info used by armactl."""

    host: str
    port: int
    server_name: str
    map_name: str
    player_count: int
    max_players: int


@dataclass
class PlayerStatus:
    """Best-effort player count snapshot for Telegram and future UIs."""

    available: bool
    host: str
    port: int | None
    player_count: int | None = None
    max_players: int | None = None
    error: str = ""


def _read_cstring(payload: bytes, offset: int) -> tuple[str, int]:
    """Read a null-terminated UTF-8 string from a byte payload."""
    end = payload.find(b"\x00", offset)
    if end == -1:
        raise ValueError("A2S payload is missing a null-terminated string.")
    return payload[offset:end].decode("utf-8", errors="replace"), end + 1


def _extract_a2s_host(config: dict[str, Any]) -> str:
    """Choose a local query host from config, defaulting to loopback."""
    a2s = config.get("a2s", {})
    if isinstance(a2s, dict):
        address = str(a2s.get("address", "")).strip()
        if address and address not in {"0.0.0.0", "::"}:
            return address

    for key in ("bindAddress", "publicAddress"):
        value = str(config.get(key, "")).strip()
        if value and value not in {"0.0.0.0", "::", "local"}:
            return value

    return "127.0.0.1"


def _extract_a2s_port(config: dict[str, Any], fallback_port: int = 17777) -> int:
    """Return the configured A2S port or the standard default."""
    a2s = config.get("a2s", {})
    if isinstance(a2s, dict):
        port = a2s.get("port")
        if isinstance(port, int) and port > 0:
            return port
    return fallback_port


def _extract_config_max_players(config: dict[str, Any]) -> int | None:
    """Return configured max players when available."""
    game = config.get("game", {})
    if isinstance(game, dict):
        value = game.get("maxPlayers")
        if isinstance(value, int):
            return value
    return None


def parse_a2s_info_response(payload: bytes, host: str, port: int) -> A2SInfo:
    """Parse an A2S_INFO response packet."""
    if not payload.startswith(A2S_HEADER + bytes([A2S_INFO_RESPONSE])):
        raise ValueError("Invalid A2S_INFO response header.")

    offset = len(A2S_HEADER) + 1
    if len(payload) <= offset:
        raise ValueError("A2S_INFO response is truncated.")

    offset += 1  # protocol byte
    server_name, offset = _read_cstring(payload, offset)
    map_name, offset = _read_cstring(payload, offset)
    _, offset = _read_cstring(payload, offset)  # folder
    _, offset = _read_cstring(payload, offset)  # game

    if len(payload) < offset + 4:
        raise ValueError("A2S_INFO response is truncated before player counts.")

    offset += 2  # app id
    player_count = payload[offset]
    max_players = payload[offset + 1]

    return A2SInfo(
        host=host,
        port=port,
        server_name=server_name,
        map_name=map_name,
        player_count=player_count,
        max_players=max_players,
    )


def query_a2s_info(host: str, port: int, timeout: float = 1.5) -> A2SInfo:
    """Query Steam A2S_INFO from the configured host/port."""
    address = (host, port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(A2S_INFO_REQUEST, address)
        payload, _ = sock.recvfrom(4096)

        if payload.startswith(A2S_HEADER + bytes([A2S_CHALLENGE_RESPONSE])):
            challenge = payload[len(A2S_HEADER) + 1 : len(A2S_HEADER) + 5]
            if len(challenge) != 4:
                raise ValueError("A2S challenge response is truncated.")
            sock.sendto(A2S_INFO_REQUEST + challenge, address)
            payload, _ = sock.recvfrom(4096)

    return parse_a2s_info_response(payload, host, port)


def query_player_status(
    instance: str,
    timeout: float = 1.5,
    state: ServerState | None = None,
) -> PlayerStatus:
    """Return a best-effort player count snapshot for an armactl instance."""
    state = state or discover(instance, save=False)
    host = "127.0.0.1"
    port = state.ports.a2s or 17777
    max_players: int | None = None

    if state.config_exists and state.config_path:
        try:
            config = load_config(state.config_path)
            host = _extract_a2s_host(config)
            port = _extract_a2s_port(config, port)
            max_players = _extract_config_max_players(config)
        except ConfigError:
            pass

    if not state.server_running:
        return PlayerStatus(
            available=True,
            host=host,
            port=port,
            player_count=0,
            max_players=max_players,
        )

    try:
        info = query_a2s_info(host, port, timeout=timeout)
    except (OSError, ValueError) as error:
        return PlayerStatus(
            available=False,
            host=host,
            port=port,
            max_players=max_players,
            error=str(error),
        )

    return PlayerStatus(
        available=True,
        host=host,
        port=port,
        player_count=info.player_count,
        max_players=info.max_players or max_players,
    )
