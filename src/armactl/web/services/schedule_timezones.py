"""Timezone-aware restart schedule DTOs for the web UI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from armactl.redaction import redact_sensitive_text

MAX_WEB_RESTART_TIMES = 3
INVALID_TIME_MESSAGE = "Use one to three restart times such as 05:00, 13:30."
INVALID_TIMEZONE_MESSAGE = "Use a valid IANA time zone such as Europe/Kyiv or UTC."

_WEB_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_TIME_SPLIT_RE = re.compile(r"[,;\s]+")
_UTC_DAILY_RE = re.compile(
    r"^\*-\*-\* (?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?:\s+UTC)?$"
)
_TIMEZONE_NAME_RE = re.compile(r"^[A-Za-z0-9._+\-/]{1,80}$")


class ScheduleTimezoneError(ValueError):
    """Raised when web schedule time or timezone input is invalid."""


@dataclass(frozen=True)
class ScheduleTimeRow:
    """One restart time projected as both browser-local and backend UTC."""

    local_time: str
    utc_time: str
    entry: str


@dataclass(frozen=True)
class ScheduleTimezoneDTO:
    """Parsed schedule values for rendering, auditing, and backend writes."""

    timezone: str
    reference_date: date
    local_times: tuple[str, ...]
    utc_times: tuple[str, ...]
    on_calendar_entries: tuple[str, ...]
    local_display: str
    utc_display: str
    summary: str
    rows: tuple[ScheduleTimeRow, ...]


def default_reference_date() -> date:
    """Return the UTC date used for daily schedule timezone projection."""
    return datetime.now(timezone.utc).date()


def coerce_reference_date(value: object = None) -> date:
    """Return a safe date for converting daily local times to UTC."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return default_reference_date()
    return default_reference_date()


def _safe_schedule_string(value: object) -> str:
    return redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()


def normalize_timezone_name(value: object = "UTC") -> str:
    """Validate and canonicalize a browser-supplied IANA timezone name."""
    name = str(value or "UTC").strip()
    if not name:
        name = "UTC"
    if name.upper() == "UTC":
        return "UTC"
    parts = name.replace("\\", "/").split("/")
    if (
        not _TIMEZONE_NAME_RE.fullmatch(name)
        or name.startswith(("/", "\\"))
        or ".." in parts
    ):
        raise ScheduleTimezoneError(INVALID_TIMEZONE_MESSAGE)
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleTimezoneError(INVALID_TIMEZONE_MESSAGE) from exc
    return name


def _zoneinfo(timezone_name: object = "UTC") -> tuple[str, ZoneInfo]:
    name = normalize_timezone_name(timezone_name)
    return name, ZoneInfo(name)


def _parse_web_time_entries(value: str) -> tuple[str, ...]:
    raw_entries = [entry.strip() for entry in _TIME_SPLIT_RE.split(value) if entry.strip()]
    if not raw_entries or len(raw_entries) > MAX_WEB_RESTART_TIMES:
        raise ScheduleTimezoneError(INVALID_TIME_MESSAGE)

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in raw_entries:
        if not _WEB_TIME_RE.fullmatch(entry):
            raise ScheduleTimezoneError(INVALID_TIME_MESSAGE)
        hour_text, minute_text = entry.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
        if hour > 23 or minute > 59:
            raise ScheduleTimezoneError(INVALID_TIME_MESSAGE)
        time_value = f"{hour:02d}:{minute:02d}"
        if time_value in seen:
            continue
        seen.add(time_value)
        normalized.append(time_value)
    if not normalized:
        raise ScheduleTimezoneError(INVALID_TIME_MESSAGE)
    return tuple(normalized)


def _entry_for_utc_time(utc_time: str) -> str:
    return f"*-*-* {utc_time}:00 UTC"


def _display_times(times: tuple[str, ...], suffix: str) -> str:
    if not times:
        return ""
    return f"{', '.join(times)} {suffix}".strip()


def _summary(local_display: str, utc_display: str) -> str:
    if local_display and utc_display and local_display != utc_display:
        return f"{local_display} ({utc_display})"
    return local_display or utc_display


def normalize_local_schedule(
    schedule_value: str,
    timezone_name: object = "UTC",
    *,
    reference_date: object = None,
) -> ScheduleTimezoneDTO:
    """Convert browser-local web time input into UTC OnCalendar entries."""
    name, zone = _zoneinfo(timezone_name)
    ref_date = coerce_reference_date(reference_date)
    local_times = _parse_web_time_entries(schedule_value)

    rows: list[ScheduleTimeRow] = []
    utc_times: list[str] = []
    entries: list[str] = []
    seen_entries: set[str] = set()
    for local_value in local_times:
        hour, minute = (int(part) for part in local_value.split(":"))
        local_dt = datetime.combine(ref_date, time(hour, minute), tzinfo=zone)
        utc_value = local_dt.astimezone(timezone.utc).strftime("%H:%M")
        entry = _entry_for_utc_time(utc_value)
        if entry in seen_entries:
            continue
        seen_entries.add(entry)
        utc_times.append(utc_value)
        entries.append(entry)
        rows.append(ScheduleTimeRow(local_time=local_value, utc_time=utc_value, entry=entry))

    local_tuple = tuple(row.local_time for row in rows)
    utc_tuple = tuple(utc_times)
    local_display = _display_times(local_tuple, name)
    utc_display = _display_times(utc_tuple, "UTC")
    return ScheduleTimezoneDTO(
        timezone=name,
        reference_date=ref_date,
        local_times=local_tuple,
        utc_times=utc_tuple,
        on_calendar_entries=tuple(entries),
        local_display=local_display,
        utc_display=utc_display,
        summary=_summary(local_display, utc_display),
        rows=tuple(rows),
    )


def _parse_utc_daily_entry(entry: object) -> str | None:
    match = _UTC_DAILY_RE.fullmatch(str(entry or "").strip())
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second") or "0")
    if hour > 23 or minute > 59 or second > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def display_utc_schedule(
    schedule_entries: list[str] | tuple[str, ...],
    timezone_name: object = "UTC",
    *,
    reference_date: object = None,
) -> ScheduleTimezoneDTO:
    """Project UTC backend OnCalendar entries into a local/UTC display DTO."""
    name, zone = _zoneinfo(timezone_name)
    ref_date = coerce_reference_date(reference_date)
    safe_entries = tuple(_safe_schedule_string(entry) for entry in schedule_entries if entry)

    rows: list[ScheduleTimeRow] = []
    for entry in safe_entries:
        utc_value = _parse_utc_daily_entry(entry)
        if utc_value is None:
            continue
        hour, minute = (int(part) for part in utc_value.split(":"))
        utc_dt = datetime.combine(ref_date, time(hour, minute), tzinfo=timezone.utc)
        local_value = utc_dt.astimezone(zone).strftime("%H:%M")
        rows.append(
            ScheduleTimeRow(
                local_time=local_value,
                utc_time=utc_value,
                entry=_entry_for_utc_time(utc_value),
            )
        )

    if not rows:
        raw_display = "; ".join(safe_entries)
        return ScheduleTimezoneDTO(
            timezone=name,
            reference_date=ref_date,
            local_times=(),
            utc_times=(),
            on_calendar_entries=safe_entries,
            local_display=raw_display,
            utc_display=raw_display,
            summary=raw_display,
            rows=(),
        )

    local_times = tuple(row.local_time for row in rows)
    utc_times = tuple(row.utc_time for row in rows)
    local_display = _display_times(local_times, name)
    utc_display = _display_times(utc_times, "UTC")
    return ScheduleTimezoneDTO(
        timezone=name,
        reference_date=ref_date,
        local_times=local_times,
        utc_times=utc_times,
        on_calendar_entries=tuple(row.entry for row in rows),
        local_display=local_display,
        utc_display=utc_display,
        summary=_summary(local_display, utc_display),
        rows=tuple(rows),
    )
