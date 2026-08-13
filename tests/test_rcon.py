"""Tests for BattlEye RCON helpers."""

from __future__ import annotations

import zlib
from unittest.mock import patch

import pytest

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


def test_parse_player_lines_supports_reforger_hash_prefixed_player_number():
    response = """
Logged In! Client ID: #0
Processing Command: #players
Players on server: [Player#] ; [Player UID] ; [Player Name]
#1 ; 0109fcf5-a861-4002-881e-8a497c59797c ; MisanTropiC#DivisioN
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


def test_parse_player_lines_deduplicates_repeated_reforger_rows_by_guid():
    response = """
Players on server: [Player#] ; [Player UID] ; [Player Name]
#1 ; 9ea05788-5a32-4148-b847-4770dae69ef6 ; S.G.L.Cerberus
#2 ; 718c1fdb-7990-41c8-9c4d-1914dbec1681 ; SGL_Taran
#1 ; 9ea05788-5a32-4148-b847-4770dae69ef6 ; S.G.L.Cerberus
#2 ; 718c1fdb-7990-41c8-9c4d-1914dbec1681 ; SGL_Taran
""".strip()

    entries = rcon._parse_player_lines(response)

    assert [(entry.player_id, entry.guid, entry.name) for entry in entries] == [
        ("1", "9ea05788-5a32-4148-b847-4770dae69ef6", "S.G.L.Cerberus"),
        ("2", "718c1fdb-7990-41c8-9c4d-1914dbec1681", "SGL_Taran"),
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


def test_send_command_marks_missing_multipart_response_incomplete() -> None:
    with patch("armactl.rcon.socket.socket", return_value=_FakeSocket()):
        session = rcon._RconSession("127.0.0.1", 19999, "secret", timeout=1.0)

    with patch.object(
        session,
        "_recv_payload",
        side_effect=[
            bytes([rcon.BE_COMMAND, 0, 0, 2, 0])
            + b"Bans: [BanID] ; [Player UID] ; [Duration]\n"
            + b"1 ; 21761a7f-c9b4-4bff-8375-b4b43abb95ec ; 3600",
            TimeoutError(),
        ],
    ):
        response = session.send_command("#ban list 1")

    assert "21761a7f-c9b4-4bff-8375-b4b43abb95ec" in response
    assert session.last_response_complete is False


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


def test_query_player_roster_uses_short_default_timeout() -> None:
    state = ServerState(
        server_running=True,
        config_exists=True,
        config_path="/tmp/config.json",
        ports=PortInfo(rcon=20000),
    )
    config = {
        "rcon": {
            "address": "127.0.0.1",
            "port": 20000,
            "password": "secret",
        }
    }
    captured: dict[str, float] = {}

    class FakeSession:
        def __init__(self, host: str, port: int, password: str, timeout: float):
            captured["timeout"] = timeout

        def login(self) -> None:
            pass

        def send_command(self, command: str) -> str:
            return "1 Alice" if command == "#players" else ""

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=state),
        patch("armactl.rcon.load_config", return_value=config),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        roster = rcon.query_player_roster("default")

    assert captured["timeout"] == rcon.RCON_ROSTER_TIMEOUT_SECONDS
    assert captured["timeout"] == 1.5
    assert roster.available is True
    assert [entry.name for entry in roster.entries] == ["Alice"]


def test_query_player_roster_allows_explicit_timeout_override() -> None:
    state = ServerState(
        server_running=True,
        config_exists=True,
        config_path="/tmp/config.json",
        ports=PortInfo(rcon=20000),
    )
    config = {
        "rcon": {
            "address": "127.0.0.1",
            "port": 20000,
            "password": "secret",
        }
    }
    captured: dict[str, float] = {}

    class FakeSession:
        def __init__(self, host: str, port: int, password: str, timeout: float):
            captured["timeout"] = timeout

        def login(self) -> None:
            pass

        def send_command(self, command: str) -> str:
            return ""

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=state),
        patch("armactl.rcon.load_config", return_value=config),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        rcon.query_player_roster("default", timeout=0.25)

    assert captured["timeout"] == 0.25


def test_query_player_entries_treats_header_only_roster_as_empty() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def send_command(self, command: str) -> str:
            self.commands.append(command)
            return """
Logged In! Client ID: #0
Processing Command: #players
Players on server: [Player#] ; [Player UID] ; [Player Name]
""".strip()

    session = FakeSession()

    entries = rcon._query_player_entries(session)

    assert entries == []
    assert session.commands == ["#players", "players"]


NATIVE_BAN_HEADER = "Bans: [BanID] ; [Player UID] ; [Duration]"


def _native_ban_row(index: int, duration: int = 3600) -> str:
    return (
        f"{index} ; "
        f"21761a7f-c9b4-4bff-8375-{index:012d} ; "
        f"{duration}"
    )


def test_parse_native_ban_list_accepts_authoritative_empty_page() -> None:
    result = rcon._parse_native_ban_list_response(
        "\n".join(
            (
                "Logged In! Client ID: #0",
                "Processing Command: #ban list 1",
                NATIVE_BAN_HEADER,
            )
        ),
        requested_page=1,
    )

    assert result == rcon.NativeBanListResult(
        requested_page=1,
        available=True,
        complete=True,
        status=rcon.NATIVE_BAN_STATUS_COMPLETE,
        entries=(),
        has_previous=False,
        has_next=False,
    )


def test_parse_native_ban_list_accepts_one_valid_permanent_row() -> None:
    result = rcon._parse_native_ban_list_response(
        f"{NATIVE_BAN_HEADER}\n{_native_ban_row(1, duration=0)}",
        requested_page=1,
    )

    assert result.status == rcon.NATIVE_BAN_STATUS_COMPLETE
    assert result.entries == (
        rcon.NativeBanEntry(
            native_ban_id="1",
            player_uid="21761a7f-c9b4-4bff-8375-000000000001",
            duration_seconds=0,
        ),
    )


def test_parse_native_ban_list_accepts_multiple_positive_duration_rows() -> None:
    result = rcon._parse_native_ban_list_response(
        "\n".join((NATIVE_BAN_HEADER, _native_ban_row(1), _native_ban_row(2, 86400))),
        requested_page=3,
    )

    assert result.status == rcon.NATIVE_BAN_STATUS_COMPLETE
    assert [entry.duration_seconds for entry in result.entries] == [3600, 86400]
    assert result.has_previous is True
    assert result.has_next is False


def test_native_ban_page_navigation_is_bounded_and_one_page_at_a_time() -> None:
    page_rows = "\n".join(_native_ban_row(index) for index in range(1, 26))

    middle = rcon._parse_native_ban_list_response(
        f"{NATIVE_BAN_HEADER}\n{page_rows}",
        requested_page=2,
    )
    last_bounded = rcon._parse_native_ban_list_response(
        f"{NATIVE_BAN_HEADER}\n{page_rows}",
        requested_page=rcon.NATIVE_BAN_MAX_PAGE,
    )

    assert len(middle.entries) == rcon.NATIVE_BAN_PAGE_SIZE
    assert middle.has_previous is True
    assert middle.has_next is True
    assert last_bounded.has_previous is True
    assert last_bounded.has_next is False


def test_parse_native_ban_list_rejects_malformed_only_response() -> None:
    result = rcon._parse_native_ban_list_response(
        f"{NATIVE_BAN_HEADER}\nnot ; a valid ; duration",
        requested_page=1,
    )

    assert result.available is False
    assert result.complete is False
    assert result.status == rcon.NATIVE_BAN_STATUS_UNAVAILABLE
    assert result.entries == ()
    assert result.error_code == rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE


def test_parse_native_ban_list_labels_rows_plus_truncated_content_partial() -> None:
    result = rcon._parse_native_ban_list_response(
        f"{NATIVE_BAN_HEADER}\n{_native_ban_row(1)}\n2 ; truncated",
        requested_page=1,
    )

    assert result.available is True
    assert result.complete is False
    assert result.status == rcon.NATIVE_BAN_STATUS_PARTIAL
    assert len(result.entries) == 1
    assert result.error_code == rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE


def test_parse_native_ban_list_labels_duplicate_native_ids_partial() -> None:
    row = _native_ban_row(1)
    result = rcon._parse_native_ban_list_response(
        f"{NATIVE_BAN_HEADER}\n{row}\n{row}",
        requested_page=1,
    )

    assert result.available is True
    assert result.complete is False
    assert result.status == rcon.NATIVE_BAN_STATUS_PARTIAL
    assert len(result.entries) == 1
    assert result.error_code == rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "response,error_code",
    (
        ("Permission denied from 198.51.100.10 password=secret", "permission_denied"),
        ("Unknown command #ban list token=secret", "command_unavailable"),
        ("unexpected password=secret at 198.51.100.10:19999", "malformed_response"),
    ),
)
def test_native_ban_denial_unknown_and_malformed_errors_are_sanitized(
    response: str,
    error_code: str,
) -> None:
    result = rcon._parse_native_ban_list_response(response, requested_page=1)

    assert result.available is False
    assert result.error_code == error_code
    serialized = repr(result)
    for forbidden in (
        response,
        "secret",
        "198.51.100.10",
        "19999",
        "#ban list",
        "password",
    ):
        assert forbidden not in serialized


def test_native_ban_parser_caps_rows_and_rejects_page_out_of_bounds() -> None:
    too_many_rows = "\n".join(_native_ban_row(index) for index in range(1, 28))
    result = rcon._parse_native_ban_list_response(
        f"{NATIVE_BAN_HEADER}\n{too_many_rows}",
        requested_page=1,
    )

    assert result.status == rcon.NATIVE_BAN_STATUS_PARTIAL
    assert len(result.entries) == rcon.NATIVE_BAN_PAGE_SIZE
    for page in (0, 101):
        with pytest.raises(ValueError, match="between 1 and 100"):
            rcon.normalize_native_ban_page(page)
    with pytest.raises(ValueError, match="integer"):
        rcon.normalize_native_ban_page(True)


def _native_ban_server_state() -> ServerState:
    return ServerState(
        server_installed=True,
        server_running=True,
        config_exists=True,
        config_path="/srv/armactl/config.json",
        ports=PortInfo(rcon=19999),
    )


def test_query_native_ban_list_sends_only_typed_requested_page_command() -> None:
    commands: list[str] = []

    class FakeSession:
        def __init__(self, host: str, port: int, password: str, timeout: float):
            assert (host, port, password) == ("127.0.0.1", 19999, "raw-secret")
            assert timeout == rcon.RCON_NATIVE_BAN_TIMEOUT_SECONDS

        def login(self) -> None:
            pass

        def send_command(self, command: str) -> str:
            commands.append(command)
            return f"{NATIVE_BAN_HEADER}\n{_native_ban_row(1)}"

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=_native_ban_server_state()),
        patch(
            "armactl.rcon.load_config",
            return_value={
                "rcon": {
                    "address": "127.0.0.1",
                    "port": 19999,
                    "password": "raw-secret",
                }
            },
        ),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        result = rcon.query_native_ban_list("default", page=2)

    assert result.status == rcon.NATIVE_BAN_STATUS_COMPLETE
    assert result.requested_page == 2
    assert commands == ["#ban list 2"]


def test_query_native_ban_list_labels_transport_truncation_partial() -> None:
    class FakeSession:
        last_response_complete = False

        def __init__(self, host: str, port: int, password: str, timeout: float):
            pass

        def login(self) -> None:
            pass

        def send_command(self, command: str) -> str:
            return f"{NATIVE_BAN_HEADER}\n{_native_ban_row(1)}"

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=_native_ban_server_state()),
        patch(
            "armactl.rcon.load_config",
            return_value={"rcon": {"password": "raw-secret", "port": 19999}},
        ),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        result = rcon.query_native_ban_list("default", page=1)

    assert result.status == rcon.NATIVE_BAN_STATUS_PARTIAL
    assert result.available is True
    assert result.complete is False
    assert len(result.entries) == 1


@pytest.mark.parametrize(
    ("failure", "error_code"),
    (
        (TimeoutError("198.51.100.10 password=raw-secret"), rcon.NATIVE_BAN_ERROR_TIMEOUT),
        (
            rcon.RconError(
                "RCON login failed at 198.51.100.10:19999 password=raw-secret"
            ),
            rcon.NATIVE_BAN_ERROR_PERMISSION_DENIED,
        ),
        (
            OSError("network failed at 198.51.100.10:19999 password=raw-secret"),
            rcon.NATIVE_BAN_ERROR_RCON_UNAVAILABLE,
        ),
    ),
)
def test_query_native_ban_list_returns_controlled_transport_failures(
    failure: Exception,
    error_code: str,
) -> None:
    class FakeSession:
        def __init__(self, host: str, port: int, password: str, timeout: float):
            pass

        def login(self) -> None:
            raise failure

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=_native_ban_server_state()),
        patch(
            "armactl.rcon.load_config",
            return_value={"rcon": {"password": "raw-secret", "port": 19999}},
        ),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        result = rcon.query_native_ban_list("default", page=1)

    assert result.available is False
    assert result.error_code == error_code
    serialized = repr(result)
    for forbidden in ("raw-secret", "198.51.100.10", "19999", "password"):
        assert forbidden not in serialized


def test_native_ban_timeout_override_is_bounded() -> None:
    assert (
        rcon._bounded_native_ban_timeout(999)
        == rcon.RCON_NATIVE_BAN_MAX_TIMEOUT_SECONDS
    )
    assert (
        rcon._bounded_native_ban_timeout(0)
        == rcon.RCON_NATIVE_BAN_MIN_TIMEOUT_SECONDS
    )


def test_rcon_module_exposes_no_generic_command_executor() -> None:
    assert not hasattr(rcon, "execute_rcon_command")
    assert not hasattr(rcon, "query_rcon_command")


NATIVE_MODERATION_TARGET = "21761a7f-c9b4-4bff-8375-b4b43abb95ec"


@pytest.mark.parametrize(
    "target",
    ("", "nickname only", "id\n#shutdown", "198.51.100.10"),
)
def test_native_moderation_target_rejects_untyped_or_unsafe_values(
    target: str,
) -> None:
    with pytest.raises(ValueError, match="identity is invalid"):
        rcon.normalize_native_ban_target(target)


@pytest.mark.parametrize(
    "duration",
    (-1, rcon.NATIVE_BAN_MAX_DURATION_SECONDS + 1, True, "3600"),
)
def test_native_ban_duration_is_strictly_bounded(duration: object) -> None:
    with pytest.raises(ValueError, match="duration"):
        rcon.normalize_native_ban_duration(duration)


@pytest.mark.parametrize(
    "reason",
    ("bad\nreason", "teamkilling\rretry", "reason-" + chr(0x451), "x" * 161),
)
def test_native_ban_reason_rejects_controls_non_ascii_and_oversize(
    reason: str,
) -> None:
    with pytest.raises(ValueError, match="reason"):
        rcon.normalize_native_ban_reason(reason)


@pytest.mark.parametrize(
    ("response", "status", "error_code"),
    (
        (
            "Permission denied password=raw-secret 198.51.100.10:19999",
            rcon.NATIVE_MODERATION_STATUS_REJECTED,
            rcon.NATIVE_BAN_ERROR_PERMISSION_DENIED,
        ),
        (
            "Unknown command #ban create token=raw-secret",
            rcon.NATIVE_MODERATION_STATUS_REJECTED,
            rcon.NATIVE_BAN_ERROR_COMMAND_UNAVAILABLE,
        ),
        (
            "Processing Command: #ban create sanitized fixture",
            rcon.NATIVE_MODERATION_STATUS_DISPATCHED,
            "",
        ),
        ("", rcon.NATIVE_MODERATION_STATUS_DISPATCHED, ""),
    ),
)
def test_native_moderation_response_classification_is_controlled(
    response: str,
    status: str,
    error_code: str,
) -> None:
    result = rcon._parse_native_moderation_command_response(
        response,
        action=rcon.NATIVE_MODERATION_ACTION_BAN,
        target_identity=NATIVE_MODERATION_TARGET,
    )

    assert result.status == status
    assert result.dispatched is True
    assert result.error_code == error_code
    serialized = repr(result)
    for forbidden in (
        "raw-secret",
        "198.51.100.10",
        "19999",
        "#ban create",
        "password",
        "token",
    ):
        assert forbidden not in serialized


def test_typed_native_ban_and_unban_send_only_bounded_commands() -> None:
    commands: list[str] = []

    class FakeSession:
        last_response_complete = True

        def __init__(self, host: str, port: int, password: str, timeout: float):
            assert (host, port, password) == ("127.0.0.1", 19999, "raw-secret")
            assert timeout == rcon.RCON_NATIVE_BAN_TIMEOUT_SECONDS

        def login(self) -> None:
            pass

        def send_command(self, command: str) -> str:
            commands.append(command)
            return ""

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=_native_ban_server_state()),
        patch(
            "armactl.rcon.load_config",
            return_value={"rcon": {"password": "raw-secret", "port": 19999}},
        ),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        created = rcon.create_native_ban(
            "default",
            target_identity=NATIVE_MODERATION_TARGET,
            duration_seconds=3600,
            reason="teamkilling",
        )
        removed = rcon.remove_native_ban(
            "default",
            target_identity=NATIVE_MODERATION_TARGET,
        )

    assert created.status == rcon.NATIVE_MODERATION_STATUS_DISPATCHED
    assert created.dispatched is True
    assert removed.status == rcon.NATIVE_MODERATION_STATUS_DISPATCHED
    assert removed.dispatched is True
    assert commands == [
        f"#ban create {NATIVE_MODERATION_TARGET} 3600 teamkilling",
        f"#ban remove {NATIVE_MODERATION_TARGET}",
    ]


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    (
        (
            rcon.RconError("RCON command timed out password=raw-secret"),
            rcon.NATIVE_MODERATION_STATUS_UNCERTAIN,
        ),
        (
            OSError("network lost 198.51.100.10:19999"),
            rcon.NATIVE_MODERATION_STATUS_UNCERTAIN,
        ),
    ),
)
def test_native_moderation_transport_failure_after_send_is_uncertain(
    failure: Exception,
    expected_status: str,
) -> None:
    class FakeSession:
        last_response_complete = True

        def __init__(self, host: str, port: int, password: str, timeout: float):
            pass

        def login(self) -> None:
            pass

        def send_command(self, command: str) -> str:
            raise failure

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=_native_ban_server_state()),
        patch(
            "armactl.rcon.load_config",
            return_value={"rcon": {"password": "raw-secret", "port": 19999}},
        ),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        result = rcon.remove_native_ban(
            "default",
            target_identity=NATIVE_MODERATION_TARGET,
        )

    assert result.status == expected_status
    assert result.dispatched is True
    for forbidden in ("raw-secret", "198.51.100.10", "19999", "password"):
        assert forbidden not in repr(result)


def test_native_moderation_incomplete_response_is_uncertain() -> None:
    class FakeSession:
        last_response_complete = False

        def __init__(self, host: str, port: int, password: str, timeout: float):
            pass

        def login(self) -> None:
            pass

        def send_command(self, command: str) -> str:
            return "unverified raw response password=raw-secret"

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pass

    with (
        patch("armactl.rcon.discover", return_value=_native_ban_server_state()),
        patch(
            "armactl.rcon.load_config",
            return_value={"rcon": {"password": "raw-secret", "port": 19999}},
        ),
        patch("armactl.rcon._RconSession", FakeSession),
    ):
        result = rcon.create_native_ban(
            "default",
            target_identity=NATIVE_MODERATION_TARGET,
            duration_seconds=0,
        )

    assert result.status == rcon.NATIVE_MODERATION_STATUS_UNCERTAIN
    assert result.dispatched is True
    assert result.error_code == rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE
    assert "raw-secret" not in repr(result)
