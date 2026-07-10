"""Stored player log event sessionization service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from armactl import player_log_events
from armactl.web.services import player_registry
from armactl.web.services.player_identity import normalize_reliable_player_id

SESSIONIZATION_CHECKPOINT_SOURCE = "player_log_events"
SESSIONIZATION_CHECKPOINT_PREFIX = "event:"

_CONNECT_EVENT_TYPES = {
    player_log_events.EVENT_TYPE_PLAYER_AUTHENTICATED,
    player_log_events.EVENT_TYPE_PLAYER_UPDATE,
}
_COMBAT_EVENT_TYPES = {
    player_log_events.EVENT_TYPE_KILL,
    player_log_events.EVENT_TYPE_SUICIDE,
    player_log_events.EVENT_TYPE_TEAMKILL,
    player_log_events.EVENT_TYPE_OTHER_DEATH,
}
_CLOSE_EVENT_TYPES = {
    player_log_events.EVENT_TYPE_PLAYER_DISCONNECTED,
    player_log_events.EVENT_TYPE_SERVER_LIFECYCLE,
}


@dataclass(frozen=True)
class PlayerLogSessionizationSummary:
    """Counts-only summary of stored-log sessionization."""

    events_scanned: int = 0
    events_ignored: int = 0
    observations_considered: int = 0
    observations_applied: int = 0
    observations_skipped: int = 0
    observations_ignored: int = 0
    sessions_created: int = 0
    sessions_updated: int = 0
    sessions_closed: int = 0


@dataclass(frozen=True)
class _SessionObservation:
    reliable_id: str
    display_name: str
    source: str
    source_ref: str
    observed_at: str
    confidence: str
    rpl_identity: str = ""
    connection_id: str = ""
    session_player_id: str = ""
    faction: str = ""
    side: str = ""


@dataclass(frozen=True)
class _SessionCloseEvidence:
    source: str
    source_ref: str
    observed_at: str
    confidence: str
    end_reason: str
    rpl_identity: str = ""
    connection_id: str = ""
    be_slot: str = ""
    lifecycle_boundary: bool = False
    event_id: int = 0


@dataclass(frozen=True)
class _CloseApplicationResult:
    applied: bool = False
    skipped: bool = False
    ignored: bool = False
    sessions_closed: int = 0


def sessionize_stored_player_log_events(
    db_path: Path,
) -> PlayerLogSessionizationSummary:
    """Open/update sessions from already stored sanitized player log events.

    This is a stored-log pass only. It does not read live logs, poll RCON/A2S,
    close stale sessions, or calculate session statistics.
    """
    events = player_registry.list_player_log_events_for_sessionization(db_path)
    events_ignored = 0
    observations_considered = 0
    observations_applied = 0
    observations_skipped = 0
    observations_ignored = 0
    sessions_created = 0
    sessions_updated = 0
    sessions_closed = 0

    for event in events:
        observations = tuple(_observations_from_event(event))
        close_evidence = tuple(_close_evidence_from_event(event))
        if not observations and not close_evidence:
            events_ignored += 1
            continue

        for observation in observations:
            observations_considered += 1
            reliable_id = normalize_reliable_player_id(observation.reliable_id)
            if not reliable_id:
                observations_ignored += 1
                continue
            if _should_skip_observation(db_path, reliable_id, observation, event):
                observations_skipped += 1
                continue

            result = player_registry.observe_player_session(
                db_path,
                reliable_id=reliable_id,
                display_name=observation.display_name,
                source=observation.source,
                observed_at=observation.observed_at,
                source_ref=observation.source_ref,
                confidence=observation.confidence,
                rpl_identity=observation.rpl_identity,
                connection_id=observation.connection_id,
                session_player_id=observation.session_player_id,
                faction=observation.faction,
                side=observation.side,
                scanner_checkpoint_source=SESSIONIZATION_CHECKPOINT_SOURCE,
                scanner_checkpoint_ref=_checkpoint_ref(event.event_id),
                scanner_checkpoint_at=observation.observed_at,
            )
            if result.ignored_count:
                observations_ignored += result.ignored_count
                continue
            if not result.written:
                observations_skipped += 1
                continue
            observations_applied += 1
            if result.created:
                sessions_created += 1
            elif result.updated:
                sessions_updated += 1

        for evidence in close_evidence:
            observations_considered += 1
            result = _apply_close_evidence(db_path, evidence)
            if result.ignored:
                observations_ignored += 1
                continue
            if result.applied:
                observations_applied += 1
                sessions_closed += result.sessions_closed
                continue
            observations_skipped += 1

    return PlayerLogSessionizationSummary(
        events_scanned=len(events),
        events_ignored=events_ignored,
        observations_considered=observations_considered,
        observations_applied=observations_applied,
        observations_skipped=observations_skipped,
        observations_ignored=observations_ignored,
        sessions_created=sessions_created,
        sessions_updated=sessions_updated,
        sessions_closed=sessions_closed,
    )


def _observations_from_event(
    event: player_registry.PlayerLogEventRecord,
) -> tuple[_SessionObservation, ...]:
    observed_at = _event_observed_at(event)
    if not observed_at:
        return ()
    if event.event_type in _CONNECT_EVENT_TYPES:
        return (
            _SessionObservation(
                reliable_id=event.player_id,
                display_name=event.player_name,
                source=event.source,
                source_ref=event.source_ref,
                observed_at=observed_at,
                confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
                rpl_identity=event.rpl_identity,
                connection_id=event.connection_id,
                session_player_id=event.session_player_id,
            ),
        )

    if event.event_type == player_log_events.EVENT_TYPE_FACTION_JOIN:
        return (
            _SessionObservation(
                reliable_id=event.player_id,
                display_name=event.player_name,
                source=event.source,
                source_ref=event.source_ref,
                observed_at=observed_at,
                confidence=player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
                session_player_id=event.session_player_id,
                faction=event.faction_resource or event.player_faction,
                side=event.player_faction,
            ),
        )

    if event.event_type in _COMBAT_EVENT_TYPES:
        return _combat_observations(event, observed_at=observed_at)

    return ()


def _close_evidence_from_event(
    event: player_registry.PlayerLogEventRecord,
) -> tuple[_SessionCloseEvidence, ...]:
    observed_at = _event_observed_at(event)
    if not observed_at:
        return ()
    if event.event_type not in _CLOSE_EVENT_TYPES:
        return ()
    if event.event_type == player_log_events.EVENT_TYPE_PLAYER_DISCONNECTED:
        return (
            _SessionCloseEvidence(
                source=event.source,
                source_ref=event.source_ref,
                observed_at=observed_at,
                confidence=_session_close_confidence(event.confidence),
                end_reason=player_registry.PLAYER_SESSION_END_REASON_DISCONNECT,
                rpl_identity=event.rpl_identity,
                connection_id=event.connection_id,
                be_slot=event.be_slot,
                event_id=event.event_id,
            ),
        )

    if event.event_type == player_log_events.EVENT_TYPE_SERVER_LIFECYCLE:
        return (
            _SessionCloseEvidence(
                source=player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
                source_ref=event.source_ref,
                observed_at=observed_at,
                confidence=_session_close_confidence(event.confidence),
                end_reason=player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
                lifecycle_boundary=True,
                event_id=event.event_id,
            ),
        )

    return ()


def _apply_close_evidence(
    db_path: Path,
    evidence: _SessionCloseEvidence,
) -> _CloseApplicationResult:
    if evidence.lifecycle_boundary:
        return _apply_lifecycle_close(db_path, evidence)
    return _apply_correlated_close(db_path, evidence)


def _apply_lifecycle_close(
    db_path: Path,
    evidence: _SessionCloseEvidence,
) -> _CloseApplicationResult:
    boundary_recorded = player_registry.record_player_session_lifecycle_boundary(
        db_path,
        boundary_at=evidence.observed_at,
        source=evidence.source,
        source_ref=evidence.source_ref,
        confidence=evidence.confidence,
    )
    sessions = player_registry.list_open_player_sessions(db_path)

    closed_count = 0
    for session in sessions:
        checkpoint_event_id = _checkpoint_event_id(session)
        if checkpoint_event_id is not None and checkpoint_event_id >= evidence.event_id:
            continue
        if _is_older_than(evidence.observed_at, session.open_observed_at):
            continue
        result = player_registry.close_player_session(
            db_path,
            reliable_id=session.reliable_id,
            close_observed_at=evidence.observed_at,
            source=evidence.source,
            source_ref=evidence.source_ref,
            confidence=evidence.confidence,
            end_reason=evidence.end_reason,
        )
        if result.closed:
            closed_count += 1

    if not closed_count and not boundary_recorded:
        return _CloseApplicationResult(skipped=True)
    return _CloseApplicationResult(applied=True, sessions_closed=closed_count)


def _apply_correlated_close(
    db_path: Path,
    evidence: _SessionCloseEvidence,
) -> _CloseApplicationResult:
    if not _has_correlation_evidence(evidence):
        return _CloseApplicationResult(ignored=True)

    session = _matched_open_session_by_correlation(db_path, evidence)
    if session is None:
        return _CloseApplicationResult(skipped=True)

    checkpoint_event_id = _checkpoint_event_id(session)
    if checkpoint_event_id is not None and checkpoint_event_id >= evidence.event_id:
        return _CloseApplicationResult(skipped=True)
    if _is_older_than(evidence.observed_at, session.open_observed_at):
        return _CloseApplicationResult(skipped=True)

    result = player_registry.close_player_session(
        db_path,
        reliable_id=session.reliable_id,
        close_observed_at=evidence.observed_at,
        source=evidence.source,
        source_ref=evidence.source_ref,
        confidence=evidence.confidence,
        end_reason=evidence.end_reason,
    )
    if not result.closed:
        return _CloseApplicationResult(skipped=True)
    return _CloseApplicationResult(applied=True, sessions_closed=1)


def _matched_open_session_by_correlation(
    db_path: Path,
    evidence: _SessionCloseEvidence,
) -> player_registry.PlayerSessionRecord | None:
    sessions = player_registry.list_open_player_sessions(db_path)
    for field_name in ("rpl_identity", "connection_id", "be_slot"):
        expected = _correlation_value(getattr(evidence, field_name))
        if not expected:
            continue
        matches = [
            session
            for session in sessions
            if _correlation_value(getattr(session, field_name)) == expected
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


def _has_correlation_evidence(evidence: _SessionCloseEvidence) -> bool:
    return any(
        _correlation_value(value)
        for value in (evidence.rpl_identity, evidence.connection_id, evidence.be_slot)
    )


def _session_close_confidence(value: str) -> str:
    if value in player_registry.PLAYER_SESSION_CONFIDENCES:
        return value
    return player_registry.PLAYER_SESSION_CONFIDENCE_LOW


def _correlation_value(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text == "***" or "/" in text or "\\" in text:
        return ""
    return text


def _combat_observations(
    event: player_registry.PlayerLogEventRecord,
    *,
    observed_at: str,
) -> tuple[_SessionObservation, ...]:
    observations: list[_SessionObservation] = []
    seen_ids: set[str] = set()
    victim_id = normalize_reliable_player_id(event.victim_id or event.player_id)
    if victim_id:
        observations.append(
            _SessionObservation(
                reliable_id=victim_id,
                display_name=event.victim_name or event.player_name,
                source=event.source,
                source_ref=event.source_ref,
                observed_at=observed_at,
                confidence=player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
                session_player_id=(
                    event.victim_session_player_id or event.session_player_id
                ),
                faction=event.victim_faction or event.player_faction,
            )
        )
        seen_ids.add(victim_id)

    instigator_id = normalize_reliable_player_id(event.instigator_id)
    if instigator_id and instigator_id not in seen_ids and not event.ai_instigator:
        observations.append(
            _SessionObservation(
                reliable_id=instigator_id,
                display_name=event.instigator_name,
                source=event.source,
                source_ref=event.source_ref,
                observed_at=observed_at,
                confidence=player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
                session_player_id=event.instigator_session_player_id,
                faction=event.instigator_faction,
            )
        )

    return tuple(observations)


def _should_skip_observation(
    db_path: Path,
    reliable_id: str,
    observation: _SessionObservation,
    event: player_registry.PlayerLogEventRecord,
) -> bool:
    session = player_registry.get_open_player_session(db_path, reliable_id)
    if session is None:
        return _closed_session_covers_observation(
            db_path, reliable_id, observation, event
        )

    checkpoint_event_id = _checkpoint_event_id(session)
    if checkpoint_event_id is not None and checkpoint_event_id >= event.event_id:
        return True

    if (
        session.last_seen_source == observation.source
        and session.last_seen_source_ref == observation.source_ref
        and session.last_seen_at == observation.observed_at
    ):
        return True

    return _is_older_than(observation.observed_at, session.last_seen_at)


def _closed_session_covers_observation(
    db_path: Path,
    reliable_id: str,
    observation: _SessionObservation,
    event: player_registry.PlayerLogEventRecord,
) -> bool:
    sessions = player_registry.list_player_sessions_for_reliable_id(db_path, reliable_id)
    for session in sessions:
        if session.status != player_registry.PLAYER_SESSION_STATUS_CLOSED:
            continue
        checkpoint_event_id = _checkpoint_event_id(session)
        if checkpoint_event_id is not None and checkpoint_event_id >= event.event_id:
            return True
        if _session_has_observation(session, observation):
            return True
        if _session_time_contains(session, observation.observed_at):
            return True
    return False


def _session_has_observation(
    session: player_registry.PlayerSessionRecord,
    observation: _SessionObservation,
) -> bool:
    return (
        session.open_source == observation.source
        and session.open_source_ref == observation.source_ref
        and session.open_observed_at == observation.observed_at
    ) or (
        session.last_seen_source == observation.source
        and session.last_seen_source_ref == observation.source_ref
        and session.last_seen_at == observation.observed_at
    )


def _session_time_contains(
    session: player_registry.PlayerSessionRecord,
    observed_at: str,
) -> bool:
    if not observed_at or not session.open_observed_at or not session.close_observed_at:
        return False
    return not _is_older_than(
        observed_at,
        session.open_observed_at,
    ) and not _is_older_than(session.close_observed_at, observed_at)


def _checkpoint_ref(event_id: int) -> str:
    return f"{SESSIONIZATION_CHECKPOINT_PREFIX}{event_id}"


def _checkpoint_event_id(session: player_registry.PlayerSessionRecord) -> int | None:
    if session.scanner_checkpoint_source != SESSIONIZATION_CHECKPOINT_SOURCE:
        return None
    ref = session.scanner_checkpoint_ref
    if not ref.startswith(SESSIONIZATION_CHECKPOINT_PREFIX):
        return None
    try:
        event_id = int(ref.removeprefix(SESSIONIZATION_CHECKPOINT_PREFIX))
    except ValueError:
        return None
    return event_id if event_id > 0 else None


def _event_observed_at(event: player_registry.PlayerLogEventRecord) -> str:
    return event.occurred_at or event.observed_at


def _is_older_than(left: str, right: str) -> bool:
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is not None and right_dt is not None:
        return left_dt < right_dt
    if left and right:
        return left < right
    return False


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
