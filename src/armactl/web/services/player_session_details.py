"""Typed sanitized DTO foundation for player-session search and detail."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl.web.services import (
    player_registry,
    player_session_stats,
)

PLAYER_SESSION_DETAIL_STATUS_OK = "ok"
PLAYER_SESSION_DETAIL_STATUS_INVALID_ID = "invalid_id"
PLAYER_SESSION_DETAIL_STATUS_NOT_FOUND = "not_found"
PLAYER_SESSION_DETAIL_STATUS_UNAVAILABLE = "unavailable"
PLAYER_SESSION_TIMELINE_STATUS_INVALID_CURSOR = "invalid_cursor"
PLAYER_SESSION_TIMELINE_STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PlayerSessionSearchItem:
    """Safe stored-session fields for list/search consumers."""

    session_id: int
    reliable_id: str
    name_at_open: str
    name_last: str
    status: str
    evidence_time: str
    first_evidence_at: str
    last_evidence_at: str
    close_evidence_at: str | None
    open_source: str
    last_evidence_source: str
    close_source: str | None
    open_confidence: str
    last_evidence_confidence: str
    close_confidence: str | None
    close_reason: str | None
    reconnect_merge_count: int
    last_reconnect_at: str | None
    last_gameplay_evidence_at: str | None
    last_gameplay_source: str | None
    last_gameplay_confidence: str | None


@dataclass(frozen=True)
class PlayerSessionSearchResult:
    """Controlled bounded search DTO with safe keyset metadata."""

    status: str
    items: tuple[PlayerSessionSearchItem, ...] = ()
    next_cursor: player_registry.PlayerSessionCursor | None = None
    limit: int = player_registry.DEFAULT_PLAYER_SESSION_LIST_LIMIT


@dataclass(frozen=True)
class PlayerSessionDetail:
    """Safe fields for a future authenticated server-rendered detail page."""

    session_id: int
    reliable_id: str
    name_at_open: str
    name_last: str
    status: str
    first_evidence_at: str
    last_evidence_at: str
    close_evidence_at: str | None
    open_source: str
    last_evidence_source: str
    close_source: str | None
    open_confidence: str
    last_evidence_confidence: str
    close_confidence: str | None
    close_reason: str | None
    reconnect_merge_count: int
    last_reconnect_at: str | None
    last_reconnect_close_evidence_at: str | None
    last_reconnect_close_reason: str | None
    last_gameplay_evidence_at: str | None
    last_gameplay_source: str | None
    last_gameplay_confidence: str | None
    stats: player_session_stats.PlayerSessionStats


@dataclass(frozen=True)
class PlayerSessionDetailResult:
    """Controlled detail lookup result without exception or storage details."""

    status: str
    detail: PlayerSessionDetail | None = None


@dataclass(frozen=True)
class PlayerSessionTimelineItem:
    """Safe high-signal event fields for a future session detail timeline."""

    event_id: int
    event_type: str
    occurred_at: str
    source: str
    confidence: str
    player_name: str | None
    player_faction: str | None
    victim_name: str | None
    victim_faction: str | None
    instigator_name: str | None
    instigator_faction: str | None
    teamkill: bool | None
    suicide: bool | None
    ai_instigator: bool | None
    damage_type: str | None
    hit_zone: str | None
    distance_m: float | None


@dataclass(frozen=True)
class PlayerSessionTimelineResult:
    """Controlled bounded timeline result without raw evidence or identifiers."""

    status: str
    items: tuple[PlayerSessionTimelineItem, ...] = ()
    next_cursor: player_registry.PlayerLogEventCursor | None = None
    limit: int = player_registry.DEFAULT_PLAYER_HISTORY_EVENT_LIMIT
    unavailable_reason: str | None = None


def _optional(value: str) -> str | None:
    return value or None


def _search_item(
    session: player_registry.PlayerSessionRecord,
) -> PlayerSessionSearchItem:
    evidence_time = (
        session.close_observed_at
        or session.last_seen_at
        or session.open_observed_at
    )
    return PlayerSessionSearchItem(
        session_id=session.session_id,
        reliable_id=session.reliable_id,
        name_at_open=session.name_at_open,
        name_last=session.name_last,
        status=session.status,
        evidence_time=evidence_time,
        first_evidence_at=session.open_observed_at,
        last_evidence_at=session.last_seen_at,
        close_evidence_at=_optional(session.close_observed_at),
        open_source=session.open_source,
        last_evidence_source=session.last_seen_source,
        close_source=_optional(session.close_source),
        open_confidence=session.open_confidence,
        last_evidence_confidence=session.last_seen_confidence,
        close_confidence=_optional(session.close_confidence),
        close_reason=_optional(session.end_reason),
        reconnect_merge_count=max(0, session.reconnect_merge_count),
        last_reconnect_at=_optional(session.last_reconnect_at),
        last_gameplay_evidence_at=_optional(session.last_gameplay_evidence_at),
        last_gameplay_source=_optional(session.last_gameplay_source),
        last_gameplay_confidence=_optional(session.last_gameplay_confidence),
    )


def _timeline_item(
    event: player_registry.PlayerLogEventRecord,
) -> PlayerSessionTimelineItem:
    return PlayerSessionTimelineItem(
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        source=event.source,
        confidence=event.confidence,
        player_name=_optional(event.player_name),
        player_faction=_optional(event.player_faction),
        victim_name=_optional(event.victim_name),
        victim_faction=_optional(event.victim_faction),
        instigator_name=_optional(event.instigator_name),
        instigator_faction=_optional(event.instigator_faction),
        teamkill=event.teamkill,
        suicide=event.suicide,
        ai_instigator=event.ai_instigator,
        damage_type=_optional(event.damage_type),
        hit_zone=_optional(event.hit_zone),
        distance_m=event.distance_m,
    )


def search_player_sessions(
    db_path: Path,
    *,
    limit: object = player_registry.DEFAULT_PLAYER_SESSION_LIST_LIMIT,
    reliable_id: object = "",
    query: object = "",
    status: object = "",
    end_reason: object = "",
    source: object = "",
    before_time: object = "",
    before_session_id: object = "",
    from_time: object = "",
    to_time: object = "",
) -> PlayerSessionSearchResult:
    """Return a safe typed projection of the registry keyset query."""
    result = player_registry.query_player_sessions(
        db_path,
        limit=limit,
        reliable_id=reliable_id,
        query=query,
        status=status,
        end_reason=end_reason,
        source=source,
        before_time=before_time,
        before_session_id=before_session_id,
        from_time=from_time,
        to_time=to_time,
    )
    return PlayerSessionSearchResult(
        status=result.status,
        items=tuple(_search_item(session) for session in result.sessions),
        next_cursor=result.next_cursor,
        limit=result.limit,
    )


def load_player_session_detail(
    db_path: Path,
    session_id: object,
    *,
    now_at: str | None = None,
) -> PlayerSessionDetailResult:
    """Return one safe detail DTO from query-only registry/stat reads."""
    result = player_registry.get_player_session_readonly(db_path, session_id)
    if result.status != player_registry.PLAYER_SESSION_READ_STATUS_OK:
        return PlayerSessionDetailResult(status=result.status)
    session = result.session
    if session is None:
        return PlayerSessionDetailResult(
            status=PLAYER_SESSION_DETAIL_STATUS_UNAVAILABLE
        )

    stats = player_session_stats.load_stored_session_stats(
        db_path,
        session,
        now_at=now_at,
    )
    return PlayerSessionDetailResult(
        status=PLAYER_SESSION_DETAIL_STATUS_OK,
        detail=PlayerSessionDetail(
            session_id=session.session_id,
            reliable_id=session.reliable_id,
            name_at_open=session.name_at_open,
            name_last=session.name_last,
            status=session.status,
            first_evidence_at=session.open_observed_at,
            last_evidence_at=session.last_seen_at,
            close_evidence_at=_optional(session.close_observed_at),
            open_source=session.open_source,
            last_evidence_source=session.last_seen_source,
            close_source=_optional(session.close_source),
            open_confidence=session.open_confidence,
            last_evidence_confidence=session.last_seen_confidence,
            close_confidence=_optional(session.close_confidence),
            close_reason=_optional(session.end_reason),
            reconnect_merge_count=max(0, session.reconnect_merge_count),
            last_reconnect_at=_optional(session.last_reconnect_at),
            last_reconnect_close_evidence_at=_optional(
                session.last_reconnect_close_observed_at
            ),
            last_reconnect_close_reason=_optional(
                session.last_reconnect_close_reason
            ),
            last_gameplay_evidence_at=_optional(
                session.last_gameplay_evidence_at
            ),
            last_gameplay_source=_optional(session.last_gameplay_source),
            last_gameplay_confidence=_optional(
                session.last_gameplay_confidence
            ),
            stats=stats,
        ),
    )


def load_player_session_timeline(
    db_path: Path,
    session_id: object,
    *,
    limit: object = player_registry.DEFAULT_PLAYER_HISTORY_EVENT_LIMIT,
    before_time: object = "",
    before_event_id: object = "",
    now_at: str | None = None,
) -> PlayerSessionTimelineResult:
    """Return one safe keyset event page inside a proven stored-session window."""
    detail_result = load_player_session_detail(
        db_path,
        session_id,
        now_at=now_at,
    )
    if detail_result.status != PLAYER_SESSION_DETAIL_STATUS_OK:
        return PlayerSessionTimelineResult(status=detail_result.status)
    detail = detail_result.detail
    if detail is None:
        return PlayerSessionTimelineResult(
            status=PLAYER_SESSION_TIMELINE_STATUS_UNAVAILABLE,
        )
    stats = detail.stats
    if (
        not stats.stats_available
        or not stats.window_started_at
        or not stats.window_ended_at
    ):
        return PlayerSessionTimelineResult(
            status=PLAYER_SESSION_TIMELINE_STATUS_UNAVAILABLE,
            unavailable_reason=stats.stats_unavailable_reason,
        )
    result = player_registry.query_player_session_events(
        db_path,
        reliable_id=detail.reliable_id,
        window_started_at=stats.window_started_at,
        window_ended_at=stats.window_ended_at,
        limit=limit,
        before_time=before_time,
        before_event_id=before_event_id,
    )
    status = result.status
    if status == player_registry.PLAYER_SESSION_EVENT_QUERY_STATUS_INVALID_CURSOR:
        status = PLAYER_SESSION_TIMELINE_STATUS_INVALID_CURSOR
    elif status != player_registry.PLAYER_SESSION_EVENT_QUERY_STATUS_OK:
        status = PLAYER_SESSION_TIMELINE_STATUS_UNAVAILABLE
    return PlayerSessionTimelineResult(
        status=status,
        items=tuple(_timeline_item(event) for event in result.events),
        next_cursor=result.next_cursor,
        limit=result.limit,
    )
