"""Pure restart-timer schedule parsing and presentation helpers."""

from __future__ import annotations

import re

TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
DAILY_TIME_RE = re.compile(r"^\*-\*-\* (\d{1,2}:\d{2}:\d{2})$")
INVALID_RESTART_TIME_MESSAGE = (
    "Restart times must use HH:MM[:SS] with hours 0-23 and "
    "minutes/seconds 0-59."
)


def normalize_on_calendar(on_calendar: str) -> str:
    """Normalize friendly time-only input into a full systemd OnCalendar value."""
    value = on_calendar.strip()
    if TIME_ONLY_RE.fullmatch(value):
        parts = value.split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        if hour > 23 or minute > 59 or second > 59:
            return ""
        return f"*-*-* {hour:02d}:{minute:02d}:{second:02d}"
    return value


def normalize_on_calendar_entries(on_calendar: str | list[str]) -> list[str]:
    """Normalize one or more schedule entries into systemd OnCalendar expressions."""
    if isinstance(on_calendar, list):
        raw_entries = on_calendar
    else:
        value = on_calendar.strip()
        if not value:
            return []
        if "\n" in value:
            raw_entries = value.splitlines()
        elif ";" in value:
            raw_entries = value.split(";")
        elif "," in value:
            comma_entries = [entry.strip() for entry in value.split(",") if entry.strip()]
            if comma_entries and all(TIME_ONLY_RE.match(entry) for entry in comma_entries):
                raw_entries = comma_entries
            else:
                raw_entries = [value]
        elif " " in value:
            space_entries = [entry.strip() for entry in value.split() if entry.strip()]
            if len(space_entries) > 1 and all(TIME_ONLY_RE.match(entry) for entry in space_entries):
                raw_entries = space_entries
            else:
                raw_entries = [value]
        else:
            raw_entries = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in raw_entries:
        cleaned = entry.strip()
        if not cleaned:
            continue
        normalized_entry = normalize_on_calendar(cleaned)
        if not normalized_entry:
            return []
        if normalized_entry in seen:
            continue
        seen.add(normalized_entry)
        normalized.append(normalized_entry)
    return normalized


def has_schedule_input(on_calendar: str | list[str]) -> bool:
    """Return whether the operator supplied any non-whitespace schedule input."""
    if isinstance(on_calendar, list):
        return any(str(entry).strip() for entry in on_calendar)
    return bool(str(on_calendar).strip())


def format_schedule_for_input(schedule_entries: list[str]) -> str:
    """Convert stored OnCalendar entries into a friendly input string."""
    if not schedule_entries:
        return ""

    display_times: list[str] = []
    for entry in schedule_entries:
        match = DAILY_TIME_RE.fullmatch(entry.strip())
        if not match:
            return "; ".join(schedule_entries)
        time_value = match.group(1)
        if time_value.endswith(":00"):
            time_value = time_value[:-3]
        display_times.append(time_value)

    return ", ".join(display_times)
