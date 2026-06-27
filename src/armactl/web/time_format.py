"""Small web-only timestamp formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def format_web_timestamp(value: object | None) -> str:
    """Return a compact UTC timestamp for operator-facing web pages."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M UTC")
