"""Shared, side-effect-free classification helpers for Reforger server logs."""

from __future__ import annotations

import re

from armactl.redaction import redact_sensitive_text

OPERATIONAL_STATUS_DETAIL_MAX_CHARS = 180
OPERATIONAL_STATUS_DETAIL_MAX_ITEMS = 4

WORKSHOP_ADDON_NOT_FOUND_RE = re.compile(
    r"\bAddon\s+(?P<mod_id>[0-9A-Fa-f]{16})\s*-\s*"
    r"Addon was not found on workshop\b",
    re.IGNORECASE,
)


def all_log_lines(text: str) -> list[str]:
    """Return non-empty lines from an already bounded console-log tail."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def safe_operational_detail(line: str) -> str:
    """Return an operator-safe, bounded status diagnostic line."""
    text = redact_sensitive_text(line).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= OPERATIONAL_STATUS_DETAIL_MAX_CHARS:
        return text
    return f"{text[: OPERATIONAL_STATUS_DETAIL_MAX_CHARS - 1].rstrip()}…"


def safe_operational_details(lines: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Redact and bound the last actionable lines for operator-facing output."""
    details = [
        detail
        for detail in (
            safe_operational_detail(line)
            for line in lines[-OPERATIONAL_STATUS_DETAIL_MAX_ITEMS:]
        )
        if detail
    ]
    return tuple(details)


def line_has_mission_error(line: str) -> bool:
    return (
        "MissionHeader::ReadMissionHeader cannot load" in line
        or "cannot load the resource" in line
    )


def line_has_startup_failure(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Unable to initialize the game",
            "Failed to fetch addon details from workshop API",
            'Can\'t compile "Game" script module',
            "Cannot create game",
            "Addon loading failed",
            "Addon was not found on workshop",
        )
    )


def line_has_workshop_metadata_error(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Failed to fetch addon details from workshop API",
            "WorkshopApi/GetDownloadListS2S",
            "SSL connect error",
            "Addon was not found on workshop",
        )
    )


def line_has_game_destroyed(line: str) -> bool:
    return "Game destroyed." in line or line.rstrip().endswith("Game destroyed")


def line_has_runtime_crash(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "Application crashed!",
            "Generated memory dump",
            "Application hangs (force crash)",
            "double free or corruption",
            "free(): invalid pointer",
            "corrupted size",
            "SIGSEGV",
            "Segmentation fault",
        )
    )
