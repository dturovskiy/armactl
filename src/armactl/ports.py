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

    lines = proc.stdout.splitlines()
    for idx, line in enumerate(lines):
        if idx == 0:
            continue  # skip header
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
    
    # Game and A2S are UDP, RCON is technically UDP (Battleye) but can be TCP depending on the implementation.
    # We will just allow both UDP/TCP by omitting the protocol, or explicit udp. Let's omit protocol to be safe and cover both.
    
    for name, port in [("Game", game_port), ("A2S", a2s_port), ("RCON", rcon_port)]:
        cmd = ["sudo", "ufw"] + ufw_action + [f"{port}/udp"]
        # We also specifically open tcp for RCON just in case standard tools use it
        cmds = [cmd]
        if name == "RCON":
            cmds.append(["sudo", "ufw"] + ufw_action + [f"{port}/tcp"])
            
        for c in cmds:
            try:
                res = subprocess.run(c, capture_output=True, text=True, check=True)
                msg = res.stdout.strip()
                if not msg:
                    msg = "Done"
                msgs.append(f"  {'✓' if action == 'open' else '✗'} {name} port {c[-1]}: {msg}")
            except subprocess.CalledProcessError as e:
                msgs.append(f"  ! Failed for {name} port {c[-1]}: {e.stderr.strip() or e.stdout.strip()}")
            except FileNotFoundError:
                msgs.append("  ! Failed: 'ufw' command not found. Are you on Ubuntu?")
                return msgs # Error early
                
    return msgs
