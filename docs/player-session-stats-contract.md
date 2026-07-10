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


## Slice B Parser Fixture Audit Findings

Slice B is implemented as an audit and fixture slice only. It reviewed `src/armactl/player_log_events.py`, `src/armactl/player_log_collector.py`, `player_registry.ingest_player_log_events`, stored-log sessionization, current-player enrichment guards, and the related parser/storage/sessionization/current-player tests. It does not add current-player stat aggregation, automatic log ingest, play-session/reconnect behavior, Discord/public enrichment, or a new source of truth.

### Supported Stable Parsed Patterns

| Evidence pattern | Parsed event/source | Reliable player ID | Name/text only | Faction fields | Event occurrence time | Fixture status | Future use |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BACKEND : Authenticated player: rplIdentity=... identityId=... name=...` | `player_authenticated` / `backend_authenticated_player` | `player_id`; `rpl_identity` is correlation only | `player_name` | None | Exact or derived only when the caller/collector provides `occurred_at` or `observed_at`; time-of-day alone stays ambiguous | Parser, collector, storage, sessionizer | High-confidence session-open evidence when reliable ID and trusted time exist. |
| `NETWORK : ### Updating player: PlayerId=..., Name=..., rplIdentity=..., IdentityId=...` | `player_update` / `network_player_update` | `player_id`; `session_player_id` and `rpl_identity` are correlation only | `player_name` | None | Same collector/caller timestamp contract | Parser, collector, storage, sessionizer | High-confidence session-open/update evidence when reliable ID and trusted time exist. |
| `SCRIPT : INFO: Faction: player ... (playerID = ... | UUID = ...) has joined faction #... (...)` | `faction_join` / `script_faction_join` | `player_id`; `session_player_id` is correlation only | `player_name` | `faction_resource`, `player_faction` | Same collector/caller timestamp contract | Parser, collector, storage, sessionizer | Medium-confidence presence and last-known faction evidence; not current faction truth by itself. |
| `SCRIPT : INFO: KILL ENEMY: ... was killed by ...` | `kill` / `script_kill` | `victim_id`; `instigator_id` only when the instigator ref includes UUID | AI or unknown instigator may be name/text only | `victim_faction`, `instigator_faction` labels | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Future `Kills` only when `instigator_id` is reliable, non-AI, and inside a proven play session. Future `Deaths` when `victim_id` is reliable. |
| `SCRIPT : INFO: KILL TK: ... was killed by ...` | `teamkill` / `script_kill` | `victim_id` and, when present, `instigator_id` | None when both refs include UUIDs | Victim/instigator faction labels | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Future `TK` only when reliable instigator ID is present and inside a proven play session. Does not increment `Kills`. |
| `SCRIPT : INFO: KILL SUICIDE: ... killed himself/...` | `suicide` / `script_kill` | `victim_id`; parser also sets instigator to victim | None | Victim/instigator faction from victim faction | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Future `Deaths` only for reliable victim ID; never increments player `Kills` or `TK`. |
| `SCRIPT : INFO: KILL OTHER_DEATH: ...` | `other_death` / `script_kill` | `victim_id` | Instigator is not reliable/structured | `victim_faction` | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Future `Deaths` only for reliable victim ID; no player kill assignment. |
| `SCRIPT : ServerAdminTools | Event serveradmintools_player_killed | player: ..., instigator: ..., friendly: true/false` | `combat_hint` / `serveradmintools_player_killed` | None | Victim and instigator names only | None | Parser and collector occurrence-time fixture | Diagnostic/enrichment hint only. It cannot assign Kills/Deaths/TK without matching stable script combat evidence. |
| `RPL : ServerImpl event: disconnected (identity=...)` | `player_disconnected` / `rpl_disconnect` | None; `rpl_identity` is correlation only | None | None | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Session close only when exactly one open session has matching stored correlation. |
| `NETWORK : Player disconnected: connectionID=...` | `player_disconnected` / `network_disconnect` | None; `connection_id` is correlation only | None | None | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Session close only when exactly one open session has matching stored correlation. |
| `DEFAULT : BattlEye Server: 'Player #... ... disconnected'` | `player_disconnected` / `battleye_disconnect` | None; `be_slot` is correlation only | `player_name` | None | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Low-confidence close evidence only with unambiguous stored slot correlation; name alone is never enough. |
| `DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started.` and bounded service stop text | `server_lifecycle` / `server_lifecycle` | None | None | None | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer | Accepted server-boundary close evidence for open sessions. |

Occurrence-time contract: the pure parser can preserve a raw time prefix, but trusted event time comes from caller/collector metadata. The collector now has focused fixture coverage for all supported parsed event families using a dated log context, for midnight rollover, for exact absolute timestamps, and for ambiguous time-of-day-only logs. Ambiguous rows are stored as evidence but do not update known-player timestamps or open sessions.

### Blocked Or Diagnostic-Only Patterns

| Pattern | Status | Reason |
| --- | --- | --- |
| BattlEye connect/address lines, BattlEye `Setting GUID`, and BE GUID hash lines | Blocked for session-stat truth | They do not provide the existing reliable player ID directly and can include external/private identifiers or addresses. They need an explicit privacy/source decision before parser/storage expansion. |
| `ServerAdminTools player_joined` wrapper | Observed but not accepted in this slice | It is mod/source-dependent and is not currently parsed. It can be reconsidered as a future connect fixture only if its identity field is confirmed to be the same reliable ID namespace and source/capability labeling is added. |
| Mission start markers such as game-created, playthrough-starting, entered-online-state, and `serveradmintools_game_started` | Blocked until server-run model | The current `server_lifecycle` parser shape is used as a close/server-boundary marker. Start markers need lifecycle subtype/server-run storage before they can safely participate in session windows. |
| Aggregate `NETWORK: Players connected: N / N` and `FPS: ... Player: N` telemetry | Diagnostic/count-only | They identify no player and must never open identified sessions or produce combat stats. |
| Time-of-day-only log prefixes with no date context | Ambiguous | Stored with raw timestamp and ambiguous confidence only. They are not accepted for known-player/session/stat truth. |
| Role/loadout, moderation/kick/ban, explicit crash reason, and vanilla/no-mod combat source | Blocked | No reliable parsed source was confirmed in the current repo evidence. |
| Optional ServerAdminTools kill wrapper without matching stable script combat line | Diagnostic-only | It has names/friendly flag but no reliable IDs, factions, or durable event identity for stat assignment. |

### Required Fields For Future Session Stats

| Future use | Required fields before use | Block when |
| --- | --- | --- |
| Session open/update from logs | Reliable `player_id`, trusted `occurred_at` or `observed_at`, safe source/source_ref, confidence; correlation fields are helpful but not durable IDs. | Reliable ID is missing/invalid, event time is ambiguous, or source is not accepted. |
| Session close from disconnect | Trusted event time plus exactly one matching open session by stored `rpl_identity`, `connection_id`, or `be_slot`; lifecycle close needs only accepted server-boundary time. | Correlation is absent, matches zero/multiple sessions, name-only evidence is present, or the time is ambiguous. |
| Server run boundary | Accepted lifecycle close marker with trusted time. | Marker is a start-only lifecycle line without server-run subtype support. |
| Faction evidence | Reliable `player_id`, trusted time, and `faction_resource` or `player_faction`. | Evidence is outside the proven play session, stale/conflicting, or only inferred from combat faction labels. |
| Kill count | `event_type=kill`, reliable non-AI `instigator_id`, reliable `victim_id`, trusted occurrence time, and play-session membership for the instigator. | Instigator is AI/unknown/name-only, event is `teamkill`, event time is ambiguous, or no proven play-session window exists. |
| Death count | Reliable victim ID on `kill`, `teamkill`, `suicide`, or `other_death`, trusted occurrence time, and play-session membership for the victim. | Victim ID or trusted time is missing, or no proven play-session window exists. |
| TK count | `event_type=teamkill`, reliable non-AI `instigator_id`, trusted occurrence time, and play-session membership for the instigator. | Teamkill is inferred only from same faction labels or ServerAdminTools hint-only rows. |
| Zero stat display | Proven current play session, fresh automatic log ingest metadata, completed scoped query, and no matching events. | Any freshness/session/time/ID requirement is missing; show placeholder instead. |

### Fixture And Test Coverage Summary

- Parser fixtures cover backend auth, network update, faction join, RPL/network/BattlEye disconnect, shutdown/service lifecycle, kill, teamkill, suicide, other death, AI instigator behavior, optional ServerAdminTools kill hints, and unmatched lines.
- Collector fixtures cover bounded reads, dry-run, duplicate imports, sanitized source refs, no raw paths/IPs/raw lines, derived occurrence time from dated log context, exact absolute timestamp prefixes, midnight rollover, and ambiguous time-of-day-only logs.
- Storage fixtures cover schema columns/indexes, sanitized ingest for auth/update/faction/disconnect/lifecycle/combat, duplicate ingest, disconnect dedupe by correlation fields, ambiguous timestamps not updating known players, legacy timestamp migration/dedupe, and filtered event listing.
- Sessionization fixtures cover auth/update session open/update, faction and combat as inferred presence only, RPL/connection/slot close only when unambiguous, lifecycle close as server boundary, idempotence, skip without trusted event time, and no stat claims from combat evidence.
- Current-player enrichment fixtures confirm the `/players` guard keeps Kills/Deaths/TK/Faction/Role placeholder-safe and returns an unavailable reason instead of fake zeroes or current-session stat claims.

### Acceptance Criteria For Later Slices

Slice C automatic log ingest foundation is implemented. It reuses the current collector/parser/player-registry ingest path, adds players.db checkpoint/freshness metadata, keeps GET routes read-only for ingest state, preserves occurrence-time evidence, handles unchanged/missing/rotated/truncated logs with controlled counts, and reports sanitized counts-only job/audit output. It does not enable a daemon, timer, app-start worker, broad scheduler, public enrichment, or current-player stat aggregation.

Slice D play-session/reconnect modeling is implemented. It keeps server-run boundary storage explicit and uses same reliable ID, same server run, compatible close reason, reconnect grace, no lifecycle boundary in the gap, and no identity conflict as hard merge gates. It does not merge or close sessions from count-only, name-only, ambiguous-time, failed-staleness, or multi-match correlation evidence.

Slice E session-scoped stat aggregation can start only after Slice D provides proven play-session windows and Slice C freshness metadata is used as a freshness gate. It must aggregate from stored event IDs inside the play session, require reliable victim/instigator IDs and trusted occurrence times, return nullable values with unavailable reasons, keep teamkills out of Kills, ignore AI/unknown instigators, and display zero only when the complete scoped query proves zero.

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

### Slice B: Parser Fixture Audit - implemented

- [x] Audit existing parser, collector, storage, sessionization, and current-enrichment evidence paths.
- [x] Document supported stable parsed patterns, blocked/diagnostic-only patterns, and required fields per future stat event type.
- [x] Confirm event occurrence time comes from caller/collector log evidence, not ingest time.
- [x] Add focused collector fixtures for all supported parsed event families under dated log context and exact absolute timestamp prefixes.
- [x] Confirm duplicate ingest and ambiguous timestamp behavior remain covered.

### Slice C: Automatic Log Ingest Foundation - implemented

- [x] Reuse the existing allowlisted web player-log collection job and current collect_player_log_events -> parser -> player_registry.ingest_player_log_events storage path.
- [x] Add per-log checkpoint metadata in players.db using safe source hashes, sanitized labels, bounded file fingerprints, size/mtime, and last scanned status.
- [x] Skip unchanged files by checkpoint, rescan changed files, and treat missing, rotated, truncated, and oversized logs as controlled counts instead of raw-path failures.
- [x] Add counts-only freshness metadata with fresh, no_logs, partial, failed, run timestamps, scanned/parsed/stored/skipped counts, skip reasons, and checkpoint-updated status.
- [x] Expose freshness only as safe status/counts on the player history surface; no raw paths, raw lines, IPs, secrets, or source refs are rendered.
- [x] Keep /players, /players/current.json, /players/history, and /players/sessions from starting ingest or creating players.db; only explicit POST/manual job execution mutates ingest metadata.
- [x] Do not enable a daemon, timer, app-start worker, broad scheduler, session aggregation, current-roster cache stats, Discord/public enrichment, or fake zeroes.

Pending after Slice C: an opt-in automatic runner/policy can enqueue this existing job path, but it must remain explicit and must not run from GET pages, app import/startup, or a hidden timer.

### Slice D: Play-Session/Reconnect Model - implemented

- [x] Treat player_sessions.session_id as the durable play-session window key, with explicit play_session_id, server_run_key, reconnect merge count, last reconnect metadata, and last gameplay evidence metadata for future Slice E aggregation.
- [x] Reopen the same play-session window when reliable evidence for the same reliable player ID returns within the default 10 minute reconnect grace and all merge gates pass.
- [x] Block reconnect merges across lifecycle boundaries, incompatible close reasons, identity/correlation conflicts, overlapping conflicting open sessions, and gaps beyond the grace window.
- [x] Record lifecycle boundary markers even when no session is open, so disconnect-before-shutdown gaps cannot merge into the next server run.
- [x] Keep roster-only evidence as presence/session evidence and gameplay evidence as explicit last-gameplay metadata; neither claims exact joined time or current combat stats.
- [x] Keep current players page Kills, Deaths, TK, Faction, and Role as placeholders; Slice E stat aggregation remains future work.
- [x] Add tests for reconnect grace, reconnect after grace, lifecycle boundaries, stale absence, identity conflicts, sessionizer idempotence, and gameplay evidence metadata.

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

- [x] Ingest foundation has checkpoint/freshness metadata and no longer requires rescanning unchanged logs from the manual button.
- [ ] Current stats do not require manual Update events from logs to become fresh.
- [ ] Manual log collection remains available but is not the only freshness path.
- [x] A player reconnecting within 10 minutes after a compatible network/drop absence continues the same play session.
- [x] A player reconnecting after the grace window starts a new play session.
- [x] Server lifecycle boundary always starts a new play session.
- [x] Reliable ID conflict blocks session merge.
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

Slice D is implemented. Do not patch the current stat counts with open-session or roster-window shortcuts. The next code-bearing slice should be Slice E session-scoped stat aggregation, using Slice C freshness and the Slice D play-session windows as gates.
