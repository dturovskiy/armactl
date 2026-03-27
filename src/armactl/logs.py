"""Logs module — read and tail server logs from journalctl.

Uses service_name from state.json to target the correct unit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def show_logs(
    service_name: str = "armareforger.service",
    lines: int = 50,
    follow: bool = False,
) -> int:
    """Show server logs from journalctl.

    If follow=True, streams logs in real-time (Ctrl+C to stop).
    Returns the exit code of journalctl.
    """
    cmd = ["journalctl", "-u", service_name, "--no-pager"]

    if follow:
        cmd.append("-f")
    else:
        cmd.extend(["-n", str(lines)])

    try:
        # Use os.execvp for follow mode so Ctrl+C works naturally
        if follow:
            os.execvp("journalctl", cmd)
            return 0  # unreachable, but satisfies type checker

        result = subprocess.run(cmd, timeout=15)
        return result.returncode

    except subprocess.TimeoutExpired:
        print("journalctl timed out", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("journalctl not found — is systemd installed?", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Failed to read logs: {e}", file=sys.stderr)
        return 1


def get_logs_text(
    service_name: str = "armareforger.service",
    lines: int = 50,
) -> str:
    """Get logs as a string (for JSON output mode)."""
    cmd = ["journalctl", "-u", service_name, "--no-pager", "-n", str(lines)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
