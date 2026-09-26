"""Pure presentation formatting for metric values and durations."""

from __future__ import annotations


def format_bytes(value: int | None) -> str:
    """Format a byte count using small binary units."""
    if value is None:
        return "Unknown"

    size = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(value)} B"


def format_cpu_percent(value: float | None) -> str:
    """Format a CPU percentage for status output."""
    if value is None:
        return "Unknown"
    return f"{value:.1f}%"


def format_fps(value: float | None) -> str:
    """Format a real server FPS value parsed from engine telemetry."""
    if value is None:
        return "Unknown"
    return f"{value:.1f}"


def format_frame_time_ms(value: float | None) -> str:
    """Format a frame time in milliseconds."""
    if value is None:
        return "Unknown"
    return f"{value:.1f} ms"


def format_load_average(
    one_minute: float | None,
    five_minutes: float | None,
    fifteen_minutes: float | None,
) -> str:
    """Format a host load-average triple."""
    if one_minute is None or five_minutes is None or fifteen_minutes is None:
        return "Unknown"
    return f"{one_minute:.2f} / {five_minutes:.2f} / {fifteen_minutes:.2f}"


def format_duration(seconds: float | None) -> str:
    """Format a duration in a compact human-readable style."""
    if seconds is None:
        return "Unknown"

    remaining = max(int(seconds), 0)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds_part = divmod(remaining, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds_part}s")
    return " ".join(parts[:3])
