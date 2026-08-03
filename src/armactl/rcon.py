"""Minimal BattlEye RCON helpers used for player roster and native ban status."""

from __future__ import annotations

import math
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
RCON_ROSTER_TIMEOUT_SECONDS = 1.5
RCON_NATIVE_BAN_TIMEOUT_SECONDS = 1.5
RCON_NATIVE_BAN_MIN_TIMEOUT_SECONDS = 0.1
RCON_NATIVE_BAN_MAX_TIMEOUT_SECONDS = 5.0
NATIVE_BAN_MIN_PAGE = 1
NATIVE_BAN_MAX_PAGE = 100
NATIVE_BAN_PAGE_SIZE = 25
NATIVE_BAN_MAX_DURATION_SECONDS = 2_147_483_647

NATIVE_BAN_STATUS_COMPLETE = "complete"
NATIVE_BAN_STATUS_PARTIAL = "partial"
NATIVE_BAN_STATUS_UNAVAILABLE = "unavailable"

NATIVE_BAN_ERROR_NOT_CONFIGURED = "not_configured"
NATIVE_BAN_ERROR_SERVER_UNAVAILABLE = "server_unavailable"
NATIVE_BAN_ERROR_TIMEOUT = "timeout"
NATIVE_BAN_ERROR_PERMISSION_DENIED = "permission_denied"
NATIVE_BAN_ERROR_MALFORMED_RESPONSE = "malformed_response"
NATIVE_BAN_ERROR_RCON_UNAVAILABLE = "rcon_unavailable"
NATIVE_BAN_ERROR_CONFIG_UNAVAILABLE = "config_unavailable"
NATIVE_BAN_ERROR_COMMAND_UNAVAILABLE = "command_unavailable"

PLAYER_SLOT_SUFFIX_RE = re.compile(r"\s*\(#(?P<player_id>\d+)\)\s*$")
GUID_LIKE_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")
HASH_PLAYER_PREFIX_RE = re.compile(r"^#(?P<player_id>\d+)\s+(?P<name>.+?)\s*$")
NATIVE_BAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NATIVE_BAN_PLAYER_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")


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


@dataclass(frozen=True)
class NativeBanEntry:
    """One fixture-proven native Reforger ban-list row."""

    native_ban_id: str
    player_uid: str
    duration_seconds: int


@dataclass(frozen=True)
class NativeBanListResult:
    """Controlled result for one bounded native Reforger ban-list page."""

    requested_page: int
    available: bool
    complete: bool
    status: str
    entries: tuple[NativeBanEntry, ...] = ()
    error_code: str = ""
    error: str = ""
    has_previous: bool = False
    has_next: bool = False


def normalize_native_ban_page(page: int) -> int:
    """Validate one native list page without silently changing caller intent."""
    if isinstance(page, bool) or not isinstance(page, int):
        raise ValueError("Native ban-list page must be an integer.")
    if not NATIVE_BAN_MIN_PAGE <= page <= NATIVE_BAN_MAX_PAGE:
        raise ValueError(
            f"Native ban-list page must be between "
            f"{NATIVE_BAN_MIN_PAGE} and {NATIVE_BAN_MAX_PAGE}."
        )
    return page


def _native_ban_result(
    page: int,
    *,
    status: str,
    entries: tuple[NativeBanEntry, ...] = (),
    error_code: str = "",
    error: str = "",
) -> NativeBanListResult:
    complete = status == NATIVE_BAN_STATUS_COMPLETE
    return NativeBanListResult(
        requested_page=page,
        available=status != NATIVE_BAN_STATUS_UNAVAILABLE,
        complete=complete,
        status=status,
        entries=entries,
        error_code=error_code,
        error=error,
        has_previous=page > NATIVE_BAN_MIN_PAGE,
        has_next=(
            complete
            and len(entries) == NATIVE_BAN_PAGE_SIZE
            and page < NATIVE_BAN_MAX_PAGE
        ),
    )


def _native_ban_unavailable(
    page: int,
    error_code: str,
    error: str,
) -> NativeBanListResult:
    return _native_ban_result(
        page,
        status=NATIVE_BAN_STATUS_UNAVAILABLE,
        error_code=error_code,
        error=error,
    )


def _is_native_ban_header(line: str) -> bool:
    normalized = re.sub(r"[\[\]\s]", "", line).casefold()
    if normalized.startswith("bans:"):
        normalized = normalized.removeprefix("bans:")
    return normalized == "banid;playeruid;duration"


def _parse_native_ban_row(line: str) -> NativeBanEntry | None:
    parts = [part.strip() for part in line.split(";")]
    if len(parts) != 3:
        return None

    native_ban_id = parts[0].removeprefix("#").strip()
    player_uid = parts[1]
    duration_text = parts[2]
    if (
        NATIVE_BAN_ID_RE.fullmatch(native_ban_id) is None
        or NATIVE_BAN_PLAYER_UID_RE.fullmatch(player_uid) is None
        or not duration_text.isascii()
        or not duration_text.isdigit()
    ):
        return None

    duration_seconds = int(duration_text)
    if duration_seconds > NATIVE_BAN_MAX_DURATION_SECONDS:
        return None
    return NativeBanEntry(
        native_ban_id=native_ban_id,
        player_uid=player_uid,
        duration_seconds=duration_seconds,
    )


def _parse_native_ban_list_response(
    response: str,
    *,
    requested_page: int,
) -> NativeBanListResult:
    """Parse only the documented native three-column ban-list shape."""
    page = normalize_native_ban_page(requested_page)
    rows_by_id: dict[str, NativeBanEntry] = {}
    saw_header = False
    saw_malformed = False

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.casefold()
        if lowered.startswith(RCON_NOISE_PREFIXES):
            continue
        if any(
            marker in lowered
            for marker in (
                "permission denied",
                "not permitted",
                "not allowed",
                "insufficient permission",
                "access denied",
            )
        ):
            return _native_ban_unavailable(
                page,
                NATIVE_BAN_ERROR_PERMISSION_DENIED,
                "RCON permission does not allow reading the native ban list.",
            )
        if any(
            marker in lowered
            for marker in ("unknown command", "command not found", "unsupported command")
        ):
            return _native_ban_unavailable(
                page,
                NATIVE_BAN_ERROR_COMMAND_UNAVAILABLE,
                "Native ban-list command is unavailable.",
            )
        if _is_native_ban_header(line):
            saw_header = True
            continue

        entry = _parse_native_ban_row(line)
        if entry is None:
            saw_malformed = True
            continue
        previous = rows_by_id.get(entry.native_ban_id)
        if previous is not None:
            saw_malformed = True
            continue
        rows_by_id[entry.native_ban_id] = entry

    entries = tuple(rows_by_id.values())
    if len(entries) > NATIVE_BAN_PAGE_SIZE:
        entries = entries[:NATIVE_BAN_PAGE_SIZE]
        saw_malformed = True

    if saw_malformed and entries:
        return _native_ban_result(
            page,
            status=NATIVE_BAN_STATUS_PARTIAL,
            entries=entries,
            error_code=NATIVE_BAN_ERROR_MALFORMED_RESPONSE,
            error="Native ban page contained unrecognized or incomplete content.",
        )
    if saw_malformed:
        return _native_ban_unavailable(
            page,
            NATIVE_BAN_ERROR_MALFORMED_RESPONSE,
            "Native ban page response could not be verified.",
        )
    if entries or saw_header:
        return _native_ban_result(
            page,
            status=NATIVE_BAN_STATUS_COMPLETE,
            entries=entries,
        )
    return _native_ban_unavailable(
        page,
        NATIVE_BAN_ERROR_MALFORMED_RESPONSE,
        "Native ban page response could not be verified.",
    )


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
    player_id: str | None = None
    for index, part in enumerate(parts):
        candidate = part.lstrip("#").strip()
        if GUID_LIKE_RE.fullmatch(candidate):
            guid_index = index
            guid = candidate
            break

    if guid_index is None or guid is None:
        return None

    for part in parts[:guid_index]:
        candidate = part.lstrip("#").strip()
        if candidate.isdigit():
            player_id = candidate
            break

    trailing_parts = [part.strip() for part in parts[guid_index + 1 :] if part.strip()]
    if not trailing_parts:
        return None

    tail = trailing_parts[-1]
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


def _player_entry_dedupe_key(entry: PlayerEntry) -> tuple[str, str]:
    guid = (entry.guid or "").strip().lower()
    if guid:
        return ("guid", guid)

    player_id = (entry.player_id or "").strip()
    if player_id:
        return ("player_id", player_id)

    normalized_name = " ".join(entry.name.strip().lower().split())
    return ("name", normalized_name)


def _dedupe_player_entries(entries: list[PlayerEntry]) -> list[PlayerEntry]:
    unique: list[PlayerEntry] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = _player_entry_dedupe_key(entry)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


class _RconSession:
    """Small one-shot BattlEye RCON session."""

    def __init__(self, host: str, port: int, password: str, timeout: float):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sequence = 0
        self.last_response_complete = True
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
        self.last_response_complete = True
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
        if expected_parts and not all(index in parts for index in range(expected_parts)):
            self.last_response_complete = False

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

        reforger_entry = _parse_reforger_player_line(line)
        if reforger_entry is not None:
            entries.append(reforger_entry)
            continue

        hash_match = HASH_PLAYER_PREFIX_RE.match(line)
        if hash_match:
            entries.append(
                PlayerEntry(
                    name=hash_match.group("name").strip(),
                    player_id=hash_match.group("player_id"),
                    raw=line,
                )
            )
            continue

        if line.startswith("#"):
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

    return _dedupe_player_entries(entries)


def _is_empty_player_roster_response(response: str) -> bool:
    """Return True when RCON returned only login/command noise and roster headers."""
    meaningful_lines: list[str] = []

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lowered = line.lower()
        if lowered.startswith(RCON_NOISE_PREFIXES):
            continue

        if lowered in {"players on server:", "players on server"}:
            continue

        if lowered.startswith("players on server:") and "[player" in lowered:
            continue

        if lowered.startswith("players") and ":" in line:
            continue

        meaningful_lines.append(line)

    return not meaningful_lines


def _query_player_entries(session: _RconSession) -> list[PlayerEntry]:
    """Try the most likely roster commands and return the first non-empty parse."""
    last_error: str = ""
    saw_empty_roster = False

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
            if _is_empty_player_roster_response(response):
                saw_empty_roster = True
                continue
            last_error = response

    if saw_empty_roster:
        return []
    if last_error:
        raise RconError(last_error)
    return []


def query_player_roster(
    instance: str,
    timeout: float = RCON_ROSTER_TIMEOUT_SECONDS,
) -> PlayerRoster:
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


def _bounded_native_ban_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        value = RCON_NATIVE_BAN_TIMEOUT_SECONDS
    if not math.isfinite(value):
        value = RCON_NATIVE_BAN_TIMEOUT_SECONDS
    return min(
        max(value, RCON_NATIVE_BAN_MIN_TIMEOUT_SECONDS),
        RCON_NATIVE_BAN_MAX_TIMEOUT_SECONDS,
    )


def query_native_ban_list(
    instance: str,
    *,
    page: int = NATIVE_BAN_MIN_PAGE,
    timeout: float = RCON_NATIVE_BAN_TIMEOUT_SECONDS,
) -> NativeBanListResult:
    """Read one bounded native Reforger ban page through typed RCON."""
    requested_page = normalize_native_ban_page(page)
    bounded_timeout = _bounded_native_ban_timeout(timeout)

    try:
        state = discover(instance, save=False)
    except Exception:
        return _native_ban_unavailable(
            requested_page,
            NATIVE_BAN_ERROR_CONFIG_UNAVAILABLE,
            "Server discovery is unavailable.",
        )

    host = "127.0.0.1"
    port = state.ports.rcon or 19999
    password = ""
    if state.config_exists and state.config_path:
        try:
            config = load_config(state.config_path)
        except (ConfigError, OSError):
            return _native_ban_unavailable(
                requested_page,
                NATIVE_BAN_ERROR_CONFIG_UNAVAILABLE,
                "RCON configuration is unavailable.",
            )
        host = _extract_rcon_host(config)
        port = _extract_rcon_port(config)
        password = _extract_rcon_password(config)

    if not password:
        return _native_ban_unavailable(
            requested_page,
            NATIVE_BAN_ERROR_NOT_CONFIGURED,
            "RCON is not configured for native ban-list reads.",
        )
    if not state.server_running:
        return _native_ban_unavailable(
            requested_page,
            NATIVE_BAN_ERROR_SERVER_UNAVAILABLE,
            "Native ban list is unavailable while the server is stopped.",
        )

    session: _RconSession | None = None
    try:
        session = _RconSession(host, port, password, bounded_timeout)
        session.login()
        response = session.send_command(f"#ban list {requested_page}")
        if not getattr(session, "last_response_complete", True):
            response = f"{response}\nIncomplete multipart response."
        return _parse_native_ban_list_response(
            response,
            requested_page=requested_page,
        )
    except TimeoutError:
        return _native_ban_unavailable(
            requested_page,
            NATIVE_BAN_ERROR_TIMEOUT,
            "Native ban-list request timed out.",
        )
    except RconError as error:
        error_text = str(error).casefold()
        if "login" in error_text:
            return _native_ban_unavailable(
                requested_page,
                NATIVE_BAN_ERROR_PERMISSION_DENIED,
                "RCON authentication failed.",
            )
        if "timed out" in error_text:
            return _native_ban_unavailable(
                requested_page,
                NATIVE_BAN_ERROR_TIMEOUT,
                "Native ban-list request timed out.",
            )
        return _native_ban_unavailable(
            requested_page,
            NATIVE_BAN_ERROR_RCON_UNAVAILABLE,
            "Native ban-list request failed.",
        )
    except OSError:
        return _native_ban_unavailable(
            requested_page,
            NATIVE_BAN_ERROR_RCON_UNAVAILABLE,
            "RCON is unavailable.",
        )
    finally:
        if session is not None:
            session.logout()
            try:
                session.close()
            except OSError:
                pass
