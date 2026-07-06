"""Small web-only timestamp formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from markupsafe import Markup, escape


def utc_iso_timestamp(value: object | None) -> str:
    """Return a machine-readable UTC ISO timestamp ending in ``Z``."""
    parsed = _parse_timestamp(value)
    if parsed is None:
        return ""
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def format_web_timestamp(value: object | None) -> Markup:
    """Return a local-time-ready HTML ``time`` element for web templates."""
    parsed = _parse_timestamp(value)
    if parsed is None:
        text = "" if value is None else str(value).strip()
        return Markup(escape(text))
    utc_value = parsed.astimezone(timezone.utc)
    iso_value = utc_value.isoformat().replace("+00:00", "Z")
    fallback = utc_value.strftime("%Y-%m-%d %H:%M UTC")
    escaped_iso = escape(iso_value)
    escaped_fallback = escape(fallback)
    return Markup(
        f'<time data-local-time datetime="{escaped_iso}" '
        f'title="{escaped_iso}">{escaped_fallback}</time>'
    )


def _parse_timestamp(value: object | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
