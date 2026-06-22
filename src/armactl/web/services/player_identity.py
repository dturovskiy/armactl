"""Player identity normalization rules for web moderation workflows."""

from __future__ import annotations

import re

from armactl import admins_manager
from armactl.redaction import redact_sensitive_text

PLAYER_QUERY_MAX_LENGTH = 120
PLAYER_TEXT_MAX_LENGTH = 160
PLAYER_GUID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")


def safe_player_text(value: object, *, max_length: int = PLAYER_TEXT_MAX_LENGTH) -> str:
    """Return UI/storage-safe player text without preserving secret-looking data."""
    text = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def normalize_player_query(value: object) -> str:
    """Normalize player search input for server-side filtering."""
    return safe_player_text(value, max_length=PLAYER_QUERY_MAX_LENGTH)


def normalize_admin_reference(value: object) -> str:
    """Return a server-admin-safe reference, or empty when it cannot be used."""
    candidate = str(value or "").strip()
    if admins_manager.STEAM_ID64_RE.fullmatch(candidate):
        return candidate
    if admins_manager.IDENTITY_ID_RE.fullmatch(candidate):
        return candidate.lower()
    return ""


def normalize_reliable_player_id(value: object) -> str:
    """Return a persistable player identity, or empty for unreliable IDs."""
    candidate = str(value or "").strip()
    admin_reference = normalize_admin_reference(candidate)
    if admin_reference:
        return admin_reference
    if PLAYER_GUID_RE.fullmatch(candidate):
        return candidate
    return ""
