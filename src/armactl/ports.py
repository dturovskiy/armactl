"""Ports module - check listening ports and manage UFW rules."""

from __future__ import annotations

import re
import subprocess


def get_listening_ports() -> dict[int, str]:
    """Get all listening UDP/TCP ports and their process names."""
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

    for index, line in enumerate(proc.stdout.splitlines()):
        if index == 0:
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        local_addr = parts[4]
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
    """Check if the standard server ports are listening."""
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

    lines = ["Port        Number  Status", "----------  ------  ------"]
    for name, info in status.items():
        marker = "OK" if info["listening"] else "X"
        label = "listening" if info["listening"] else "closed"
        lines.append(f"{name:<10}  {info['port']:<6}  {marker} {label}")

    return "\n".join(lines)


def manage_ports(
    action: str,
    game_port: int = 2001,
    a2s_port: int = 17777,
    rcon_port: int = 19999,
) -> list[str]:
    """Open or close ports using UFW."""
    if action not in ("open", "close"):
        raise ValueError(f"Invalid port action: {action}")

    msgs = []
    ufw_action = ["allow"] if action == "open" else ["delete", "allow"]

    # Game and A2S are UDP. RCON may also be used over TCP, so we add both.
    for name, port in [("Game", game_port), ("A2S", a2s_port), ("RCON", rcon_port)]:
        cmds = [["sudo", "ufw", *ufw_action, f"{port}/udp"]]
        if name == "RCON":
            cmds.append(["sudo", "ufw", *ufw_action, f"{port}/tcp"])

        for cmd in cmds:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                msg = res.stdout.strip() or "Done"
                marker = "OK" if action == "open" else "X"
                msgs.append(f"  {marker} {name} port {cmd[-1]}: {msg}")
            except subprocess.CalledProcessError as e:
                error_output = e.stderr.strip() or e.stdout.strip()
                msgs.append(f"  ! Failed for {name} port {cmd[-1]}: {error_output}")
            except FileNotFoundError:
                msgs.append("  ! Failed: 'ufw' command not found. Are you on Ubuntu?")
                return msgs

    return msgs
