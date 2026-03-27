"""Ports module — check listening ports via ss."""

from __future__ import annotations

import re
import subprocess


def get_listening_ports() -> dict[int, str]:
    """Get all listening UDP ports and their process names.

    Returns a dict mapping port_number -> process_info_string.
    """
    result: dict[int, str] = {}
    try:
        proc = subprocess.run(
            ["ss", "-lunpt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return result

    for line in proc.stdout.splitlines()[1:]:  # skip header
        # Parse port from local address column
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[4]
        # Format: 0.0.0.0:2001 or *:2001 or [::]:2001
        match = re.search(r":(\d+)$", local_addr)
        if match:
            port = int(match.group(1))
            process_info = parts[-1] if len(parts) > 5 else ""
            result[port] = process_info

    return result


def check_server_ports(
    game_port: int = 2001,
    a2s_port: int = 17777,
    rcon_port: int = 19999,
) -> dict[str, dict[str, int | bool]]:
    """Check if the standard server ports are listening.

    Returns a structured dict with port name, number, and listening status.
    """
    listening = get_listening_ports()

    return {
        "game": {"port": game_port, "listening": game_port in listening},
        "a2s": {"port": a2s_port, "listening": a2s_port in listening},
        "rcon": {"port": rcon_port, "listening": rcon_port in listening},
    }


def format_ports_table(
    game_port: int = 2001,
    a2s_port: int = 17777,
    rcon_port: int = 19999,
) -> str:
    """Return a human-readable table of port status."""
    status = check_server_ports(game_port, a2s_port, rcon_port)

    lines = ["Port        Number  Status", "──────────  ──────  ──────"]
    for name, info in status.items():
        icon = "✓" if info["listening"] else "✗"
        label = "listening" if info["listening"] else "closed"
        lines.append(f"{name:<10}  {info['port']:<6}  {icon} {label}")

    return "\n".join(lines)
