"""Tests for shared port helpers."""

from __future__ import annotations

from armactl.ports import (
    BLOCKED_WEB_PORTS,
    WEB_PANEL_DEFAULT_PORT,
    explain_web_port_conflict,
    get_blocked_web_ports,
    is_port_number,
)


def test_blocked_web_ports_include_reserved_defaults() -> None:
    assert BLOCKED_WEB_PORTS[22] == "SSH"
    assert BLOCKED_WEB_PORTS[80] == "HTTP/reverse proxy"
    assert BLOCKED_WEB_PORTS[443] == "HTTPS/reverse proxy"
    assert BLOCKED_WEB_PORTS[2001] == "Arma game default"
    assert BLOCKED_WEB_PORTS[17777] == "Steam A2S default"
    assert BLOCKED_WEB_PORTS[19999] == "RCON default"


def test_blocked_web_ports_include_instance_game_ports() -> None:
    blocked = get_blocked_web_ports(game_port=3000, a2s_port=18000, rcon_port=20000)

    assert blocked[3000] == "configured Arma game port"
    assert blocked[18000] == "configured Steam A2S port"
    assert blocked[20000] == "configured RCON port"


def test_web_default_port_is_allowed_when_free() -> None:
    assert explain_web_port_conflict(WEB_PANEL_DEFAULT_PORT, listening_ports={}) is None


def test_web_port_conflict_rejects_invalid_port_values() -> None:
    assert explain_web_port_conflict(0) == "Port must be an integer between 1 and 65535."
    assert explain_web_port_conflict(65536) == "Port must be an integer between 1 and 65535."
    assert explain_web_port_conflict(True) == "Port must be an integer between 1 and 65535."
    assert not is_port_number(True)


def test_web_port_conflict_rejects_reserved_defaults() -> None:
    assert explain_web_port_conflict(2001) == "Port 2001 is reserved for Arma game default."
    assert explain_web_port_conflict(22) == "Port 22 is reserved for SSH."


def test_web_port_conflict_rejects_instance_game_ports() -> None:
    conflict = explain_web_port_conflict(
        3000,
        game_port=3000,
        a2s_port=18000,
        rcon_port=20000,
    )

    assert conflict == "Port 3000 is reserved for configured Arma game port."


def test_web_port_conflict_allows_reverse_proxy_ports_only_when_explicit() -> None:
    assert explain_web_port_conflict(443) == "Port 443 is reserved for HTTPS/reverse proxy."
    assert explain_web_port_conflict(443, allow_reverse_proxy_ports=True) is None


def test_reverse_proxy_override_does_not_allow_instance_game_port() -> None:
    conflict = explain_web_port_conflict(
        443,
        game_port=443,
        allow_reverse_proxy_ports=True,
    )

    assert conflict == "Port 443 is reserved for configured Arma game port."


def test_web_port_conflict_rejects_listening_port() -> None:
    conflict = explain_web_port_conflict(
        WEB_PANEL_DEFAULT_PORT,
        listening_ports={WEB_PANEL_DEFAULT_PORT: "python"},
    )

    assert conflict == "Port 8765 is already listening (python)."
