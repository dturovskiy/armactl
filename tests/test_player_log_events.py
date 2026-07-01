"""Tests for sanitized player log event parsing."""

from __future__ import annotations

from dataclasses import asdict

from armactl import player_log_events as events

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def test_parse_backend_authenticated_player_line() -> None:
    event = events.parse_player_log_event(
        "12:00:01.000 BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
        observed_at="2026-01-01T12:00:01+00:00",
        raw_source_ref="journal:alpha-auth",
    )

    assert event is not None
    assert event.event_type == events.EVENT_TYPE_PLAYER_AUTHENTICATED
    assert event.source == events.SOURCE_BACKEND_AUTH
    assert event.confidence == events.CONFIDENCE_HIGH
    assert event.observed_at == "2026-01-01T12:00:01+00:00"
    assert event.player_id == PLAYER_ALPHA_ID
    assert event.player_name == "Alpha One"
    assert event.rpl_identity == "42"
    assert event.raw_source_ref == "journal:alpha-auth"
    assert "raw_line" not in asdict(event)


def test_parse_network_player_update_line() -> None:
    event = events.parse_player_log_event(
        "NETWORK : ### Updating player: PlayerId=7, Name=Alpha One, "
        f"rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
        raw_timestamp="12:00:02.000",
        raw_source_ref="journal:alpha-update",
    )

    assert event is not None
    assert event.event_type == events.EVENT_TYPE_PLAYER_UPDATE
    assert event.source == events.SOURCE_NETWORK_PLAYER_UPDATE
    assert event.player_id == PLAYER_ALPHA_ID
    assert event.player_name == "Alpha One"
    assert event.session_player_id == "7"
    assert event.rpl_identity == "42"
    assert event.raw_timestamp == "12:00:02.000"


def test_parse_faction_join_line() -> None:
    event = events.parse_player_log_event(
        "SCRIPT : INFO: Faction: player Alpha One "
        f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) "
        "has joined faction #US_Army (US)",
    )

    assert event is not None
    assert event.event_type == events.EVENT_TYPE_FACTION_JOIN
    assert event.source == events.SOURCE_SCRIPT_FACTION_JOIN
    assert event.player_id == PLAYER_ALPHA_ID
    assert event.player_name == "Alpha One"
    assert event.session_player_id == "7"
    assert event.player_faction == "US"
    assert event.faction_resource == "US_Army"


def test_parse_disconnect_and_lifecycle_lines() -> None:
    rpl = events.parse_player_log_event(
        "RPL     : ServerImpl event: disconnected (identity=42), "
        "group=5, reason=timeout",
        observed_at="2026-01-01T12:10:00+00:00",
        raw_source_ref="journal:rpl-disconnect",
    )
    network = events.parse_player_log_event(
        "NETWORK : Player disconnected: connectionID=conn-7",
        raw_source_ref="journal:network-disconnect",
    )
    battleye = events.parse_player_log_event(
        "DEFAULT : BattlEye Server: 'Player #7 Alpha One disconnected'",
        raw_source_ref="journal:be-disconnect",
    )
    shutdown = events.parse_player_log_event(
        "DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started.",
        raw_source_ref="journal:shutdown",
    )
    service = events.parse_player_log_event(
        "systemd[1]: Stopping Arma Reforger Dedicated Server...",
        raw_source_ref="journal:service-stop",
    )

    assert rpl is not None
    assert rpl.event_type == events.EVENT_TYPE_PLAYER_DISCONNECTED
    assert rpl.source == events.SOURCE_RPL_DISCONNECT
    assert rpl.confidence == events.CONFIDENCE_MEDIUM
    assert rpl.rpl_identity == "42"
    assert rpl.observed_at == "2026-01-01T12:10:00+00:00"

    assert network is not None
    assert network.event_type == events.EVENT_TYPE_PLAYER_DISCONNECTED
    assert network.source == events.SOURCE_NETWORK_DISCONNECT
    assert network.confidence == events.CONFIDENCE_MEDIUM
    assert network.connection_id == "conn-7"

    assert battleye is not None
    assert battleye.event_type == events.EVENT_TYPE_PLAYER_DISCONNECTED
    assert battleye.source == events.SOURCE_BATTLEYE_DISCONNECT
    assert battleye.confidence == events.CONFIDENCE_LOW
    assert battleye.be_slot == "7"
    assert battleye.player_name == "Alpha One"

    assert shutdown is not None
    assert shutdown.event_type == events.EVENT_TYPE_SERVER_LIFECYCLE
    assert shutdown.source == events.SOURCE_SERVER_LIFECYCLE
    assert shutdown.confidence == events.CONFIDENCE_HIGH
    assert service is not None
    assert service.event_type == events.EVENT_TYPE_SERVER_LIFECYCLE


def test_parse_ai_kill_line() -> None:
    event = events.parse_player_log_event(
        "SCRIPT : INFO: KILL ENEMY: Bravo Two "
        f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
        "at <10 20 30> was killed by AI from FIA faction "
        "who was at that time at <40 50 60> [64.5m away from the corpse]. "
        "With last inflicted damage type Projectile to the 'Head' hit zone",
        raw_source_ref="journal:ai-kill",
    )

    assert event is not None
    assert event.event_type == events.EVENT_TYPE_KILL
    assert event.source == events.SOURCE_SCRIPT_KILL
    assert event.player_id == PLAYER_BRAVO_ID
    assert event.player_name == "Bravo Two"
    assert event.victim_id == PLAYER_BRAVO_ID
    assert event.victim_name == "Bravo Two"
    assert event.victim_session_player_id == "8"
    assert event.victim_faction == "US"
    assert event.instigator_id is None
    assert event.instigator_name == "AI"
    assert event.instigator_faction == "FIA"
    assert event.ai_instigator is True
    assert event.teamkill is False
    assert event.suicide is False
    assert event.damage_type == "Projectile"
    assert event.hit_zone == "Head"
    assert event.distance_m == 64.5
    assert event.raw_source_ref == "journal:ai-kill"


def test_parse_suicide_line() -> None:
    event = events.parse_player_log_event(
        "SCRIPT : INFO: KILL SUICIDE: Alpha One "
        f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
        "at <1 2 3> killed himself! "
        "With last inflicted damage type Explosion to the 'Torso' hit zone"
    )

    assert event is not None
    assert event.event_type == events.EVENT_TYPE_SUICIDE
    assert event.player_id == PLAYER_ALPHA_ID
    assert event.victim_id == PLAYER_ALPHA_ID
    assert event.instigator_id == PLAYER_ALPHA_ID
    assert event.instigator_name == "Alpha One"
    assert event.victim_faction == "US"
    assert event.instigator_faction == "US"
    assert event.suicide is True
    assert event.teamkill is False
    assert event.ai_instigator is False
    assert event.damage_type == "Explosion"
    assert event.hit_zone == "Torso"


def test_parse_teamkill_line() -> None:
    event = events.parse_player_log_event(
        "SCRIPT : INFO: KILL TK: Bravo Two "
        f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
        "at <4 5 6> was killed by Alpha One "
        f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
        "who was at that time at <4 5 7> [2.2m away from the corpse]. "
        "With last inflicted damage type Bullet to the 'LeftArm' hit zone"
    )

    assert event is not None
    assert event.event_type == events.EVENT_TYPE_TEAMKILL
    assert event.player_id == PLAYER_BRAVO_ID
    assert event.victim_id == PLAYER_BRAVO_ID
    assert event.instigator_id == PLAYER_ALPHA_ID
    assert event.instigator_name == "Alpha One"
    assert event.instigator_session_player_id == "7"
    assert event.victim_faction == "US"
    assert event.instigator_faction == "US"
    assert event.teamkill is True
    assert event.suicide is False
    assert event.ai_instigator is False
    assert event.damage_type == "Bullet"
    assert event.hit_zone == "LeftArm"
    assert event.distance_m == 2.2


def test_parse_other_death_line() -> None:
    event = events.parse_player_log_event(
        "SCRIPT : INFO: KILL OTHER_DEATH: Charlie Three "
        f"(playerID = 9 | UUID = {PLAYER_CHARLIE_ID}) from FIA faction "
        "at <7 8 9> was killed by AI"
    )

    assert event is not None
    assert event.event_type == events.EVENT_TYPE_OTHER_DEATH
    assert event.player_id == PLAYER_CHARLIE_ID
    assert event.victim_id == PLAYER_CHARLIE_ID
    assert event.victim_name == "Charlie Three"
    assert event.victim_faction == "FIA"
    assert event.instigator_id is None
    assert event.instigator_name is None
    assert event.teamkill is False
    assert event.suicide is False
    assert event.ai_instigator is None
    assert event.damage_type is None
    assert event.hit_zone is None


def test_malformed_unmatched_line_returns_no_event() -> None:
    assert events.parse_player_log_event("SCRIPT : INFO: unrelated log message") is None
    assert events.parse_player_log_event("DEFAULT : BattlEye address field ignored") is None


def test_optional_server_admin_tools_wrapper_can_enrich_kill_event() -> None:
    base_event = events.parse_player_log_event(
        "SCRIPT : INFO: KILL TK: Bravo Two "
        f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from US faction "
        "at <4 5 6> was killed by Alpha One "
        f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction. "
        "With last inflicted damage type Bullet to the 'LeftArm' hit zone",
        raw_source_ref="journal:combat-line",
    )
    sat_hint = events.parse_player_log_event(
        "SCRIPT : ServerAdminTools | Event serveradmintools_player_killed | "
        "player: Bravo Two, instigator: Alpha One, friendly: true",
        raw_source_ref="journal:sat-wrapper",
    )

    assert base_event is not None
    assert sat_hint is not None
    assert sat_hint.event_type == events.EVENT_TYPE_COMBAT_HINT
    assert sat_hint.source == events.SOURCE_SERVER_ADMIN_TOOLS_KILL
    assert sat_hint.victim_name == "Bravo Two"
    assert sat_hint.instigator_name == "Alpha One"
    assert sat_hint.teamkill is True

    enriched = events.enrich_player_log_event(base_event, sat_hint)

    assert enriched.event_type == events.EVENT_TYPE_TEAMKILL
    assert enriched.source == (
        f"{events.SOURCE_SCRIPT_KILL}+{events.SOURCE_SERVER_ADMIN_TOOLS_KILL}"
    )
    assert enriched.teamkill is True
    assert enriched.raw_source_ref == "journal:combat-line"
    assert enriched.victim_id == PLAYER_BRAVO_ID
    assert enriched.instigator_id == PLAYER_ALPHA_ID
