"""Pure parser for structured player events from sanitized game log lines."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

EVENT_TYPE_PLAYER_AUTHENTICATED = "player_authenticated"
EVENT_TYPE_PLAYER_UPDATE = "player_update"
EVENT_TYPE_FACTION_JOIN = "faction_join"
EVENT_TYPE_PLAYER_DISCONNECTED = "player_disconnected"
EVENT_TYPE_SERVER_LIFECYCLE = "server_lifecycle"
EVENT_TYPE_KILL = "kill"
EVENT_TYPE_SUICIDE = "suicide"
EVENT_TYPE_TEAMKILL = "teamkill"
EVENT_TYPE_OTHER_DEATH = "other_death"
EVENT_TYPE_COMBAT_HINT = "combat_hint"

SOURCE_BACKEND_AUTH = "backend_authenticated_player"
SOURCE_NETWORK_PLAYER_UPDATE = "network_player_update"
SOURCE_SCRIPT_FACTION_JOIN = "script_faction_join"
SOURCE_RPL_DISCONNECT = "rpl_disconnect"
SOURCE_NETWORK_DISCONNECT = "network_disconnect"
SOURCE_BATTLEYE_DISCONNECT = "battleye_disconnect"
SOURCE_SERVER_LIFECYCLE = "server_lifecycle"
SOURCE_SCRIPT_KILL = "script_kill"
SOURCE_SERVER_ADMIN_TOOLS_KILL = "serveradmintools_player_killed"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

EVENT_TIME_SOURCE_LOG_PREFIX_WITH_DATE = "log_prefix_with_date"
EVENT_TIME_SOURCE_LOG_PREFIX_WITHOUT_DATE = "log_prefix_without_date"
EVENT_TIME_SOURCE_CALLER_OCCURRED_AT = "caller_occurred_at"
EVENT_TIME_SOURCE_CALLER_OBSERVED_AT = "caller_observed_at"
EVENT_TIME_SOURCE_UNAVAILABLE = "unavailable"
EVENT_TIME_CONFIDENCE_DERIVED = "derived"
EVENT_TIME_CONFIDENCE_EXACT = "exact"
EVENT_TIME_CONFIDENCE_AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class PlayerLogEvent:
    """Structured player event parsed from one bounded log source reference."""

    event_type: str
    source: str
    confidence: str
    occurred_at: str | None = None
    observed_at: str | None = None
    raw_timestamp: str | None = None
    time_source: str | None = None
    time_confidence: str | None = None
    player_id: str | None = None
    player_name: str | None = None
    session_player_id: str | None = None
    rpl_identity: str | None = None
    connection_id: str | None = None
    be_slot: str | None = None
    player_faction: str | None = None
    faction_resource: str | None = None
    victim_id: str | None = None
    victim_name: str | None = None
    victim_session_player_id: str | None = None
    victim_faction: str | None = None
    instigator_id: str | None = None
    instigator_name: str | None = None
    instigator_session_player_id: str | None = None
    instigator_faction: str | None = None
    teamkill: bool | None = None
    suicide: bool | None = None
    ai_instigator: bool | None = None
    damage_type: str | None = None
    hit_zone: str | None = None
    distance_m: float | None = None
    raw_source_ref: str | None = None


@dataclass(frozen=True)
class _ParsedPlayerRef:
    name: str
    session_player_id: str | None = None
    player_id: str | None = None


_AUTHENTICATED_RE = re.compile(
    r"\bBACKEND\s*:\s*Authenticated player:?\s+"
    r"rplIdentity=(?P<rpl_identity>\S+)\s+"
    r"identityId=(?P<player_id>\S+)\s+"
    r"name=(?P<player_name>.+?)\s*$"
)

_NETWORK_UPDATE_RE = re.compile(
    r"\bNETWORK\s*:\s*### Updating player:\s*"
    r"PlayerId=(?P<session_player_id>[^,]+),\s*"
    r"Name=(?P<player_name>.*?),\s*"
    r"rplIdentity=(?P<rpl_identity>[^,]+),\s*"
    r"IdentityId=(?P<player_id>\S+)\s*$"
)

_FACTION_JOIN_RE = re.compile(
    r"\bSCRIPT\s*:\s*INFO:\s*Faction:\s*player\s+"
    r"(?P<player_name>.+?)\s+"
    r"\(playerID\s*=\s*(?P<session_player_id>[^|)]+)\s*"
    r"\|\s*UUID\s*=\s*(?P<player_id>[^)]+)\)\s+"
    r"has joined faction\s+#(?P<faction_resource>\S+)\s+"
    r"\((?P<player_faction>[^)]+)\)\s*$"
)

_RPL_DISCONNECT_RE = re.compile(
    r"\bRPL\s*:\s*ServerImpl event:\s*disconnected\s*"
    r"\(identity=(?P<rpl_identity>[^)]+)\)"
    r"(?:,\s*group=[^,]+)?"
    r"(?:,\s*reason=.*?)?\s*$",
    re.IGNORECASE,
)

_NETWORK_DISCONNECT_RE = re.compile(
    r"\bNETWORK\s*:\s*Player disconnected:\s*"
    r"connectionID=(?P<connection_id>[^,\s]+)\s*$",
    re.IGNORECASE,
)

_BATTLEYE_DISCONNECT_RE = re.compile(
    r"\bDEFAULT\s*:\s*BattlEye Server:\s*"
    r"'Player\s+#(?P<be_slot>\d+)\s+(?P<player_name>.*?)\s+disconnected'\s*$",
    re.IGNORECASE,
)

_PERSISTENCE_SHUTDOWN_RE = re.compile(
    r"\bDEFAULT\s*:\s*\[PERSISTENCE\]\s*Save\s*\(SHUTDOWN\)\s*started\.?\s*$",
    re.IGNORECASE,
)

_SERVICE_STOP_RE = re.compile(
    r"\b(?:Stopping|Stopped)\s+"
    r"(?:Arma\s+Reforger(?:\s+Dedicated\s+Server)?|armareforger\.service)\b",
    re.IGNORECASE,
)

_PLAYER_REF_RE = re.compile(
    r"^(?P<name>.+?)\s+"
    r"\(playerID\s*=\s*(?P<session_player_id>[^|)]+)\s*"
    r"\|\s*UUID\s*=\s*(?P<player_id>[^)]+)\)$"
)

_KILL_ENEMY_RE = re.compile(
    r"\bSCRIPT\s*:\s*INFO:\s*KILL ENEMY:\s*"
    r"(?P<victim_name>.+?)\s+"
    r"\(playerID\s*=\s*(?P<victim_session_player_id>[^|)]+)\s*"
    r"\|\s*UUID\s*=\s*(?P<victim_id>[^)]+)\)\s+"
    r"from\s+(?P<victim_faction>.+?)\s+faction\s+"
    r"at\s+(?P<context>.+?)\s+was killed by\s+"
    r"(?P<instigator_ref>.+?)\s+from\s+"
    r"(?P<instigator_faction>.+?)\s+faction"
    r"(?P<instigator_context>.*?)\.\s+"
    r"With last inflicted damage type\s+(?P<damage_type>.+?)\s+"
    r"to the '(?P<hit_zone>[^']*)' hit zone\s*$"
)

_KILL_TK_RE = re.compile(
    r"\bSCRIPT\s*:\s*INFO:\s*KILL TK:\s*"
    r"(?P<victim_name>.+?)\s+"
    r"\(playerID\s*=\s*(?P<victim_session_player_id>[^|)]+)\s*"
    r"\|\s*UUID\s*=\s*(?P<victim_id>[^)]+)\)\s+"
    r"from\s+(?P<victim_faction>.+?)\s+faction\s+"
    r"at\s+(?P<context>.+?)\s+was killed by\s+"
    r"(?P<instigator_ref>.+?)\s+from\s+"
    r"(?P<instigator_faction>.+?)\s+faction"
    r"(?P<instigator_context>.*?)\.\s+"
    r"With last inflicted damage type\s+(?P<damage_type>.+?)\s+"
    r"to the '(?P<hit_zone>[^']*)' hit zone\s*$"
)

_KILL_SUICIDE_RE = re.compile(
    r"\bSCRIPT\s*:\s*INFO:\s*KILL SUICIDE:\s*"
    r"(?P<victim_name>.+?)\s+"
    r"\(playerID\s*=\s*(?P<victim_session_player_id>[^|)]+)\s*"
    r"\|\s*UUID\s*=\s*(?P<victim_id>[^)]+)\)\s+"
    r"from\s+(?P<victim_faction>.+?)\s+faction\s+"
    r"at\s+(?P<context>.+?)\s+"
    r"(?:killed (?:himself|herself|themselves)|committed suicide)!\s+"
    r"With last inflicted damage type\s+(?P<damage_type>.+?)\s+"
    r"to the '(?P<hit_zone>[^']*)' hit zone\s*$"
)

_KILL_OTHER_DEATH_RE = re.compile(
    r"\bSCRIPT\s*:\s*INFO:\s*KILL OTHER_DEATH:\s*"
    r"(?P<victim_name>.+?)\s+"
    r"\(playerID\s*=\s*(?P<victim_session_player_id>[^|)]+)\s*"
    r"\|\s*UUID\s*=\s*(?P<victim_id>[^)]+)\)\s+"
    r"from\s+(?P<victim_faction>.+?)\s+faction\s+"
    r"at\s+(?P<context><[^>]+>)(?P<death_context>.*?)"
    r"(?:\s+With last inflicted damage type\s+(?P<damage_type>.+?)\s+"
    r"to the '(?P<hit_zone>[^']*)' hit zone)?\s*$"
)

_SAT_PLAYER_KILLED_RE = re.compile(
    r"\bSCRIPT\s*:\s*ServerAdminTools\s*\|\s*"
    r"Event\s+serveradmintools_player_killed\s*\|\s*"
    r"player:\s*(?P<victim_name>.*?),\s*"
    r"instigator:\s*(?P<instigator_name>.*?),\s*"
    r"friendly:\s*(?P<friendly>true|false)\s*$",
    re.IGNORECASE,
)

_DISTANCE_RE = re.compile(
    r"(?:\bdistance\s*[:=]?\s*|\[)"
    r"(?P<distance>\d+(?:\.\d+)?)\s*m(?:\b|\s+away)",
    re.I,
)

_LOG_TIME_PREFIX_RE = re.compile(
    r"^\s*(?P<timestamp>\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,6})?)\b"
)


def parse_player_log_event(
    line: str,
    *,
    occurred_at: str | None = None,
    observed_at: str | None = None,
    raw_timestamp: str | None = None,
    time_source: str | None = None,
    time_confidence: str | None = None,
    raw_source_ref: str | None = None,
) -> PlayerLogEvent | None:
    """Parse one log line into a bounded DTO, or return ``None`` when unmatched."""
    text = line.strip()
    if not text:
        return None
    raw_timestamp = raw_timestamp or _raw_timestamp_prefix(text)

    if match := _AUTHENTICATED_RE.search(text):
        return PlayerLogEvent(
            event_type=EVENT_TYPE_PLAYER_AUTHENTICATED,
            source=SOURCE_BACKEND_AUTH,
            confidence=CONFIDENCE_HIGH,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            player_id=_clean(match.group("player_id")),
            player_name=_clean(match.group("player_name")),
            rpl_identity=_clean(match.group("rpl_identity")),
            raw_source_ref=raw_source_ref,
        )

    if match := _NETWORK_UPDATE_RE.search(text):
        return PlayerLogEvent(
            event_type=EVENT_TYPE_PLAYER_UPDATE,
            source=SOURCE_NETWORK_PLAYER_UPDATE,
            confidence=CONFIDENCE_HIGH,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            player_id=_clean(match.group("player_id")),
            player_name=_clean(match.group("player_name")),
            session_player_id=_clean(match.group("session_player_id")),
            rpl_identity=_clean(match.group("rpl_identity")),
            raw_source_ref=raw_source_ref,
        )

    if match := _FACTION_JOIN_RE.search(text):
        return PlayerLogEvent(
            event_type=EVENT_TYPE_FACTION_JOIN,
            source=SOURCE_SCRIPT_FACTION_JOIN,
            confidence=CONFIDENCE_HIGH,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            player_id=_clean(match.group("player_id")),
            player_name=_clean(match.group("player_name")),
            session_player_id=_clean(match.group("session_player_id")),
            player_faction=_clean(match.group("player_faction")),
            faction_resource=_clean(match.group("faction_resource")),
            raw_source_ref=raw_source_ref,
        )

    if match := _RPL_DISCONNECT_RE.search(text):
        return PlayerLogEvent(
            event_type=EVENT_TYPE_PLAYER_DISCONNECTED,
            source=SOURCE_RPL_DISCONNECT,
            confidence=CONFIDENCE_MEDIUM,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            rpl_identity=_clean(match.group("rpl_identity")),
            raw_source_ref=raw_source_ref,
        )

    if match := _NETWORK_DISCONNECT_RE.search(text):
        return PlayerLogEvent(
            event_type=EVENT_TYPE_PLAYER_DISCONNECTED,
            source=SOURCE_NETWORK_DISCONNECT,
            confidence=CONFIDENCE_MEDIUM,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            connection_id=_clean(match.group("connection_id")),
            raw_source_ref=raw_source_ref,
        )

    if match := _BATTLEYE_DISCONNECT_RE.search(text):
        return PlayerLogEvent(
            event_type=EVENT_TYPE_PLAYER_DISCONNECTED,
            source=SOURCE_BATTLEYE_DISCONNECT,
            confidence=CONFIDENCE_LOW,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            player_name=_clean(match.group("player_name")),
            be_slot=_clean(match.group("be_slot")),
            raw_source_ref=raw_source_ref,
        )

    if _PERSISTENCE_SHUTDOWN_RE.search(text) or _SERVICE_STOP_RE.search(text):
        return PlayerLogEvent(
            event_type=EVENT_TYPE_SERVER_LIFECYCLE,
            source=SOURCE_SERVER_LIFECYCLE,
            confidence=CONFIDENCE_HIGH,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            raw_source_ref=raw_source_ref,
        )

    if match := _KILL_ENEMY_RE.search(text):
        return _build_kill_event(
            match,
            event_type=EVENT_TYPE_KILL,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            raw_source_ref=raw_source_ref,
            teamkill=False,
            suicide=False,
        )

    if match := _KILL_TK_RE.search(text):
        return _build_kill_event(
            match,
            event_type=EVENT_TYPE_TEAMKILL,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            raw_source_ref=raw_source_ref,
            teamkill=True,
            suicide=False,
        )

    if match := _KILL_SUICIDE_RE.search(text):
        return _build_suicide_event(
            match,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            raw_source_ref=raw_source_ref,
        )

    if match := _KILL_OTHER_DEATH_RE.search(text):
        return _build_other_death_event(
            match,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            raw_source_ref=raw_source_ref,
        )

    if match := _SAT_PLAYER_KILLED_RE.search(text):
        instigator_name = _clean(match.group("instigator_name"))
        return PlayerLogEvent(
            event_type=EVENT_TYPE_COMBAT_HINT,
            source=SOURCE_SERVER_ADMIN_TOOLS_KILL,
            confidence=CONFIDENCE_MEDIUM,
            occurred_at=occurred_at,
            observed_at=observed_at,
            raw_timestamp=raw_timestamp,
            time_source=time_source,
            time_confidence=time_confidence,
            player_name=_clean(match.group("victim_name")),
            victim_name=_clean(match.group("victim_name")),
            instigator_name=instigator_name,
            teamkill=match.group("friendly").casefold() == "true",
            suicide=False,
            ai_instigator=_looks_like_ai(instigator_name, None),
            raw_source_ref=raw_source_ref,
        )

    return None


def enrich_player_log_event(event: PlayerLogEvent, hint: PlayerLogEvent) -> PlayerLogEvent:
    """Return ``event`` enriched by an optional parsed SAT hint when it matches."""
    if hint.source != SOURCE_SERVER_ADMIN_TOOLS_KILL:
        return event
    if not _names_match(event.victim_name, hint.victim_name):
        return event
    if not _names_match(event.instigator_name, hint.instigator_name):
        return event

    return replace(
        event,
        source=_append_source(event.source, hint.source),
        teamkill=event.teamkill if event.teamkill is not None else hint.teamkill,
        ai_instigator=(
            event.ai_instigator if event.ai_instigator is not None else hint.ai_instigator
        ),
    )


def _build_kill_event(
    match: re.Match[str],
    *,
    event_type: str,
    occurred_at: str | None,
    observed_at: str | None,
    raw_timestamp: str | None,
    time_source: str | None,
    time_confidence: str | None,
    raw_source_ref: str | None,
    teamkill: bool,
    suicide: bool,
) -> PlayerLogEvent:
    instigator = _parse_player_ref(match.group("instigator_ref"))
    instigator_id = instigator.player_id
    instigator_name = instigator.name
    return PlayerLogEvent(
        event_type=event_type,
        source=SOURCE_SCRIPT_KILL,
        confidence=CONFIDENCE_HIGH,
        occurred_at=occurred_at,
        observed_at=observed_at,
        raw_timestamp=raw_timestamp,
        time_source=time_source,
        time_confidence=time_confidence,
        player_id=_clean(match.group("victim_id")),
        player_name=_clean(match.group("victim_name")),
        session_player_id=_clean(match.group("victim_session_player_id")),
        victim_id=_clean(match.group("victim_id")),
        victim_name=_clean(match.group("victim_name")),
        victim_session_player_id=_clean(match.group("victim_session_player_id")),
        victim_faction=_clean(match.group("victim_faction")),
        instigator_id=instigator_id,
        instigator_name=instigator_name,
        instigator_session_player_id=instigator.session_player_id,
        instigator_faction=_clean(match.group("instigator_faction")),
        teamkill=teamkill,
        suicide=suicide,
        ai_instigator=_looks_like_ai(instigator_name, instigator_id),
        damage_type=_clean(match.group("damage_type")),
        hit_zone=_clean(match.group("hit_zone")),
        distance_m=_parse_distance_m(
            f"{match.group('context')} {match.groupdict().get('instigator_context') or ''}"
        ),
        raw_source_ref=raw_source_ref,
    )


def _build_suicide_event(
    match: re.Match[str],
    *,
    occurred_at: str | None,
    observed_at: str | None,
    raw_timestamp: str | None,
    time_source: str | None,
    time_confidence: str | None,
    raw_source_ref: str | None,
) -> PlayerLogEvent:
    victim_id = _clean(match.group("victim_id"))
    victim_name = _clean(match.group("victim_name"))
    victim_session_player_id = _clean(match.group("victim_session_player_id"))
    return PlayerLogEvent(
        event_type=EVENT_TYPE_SUICIDE,
        source=SOURCE_SCRIPT_KILL,
        confidence=CONFIDENCE_HIGH,
        occurred_at=occurred_at,
        observed_at=observed_at,
        raw_timestamp=raw_timestamp,
        time_source=time_source,
        time_confidence=time_confidence,
        player_id=victim_id,
        player_name=victim_name,
        session_player_id=victim_session_player_id,
        victim_id=victim_id,
        victim_name=victim_name,
        victim_session_player_id=victim_session_player_id,
        victim_faction=_clean(match.group("victim_faction")),
        instigator_id=victim_id,
        instigator_name=victim_name,
        instigator_session_player_id=victim_session_player_id,
        instigator_faction=_clean(match.group("victim_faction")),
        teamkill=False,
        suicide=True,
        ai_instigator=False,
        damage_type=_clean(match.group("damage_type")),
        hit_zone=_clean(match.group("hit_zone")),
        distance_m=_parse_distance_m(match.group("context")),
        raw_source_ref=raw_source_ref,
    )


def _build_other_death_event(
    match: re.Match[str],
    *,
    occurred_at: str | None,
    observed_at: str | None,
    raw_timestamp: str | None,
    time_source: str | None,
    time_confidence: str | None,
    raw_source_ref: str | None,
) -> PlayerLogEvent:
    return PlayerLogEvent(
        event_type=EVENT_TYPE_OTHER_DEATH,
        source=SOURCE_SCRIPT_KILL,
        confidence=CONFIDENCE_HIGH,
        occurred_at=occurred_at,
        observed_at=observed_at,
        raw_timestamp=raw_timestamp,
        time_source=time_source,
        time_confidence=time_confidence,
        player_id=_clean(match.group("victim_id")),
        player_name=_clean(match.group("victim_name")),
        session_player_id=_clean(match.group("victim_session_player_id")),
        victim_id=_clean(match.group("victim_id")),
        victim_name=_clean(match.group("victim_name")),
        victim_session_player_id=_clean(match.group("victim_session_player_id")),
        victim_faction=_clean(match.group("victim_faction")),
        teamkill=False,
        suicide=False,
        ai_instigator=None,
        damage_type=_clean(match.group("damage_type")),
        hit_zone=_clean(match.group("hit_zone")),
        distance_m=_parse_distance_m(match.group("context")),
        raw_source_ref=raw_source_ref,
    )


def _parse_player_ref(value: str) -> _ParsedPlayerRef:
    text = _clean(value) or ""
    if match := _PLAYER_REF_RE.fullmatch(text):
        return _ParsedPlayerRef(
            name=_clean(match.group("name")) or "",
            session_player_id=_clean(match.group("session_player_id")),
            player_id=_clean(match.group("player_id")),
        )
    return _ParsedPlayerRef(name=text)


def _parse_distance_m(value: str) -> float | None:
    if not (match := _DISTANCE_RE.search(value)):
        return None
    return float(match.group("distance"))


def _looks_like_ai(name: str | None, player_id: str | None) -> bool:
    if player_id or not name:
        return False
    normalized = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    if not normalized:
        return False
    return normalized == "ai" or normalized.startswith("ai ") or " ai " in f" {normalized} "


def _names_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    return left.casefold() == right.casefold()


def _append_source(source: str, hint_source: str) -> str:
    if hint_source in source.split("+"):
        return source
    return f"{source}+{hint_source}"


def _raw_timestamp_prefix(text: str) -> str | None:
    if not (match := _LOG_TIME_PREFIX_RE.match(text)):
        return None
    return _clean(match.group("timestamp"))


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text
