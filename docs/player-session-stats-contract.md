# Player Session Stats Contract

This document is the source of truth for fixing current-player combat, faction, and session columns without adding another heuristic patch. It exists because the first authenticated web-only enrichment slice proved that read-only aggregation over existing open player_sessions is not enough: data freshness still depends on manual log collection, reconnects need explicit semantics, and stats must be scoped to a real play session instead of a loose server-time window.

## Problem Statement

Current /players stats must not accumulate forever and must not reset randomly. They should describe the player's current play session when that session is backed by reliable evidence. A player can be kicked by network trouble and reconnect quickly; that should continue the same play session when the evidence supports it. A normal later rejoin, a server lifecycle boundary, or an identity conflict must start a new play session.

The current UI columns Kills, Deaths, TK, Faction, Role, and details Session first observed are layout placeholders plus limited read-only evidence. They are not accepted as complete session-stat truth until this contract is implemented.

## Non-Negotiable Rules

- Do not count combat events from an unbounded server log window.
- Do not use current roster cache, A2S count, or dashboard player count as combat/stat truth.
- Do not show fake zeroes. Show a placeholder when the session window, log freshness, or reliable ID binding is not proven.
- Do not make /players GET create sessions, collect logs, enqueue jobs, or write players.db.
- Do not store or render raw log lines, raw absolute paths, raw RCON rows, IPs, secrets, webhook URLs, CSRF/session tokens, or public player IDs.
- Do not enrich Discord/public stats with combat/session columns until authenticated web truth is stable and labelled.
- Role stays placeholder-only until a reliable role/loadout source is parsed and documented.

## Terms

### Server Run

A server run is the interval between accepted lifecycle/start markers and the next accepted lifecycle/stop/restart boundary. A server run boundary closes or splits play sessions. Combat stats must never cross a server lifecycle boundary.

### Connection Span

A connection span is one continuous observed connection/presence interval for one reliable player ID. It can start from reliable auth/update/connect evidence or a reliable current-roster scan observation. It can close from reliable disconnect evidence, repeated reliable roster absence, stale timeout, or server boundary.

### Play Session

A play session is the operator-facing gameplay interval used for current-player stats. It can contain multiple connection spans for the same reliable player ID when those spans are separated by a short reconnect gap and no conflicting boundary exists.

### Reconnect Grace

Default reconnect grace is 10 minutes. It is a merge policy, not the source of truth. A reconnect may continue the same play session only when all required evidence checks pass.

## Reconnect Merge Policy

A new connection span may continue the previous play session only if all of these are true:

- Same normalized reliable player ID.
- Same instance and same server run.
- Previous close reason is compatible with reconnect, for example network drop, timeout, unreliable disconnect, stale absence, or short missing-roster gap.
- Reconnect happens within the configured grace window, default 10 minutes.
- No accepted lifecycle/server-boundary marker exists between the previous close and the reconnect.
- No reliable identity conflict exists, such as a different reliable ID for the same connection/RPL correlation.
- The previous play session has not been finalized by retention/maintenance policy as a hard close.

A new play session must be created when any of these is true:

- Server lifecycle boundary occurred.
- Reliable ID changed.
- Reconnect gap exceeded the grace window.
- Previous session ended with a hard close reason that should not be merged.
- Evidence is ambiguous enough that merging would hide a possible real logout/rejoin.

Better-than-timer signals should override the grace fallback when available:

- Explicit backend/RPL/BattleEye correlation that proves the same reconnect can strengthen a merge.
- Explicit server boundary always blocks a merge.
- Repeated fresh reliable roster absence can close a span, but should not by itself erase the play session until reconnect grace expires.

## Log Ingest Contract

Current-player stats must not depend on the operator pressing Update events from logs.

Required future behavior:

- Add an explicit automatic log-ingest runner or scheduler path for player log events.
- It must reuse the existing allowlisted log collector/parser/ingest pipeline.
- It must be checkpointed and idempotent.
- It must be separated from /players GET and /players/current.json.
- It must produce counts-only job output and sanitized audit details.
- It must have freshness metadata so /players can say stats are unavailable/stale instead of showing misleading numbers.

Manual Update events from logs can remain as an operator action, but it must not be the only way current stats become fresh.

## Parser Stability Contract

Every stat-bearing field must come from a stable parsed event shape covered by fixtures from real log lines.

Required parser coverage:

- Reliable auth/update/connect evidence with reliable player ID.
- Reliable disconnect evidence when available.
- Server lifecycle markers.
- Faction join or side selection evidence.
- Combat kill, teamkill, suicide, and other death events.
- Combat victim reliable ID.
- Combat instigator reliable ID when present.
- AI/unknown instigator behavior.
- Event occurrence time from log evidence, not ingest time.
- Duplicate ingest of the same log lines.

Stats must ignore combat events when required reliable IDs or event times are missing or ambiguous. Those events can remain visible as diagnostics/history evidence.

## Storage Model Direction

Do not materialize counters first. First make the event/session boundaries correct.

Possible schema direction, to be finalized in implementation design:

- Keep player_log_events as the deduped event evidence table.
- Keep player_sessions as stored connection/presence evidence if it remains compatible.
- Add a separate play-session concept only if needed to model reconnect merging cleanly.
- Prefer an evidence/link table that maps player log events to a play session/session window by event ID and confidence.
- Keep stats as read-only aggregation over session-scoped evidence until the schema and retention story is proven.

If a new table is added, it must store only sanitized structured fields and no raw logs/paths/IPs/secrets.

## Current-Player Stats Rules

For /players current rows:

- A current roster reliable ID selects the candidate player.
- The candidate must have a current open or reconnect-grace play session in the same server run.
- Kills counts only normal kill events where that reliable ID is the reliable instigator.
- Deaths counts kill, teamkill, suicide, and other-death events where that reliable ID is the reliable victim.
- TK counts only teamkill events where that reliable ID is the reliable instigator.
- Teamkills do not increment Kills.
- AI or unknown instigator does not increment a player kill or TK.
- Faction is last-known faction/side evidence within the current play session and must be labelled as evidence, not guaranteed current truth.
- Joined time should be shown only as Session first observed unless a reliable explicit join/connect time source is proven.
- Role remains placeholder-only.

A zero is allowed only when the system has a proven current play session, fresh enough log ingest, and a completed query over that session window. Otherwise show a placeholder.

## UI Contract

Current /players table may reserve columns for Kills, Deaths, TK, Faction, Role, and Available actions, but values must stay placeholder-safe until the data contract is satisfied.

Details may show:

- Reliable ID.
- Human source label.
- Technical source label.
- Current roster update time.
- Session first observed time when backed by a play-session/session evidence window.
- Stats freshness / log ingest freshness state.
- Why stats are unavailable when relevant.

Do not show raw source refs, raw log lines, raw paths, IPs, secrets, or public player IDs.

## Discord/Public Contract

Discord and public status must stay at safe roster/status output until authenticated web session stats are stable.

Before adding Discord player columns, require:

- Session-scoped authenticated web stats pass VM smoke.
- Reconnect grace behavior is tested.
- Automatic log ingest freshness is working.
- Public wording and privacy review is complete.
- Message length and mention-safety tests are updated.

## Implementation Slices

### Slice A: Contract And Current UI Guard — implemented

- [x] Document this contract and wire it into checklist/plans.
- [x] Mark the previous authenticated web-only enrichment as insufficient for final session-stat truth.
- [x] Ensure current UI never shows fake zeroes when no proven session/log freshness exists.
- [x] Keep stats placeholders if the contract is not satisfied.

### Slice B: Parser Fixture Audit

- Collect real sample log lines for connect/auth/update, faction join, combat, disconnect, reconnect, and lifecycle.
- Add/repair parser tests for each required field.
- Confirm event occurrence time comes from log evidence.
- Confirm duplicate ingest does not duplicate events.

### Slice C: Automatic Log Ingest Foundation

- Add explicit automatic/foreground scheduler path for allowlisted player log collection.
- Reuse the current collector and ingest pipeline.
- Add checkpoint/freshness metadata.
- Keep GET read-only and output counts-only.

### Slice D: Play-Session/Reconnect Model

- Implement or adapt storage so one play session can contain reconnect spans.
- Add reconnect grace policy with default 10 minutes.
- Enforce same reliable ID, same server run, no lifecycle boundary, and compatible close reason.
- Add tests for reconnect within grace, reconnect after grace, server restart boundary, identity conflict, and repeated absence.

### Slice E: Session-Scoped Stats Aggregation

- Count kills/deaths/TK only inside the current play session.
- Require reliable IDs and event occurrence times.
- Return nullable values with explicit unavailable reasons.
- Do not persist counters in this slice unless a separate storage decision is made.

### Slice F: UI Smoke And Cleanup

- Update /players details to explain stats freshness and session first observed.
- Verify no fake zeroes, no stale/manual-only behavior, and no accumulation across real new sessions.
- Run Serhiivka smoke first, then Chervonopilya only after approval.

### Slice G: Discord/Public Evaluation

- Revisit Discord player columns only after Slice F is stable.
- Keep role blocked unless a source was added.
- Decide whether Discord should show current session stats, last session stats, or no combat stats.

## Acceptance Checklist

- [ ] Current stats do not require manual Update events from logs to become fresh.
- [ ] Manual log collection remains available but is not the only freshness path.
- [ ] A player reconnecting within 10 minutes after a compatible network/drop absence continues the same play session.
- [ ] A player reconnecting after the grace window starts a new play session.
- [ ] Server lifecycle boundary always starts a new play session.
- [ ] Reliable ID conflict blocks session merge.
- [ ] Kills, deaths, and TK are counted only inside the current play session.
- [ ] Teamkills do not increment Kills.
- [ ] AI/unknown instigator does not increment a player kill/TK.
- [ ] Faction is labelled as last-known session evidence.
- [ ] Role remains placeholder-only unless a reliable source is added.
- [ ] No fake zeroes when stats freshness/session binding is missing.
- [ ] Re-running ingest/session jobs does not duplicate stats.
- [ ] /players and /players/current.json stay GET-read-only for players.db and session/stat state.
- [ ] Job output and audit details are counts-only and sanitized.
- [ ] No raw log lines, raw paths, raw RCON rows, IPs, secrets, public player IDs, or Discord enrichment are introduced.
- [ ] Serhiivka VM smoke passes before any Chervonopilya deploy.

## Immediate Next Recommended Slice

Slice A is implemented. Do not patch the current stat counts again. The next code-bearing slice should be Slice B parser fixture audit or narrowly scoped freshness groundwork. Session-scoped stats should wait until parser fixtures, automatic ingest freshness, and reconnect/session boundaries are proven.
