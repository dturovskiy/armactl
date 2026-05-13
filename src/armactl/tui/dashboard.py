"""Small formatting helpers for the TUI dashboard."""

from __future__ import annotations


def format_usage_bar(
    used: int | None,
    total: int | None,
    *,
    width: int = 10,
) -> str:
    """Return an ASCII usage bar with a percentage."""
    fallback_width = width if width > 0 else 10
    if used is None or total is None or total <= 0 or width <= 0:
        return f"[{'-' * fallback_width}] Unknown"

    ratio = max(0.0, min(float(used) / float(total), 1.0))
    filled = round(ratio * width)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {ratio * 100:.0f}%"


def format_player_count(current: int | None, maximum: int | None) -> str:
    """Return a compact player count label."""
    if current is None:
        return "Unknown"
    if maximum is None:
        return str(current)
    return f"{current} / {maximum}"
