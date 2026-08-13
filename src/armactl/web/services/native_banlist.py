"""Read-only web orchestration for the native Reforger ban list."""

from __future__ import annotations

from armactl import rcon
from armactl.web.services.native_moderation import moderation_lock


def parse_page_parameter(value: str) -> int | None:
    """Return one bounded page number or None for an invalid web parameter."""
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 3
        or not normalized.isascii()
        or not normalized.isdigit()
    ):
        return None
    page = int(normalized)
    try:
        return rcon.normalize_native_ban_page(page)
    except ValueError:
        return None


def load_native_ban_list(
    instance: str,
    *,
    page: int,
) -> rcon.NativeBanListResult:
    """Read one native page under the moderation operation lock."""
    requested_page = rcon.normalize_native_ban_page(page)
    with moderation_lock(instance):
        return rcon.query_native_ban_list(instance, page=requested_page)
