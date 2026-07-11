# Player Session Stats Contract

This document is the source of truth for current-player combat, faction, and session columns without heuristic shortcuts. Slices C, D, and E provide checkpointed ingest freshness metadata, explicit reconnect-aware play-session windows, and read-only session-scoped aggregation. Slice F2-a provides the systemd oneshot/timer foundation for the shared F1 ingest path, F2-c provides bounded incremental active-log coverage, and F2-b production acceptance is complete on Serhiivka and Chervonopilya. Installation remains disabled by default and preserves existing enablement; production activation was explicit. No app-start worker, browser poller, hidden thread, GET-side ingest, or player-session scheduler was enabled.

Busy-server acceptance also requires the bounded incremental contract in [player-log-ingest-incremental-contract.md](player-log-ingest-incremental-contract.md). An oversized active log is tailed once and then read from persisted append offsets; active-source coverage start is stored explicitly, and statistics stay unavailable for any session that began before that proven coverage.

## Problem Statement

Current /players stats must not accumulate forever and must not reset randomly. They should describe the player's current play session when that session is backed by reliable evidence. A player can be kicked by network trouble and reconnect quickly; that should continue the same play session when the evidence supports it. A normal later rejoin, a server lifecycle boundary, or an identity conflict must start a new play session.

The authenticated `/players` and `/players/current.json` surfaces now expose nullable Kills, Deaths, TK, and last-known Faction only when this contract is satisfied. Missing reliable ID, database/schema, open play session, lifecycle proof, checkpoint proof, or fresh ingest coverage renders `—`, not `0`. Role remains `—`.

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

Slice C provides the reusable allowlisted collector/parser/ingest path, per-source checkpoints, idempotent event storage, counts-only job/audit output, and safe freshness metadata. Slice F1 now exposes that same path as one shared synchronous service used by both the manual web job and an explicit foreground CLI. Slice E consumes stored metadata but never starts ingest from `/players` or `/players/current.json`.

The current freshness gate requires all of the following:

- Scope `instance_config_profile_console_logs` has status `fresh`.
- `last_success_at` is a valid trusted timestamp no more than five minutes old, with only a small future-clock tolerance.
- At least one checkpoint for the same scope is marked `scanned` with a scan timestamp.
- Fresh coverage reaches or exceeds the open play-session start.

Missing, `no_logs`, partial, failed, stale, malformed, or checkpoint-free freshness cannot prove a zero and returns nullable stats with a safe unavailable reason. The explicit manual job and foreground `players log-ingest run --once` remain available. Slice F2-a adds generated `armactl-player-log-ingest.service` and `armactl-player-log-ingest.timer` units plus explicit install/enable/disable/status commands. Installation leaves the 120-second completion-relative timer disabled; only an explicit enable activates it. F2-b production acceptance proved repeated automatic freshness without the manual button on both target VMs, including the F2-c bounded busy-log path. This runner is not a hidden daemon, app-start hook, browser poller, GET mutation, or player-session scheduler.

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
| `SCRIPT : INFO: KILL ENEMY: ... was killed by ...` | `kill` / `script_kill` | `victim_id`; `instigator_id` only when the instigator ref includes UUID | AI or unknown instigator may be name/text only | `victim_faction`, `instigator_faction` labels | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer, current stats | `Kills` only when `instigator_id` is the current reliable player ID, the instigator is non-AI, the event is stable, and it is inside the proven play-session window. `Deaths` uses reliable `victim_id`. |
| `SCRIPT : INFO: KILL TK: ... was killed by ...` | `teamkill` / `script_kill` | `victim_id` and, when present, `instigator_id` | None when both refs include UUIDs | Victim/instigator faction labels | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer, current stats | `TK` only when reliable non-AI `instigator_id` matches the current player inside the proven window. It never increments `Kills`; a reliable matching victim increments `Deaths`. |
| `SCRIPT : INFO: KILL SUICIDE: ... killed himself/...` | `suicide` / `script_kill` | `victim_id`; parser also sets instigator to victim | None | Victim/instigator faction from victim faction | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer, current stats | `Deaths` only for reliable matching victim ID inside the proven window; never increments player `Kills` or `TK`. |
| `SCRIPT : INFO: KILL OTHER_DEATH: ...` | `other_death` / `script_kill` | `victim_id` | Instigator is not reliable/structured | `victim_faction` | Same collector/caller timestamp contract | Parser, collector occurrence-time fixture, storage, sessionizer, current stats | `Deaths` only for reliable matching victim ID inside the proven window; no player kill assignment. |
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

### Required Fields For Session Stats

| Use | Required fields before use | Block when |
| --- | --- | --- |
| Session open/update from logs | Reliable `player_id`, trusted `occurred_at` or `observed_at`, safe source/source_ref, confidence; correlation fields are helpful but not durable IDs. | Reliable ID is missing/invalid, event time is ambiguous, or source is not accepted. |
| Session close from disconnect | Trusted event time plus exactly one matching open session by stored `rpl_identity`, `connection_id`, or `be_slot`; lifecycle close needs only accepted server-boundary time. | Correlation is absent, matches zero/multiple sessions, name-only evidence is present, or the time is ambiguous. |
| Server run boundary | Accepted lifecycle close marker with trusted time. | Marker is a start-only lifecycle line without server-run subtype support. |
| Faction evidence | Last structured faction label for the current reliable player from stable faction-join, victim, or non-AI instigator evidence inside the proven play-session window. | Evidence is outside the window, has ambiguous time, belongs only to another player, or no structured faction label exists. The result is last-known session evidence, not guaranteed current truth. |
| Kill count | `event_type=kill`, reliable non-AI `instigator_id` equal to the current player reliable ID, trusted occurrence time, and play-session membership for the instigator. | Instigator is AI/unknown/name-only, event is `teamkill`, event time is ambiguous, or no proven play-session window exists. |
| Death count | Reliable victim ID on `kill`, `teamkill`, `suicide`, or `other_death`, trusted occurrence time, and play-session membership for the victim. | Victim ID or trusted time is missing, or no proven play-session window exists. |
| TK count | `event_type=teamkill`, reliable non-AI `instigator_id`, trusted occurrence time, and play-session membership for the instigator. | Teamkill is inferred only from same faction labels or ServerAdminTools hint-only rows. |
| Zero stat display | Proven current play session, fresh automatic log ingest metadata, completed scoped query, and no matching events. | Any freshness/session/time/ID requirement is missing; show placeholder instead. |

### Fixture And Test Coverage Summary

- Parser fixtures cover backend auth, network update, faction join, RPL/network/BattlEye disconnect, shutdown/service lifecycle, kill, teamkill, suicide, other death, AI instigator behavior, optional ServerAdminTools kill hints, and unmatched lines.
- Collector fixtures cover bounded reads, dry-run, duplicate imports, sanitized source refs, no raw paths/IPs/raw lines, derived occurrence time from dated log context, exact absolute timestamp prefixes, midnight rollover, and ambiguous time-of-day-only logs.
- Storage fixtures cover schema columns/indexes, sanitized ingest for auth/update/faction/disconnect/lifecycle/combat, duplicate ingest, disconnect dedupe by correlation fields, ambiguous timestamps not updating known players, legacy timestamp migration/dedupe, and filtered event listing.
- Sessionization fixtures cover auth/update session open/update, faction and combat as inferred presence only, RPL/connection/slot close only when unambiguous, lifecycle close as server boundary, idempotence, skip without trusted event time, and no stat claims from combat evidence.
- Current-player enrichment fixtures cover fresh proven windows, true zeroes, stable kills/deaths/TK, teamkill separation, victim-only deaths, AI and ambiguous-time exclusion, last-known faction evidence, out-of-window exclusion, reconnect merge/split behavior, lifecycle boundaries, stale/missing freshness, missing database/session/checkpoints, read-only routes, and sensitive-output redaction. Foreground ingest fixtures additionally cover shared manual/CLI service use, blocking completion, unchanged/appended evidence, controlled missing/oversized sources, lock contention and stale-file recovery, failed freshness, read-only status, and sanitized CLI/audit output. Role remains placeholder-only.

### Implemented Slice Acceptance

Slice C automatic log ingest foundation, Slice F1 reusable foreground foundation, Slice F2-a supervised service foundation, F2-c bounded incremental active-log support, and F2-b VM acceptance are implemented. One synchronous service reuses the current collector/parser/player-registry ingest path, owns allowlisted discovery plus checkpoint/freshness orchestration, and is called by the thin manual web job adapter, `armactl players log-ingest run --once`, and the F2-a systemd oneshot wrapper. It preserves occurrence-time evidence, handles unchanged/appended/missing/rotated/truncated/oversized logs with controlled counts, rejects overlapping instance/scope runs with the same process-lifetime file lock, and reports sanitized counts-only output. F2-a installs no second parser, collector, SQL pipeline, checkpoint/freshness ledger, background thread, GET trigger, or player-session scheduler; installation does not enable or start its timer. Production enablement was an explicit operator action after validation.

Slice D play-session/reconnect modeling is implemented. It keeps server-run boundary storage explicit and uses same reliable ID, same server run, compatible close reason, reconnect grace, no lifecycle boundary in the gap, and no identity conflict as hard merge gates. It does not merge or close sessions from count-only, name-only, ambiguous-time, failed-staleness, or multi-match correlation evidence.

Slice E session-scoped stat aggregation is implemented as a read-only query over stored evidence. It uses the open Slice D play-session window and server-run key, validates that no lifecycle boundary crossed the window, caps the query at the fresh Slice C `last_success_at`, accepts only exact/derived event times, returns nullable values with safe unavailable reasons, keeps teamkills out of Kills, ignores AI/unknown instigators for Kills/TK, and displays zero only when the completed fresh scoped query proves zero.

## Storage Model

Slice E does not materialize counters or add a stats table. It keeps `player_log_events` as deduplicated event evidence, uses `player_sessions.play_session_id` and `server_run_key` as the stored play-window proof, uses lifecycle-boundary rows to prevent carryover, and reads Slice C freshness/checkpoint rows as the coverage gate. Kills, Deaths, TK, and Faction are recomputed read-only for the current reliable roster ID.

Any later materialization or evidence-link table requires a separate storage/retention decision and may contain only sanitized structured fields—never raw logs, raw paths, IPs, secrets, or raw correlation output.

## Current-Player Stats Rules

For `/players` current rows:

- A normalized current-roster reliable ID selects the candidate player; roster/A2S data never synthesizes stats.
- The candidate must have one open Slice D play session whose `play_session_id`, open evidence time, `server_run_key`, and lifecycle boundary proof are internally consistent.
- The inclusive stats window starts at the play-session open evidence time and ends at the fresh Slice C `last_success_at`; events before the start or after the coverage cutoff are ignored.
- A reconnect merged within grace keeps the original play-session start and therefore the same stats window. A reconnect after grace or across a lifecycle boundary starts a new window.
- Only stable stored events with exact or derived occurrence time participate; ambiguous-time and diagnostic-only rows do not.
- Kills counts only stable normal `kill` events where the reliable non-AI instigator ID equals the current player reliable ID.
- Deaths counts stable `kill`, `teamkill`, `suicide`, and `other_death` events where the reliable victim ID equals the current player reliable ID.
- TK counts only stable `teamkill` events where the reliable non-AI instigator ID equals the current player reliable ID.
- Teamkills do not increment Kills; AI or unknown instigators do not increment player Kills or TK.
- Faction is the last structured faction evidence for the current player inside the window and is labelled as last-known session evidence, not guaranteed current truth.
- Session first observed is evidence time, not guaranteed exact joined time. Role remains placeholder-only.

A zero is allowed only after reliable identity, session/server-run/lifecycle proof, fresh checkpointed ingest coverage, and the completed scoped query all pass. Otherwise the API returns nullable values and the UI shows `—` with a safe unavailable reason.

## UI Contract

The current `/players` table renders nullable Kills, Deaths, TK, and Faction. Available proven zeroes render `0`; unavailable values render `—`. Role always remains `—` in this slice, and no K/D column is added.

Details may show only safe operator terms:

- Reliable ID plus human and technical roster-source labels.
- Current roster update time and Session first observed evidence time.
- Safe stats source label, log-ingest freshness timestamp, stats-window start, coverage cutoff, and whether reconnect grace merged the window.
- A controlled unavailable reason when any proof gate fails.

The JSON surface may expose the nullable values plus `stats_available`, `stats_unavailable_reason`, safe freshness/window timestamps, and reconnect-merged status. It must not expose raw source refs, raw log lines, raw paths, IPs, secrets, raw correlation IDs, `server_run_key`, internal play-session IDs, or public player IDs.

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

- [x] Reuse the current allowlisted discovery and `collect_player_log_events` -> parser -> `player_registry.ingest_player_log_events` storage path without a second parser, collector, SQL, checkpoint, or freshness pipeline.
- [x] Add per-log checkpoint metadata in players.db using safe source hashes, sanitized labels, bounded file fingerprints, size/mtime, and last scanned status.
- [x] Skip unchanged files by checkpoint, rescan changed files, and treat missing, rotated, truncated, and oversized logs as controlled counts instead of raw-path failures.
- [x] Add counts-only freshness metadata with fresh, no_logs, partial, failed, run timestamps, scanned/parsed/stored/skipped counts, skip reasons, and checkpoint-updated status.
- [x] Expose freshness only as safe status/counts on the player history surface; no raw paths, raw lines, IPs, secrets, or source refs are rendered.
- [x] Keep `/players`, `/players/current.json`, `/players/history`, and `/players/sessions` from starting ingest or creating `players.db`; only explicit manual POST job execution or explicit foreground CLI execution mutates ingest metadata.
- [x] Do not enable a daemon, timer, app-start worker, broad scheduler, session aggregation, current-roster cache stats, Discord/public enrichment, or fake zeroes.

### Slice F1: Shared One-Shot And Foreground CLI — implemented locally

- [x] Complete the one-shot orchestration audit/design and record the manual-only root cause, job/service boundary, daemon-thread limitation, persistence, audit, and source-of-truth decisions.
- [x] Move allowlisted discovery, checkpoint planning, existing collector invocation, checkpoint/freshness persistence, and typed counts-only outcome into one synchronous service.
- [x] Keep `players:collect-log-events` as a thin adapter with existing active-job dedupe and manual intent/outcome audit semantics.
- [x] Add blocking `armactl players log-ingest run --once` and read-only `armactl players log-ingest status`, with instance/data-root support and sanitized output.
- [x] Prevent manual/CLI overlap with one shared nonblocking instance/scope lock whose kernel ownership is released on process death; do not add a second dedupe ledger or fake cancellation.
- [x] Hand the foreground runner to a separate explicit supervised service/timer slice rather than adding a hidden thread or GET trigger.

### Slice F2-a: Explicit Supervised Ingest Service/Timer Foundation — implemented locally

- [x] Generate one `Type=oneshot` `armactl-player-log-ingest.service` and one explicit `armactl-player-log-ingest.timer` through the established service-manager/template ownership model.
- [x] Keep installation idempotent and disabled by default; require `players log-ingest enable` to enable and activate the timer, and provide explicit disable/status operations.
- [x] Use one 120-second source-of-truth cadence with `OnUnitInactiveSec`, so the next run is scheduled after the prior oneshot finishes and timer-driven runs do not overlap.
- [x] Keep the F1 cross-process lock authoritative; scheduled `already_running` is a controlled zero-exit skipped cycle, while real ingest failures stay nonzero and do not manufacture fresh success.
- [x] Run through the generated direct project `.venv` path as the resolved armactl instance owner with `UMask=0077`, a bounded 360-second failure guard derived from the 32-file workload bound, and restrained CPU/I/O priority.
- [x] Keep scheduled journald output counts-only. Preserve manual foreground outcome audit, suppress unchanged successful scheduled audit, and persist only bounded sanitized failure/freshness-transition/recovery events.
- [x] Keep status read-only and controlled for missing units or missing `players.db`; report unit existence, enabled/active/failed/result state, timer next trigger, and stored freshness.
- [x] Do not restart or mutate `armareforger.service`, start work from GET/browser/app startup, couple to the player-session scheduler, or add a daemon/background thread.

The ingest acceptance criteria “freshness updates without the manual button” and “manual collection is not the only freshness path” are complete after F2-b VM observation. Stats still remain unavailable without a proven open play session covered from its opening; F2-a does not silently enable the separate player-session scheduler.

### Slice D: Play-Session/Reconnect Model - implemented

- [x] Treat player_sessions.session_id as the durable play-session window key, with explicit play_session_id, server_run_key, reconnect merge count, last reconnect metadata, and last gameplay evidence metadata consumed by Slice E aggregation.
- [x] Reopen the same play-session window when reliable evidence for the same reliable player ID returns within the default 10 minute reconnect grace and all merge gates pass.
- [x] Block reconnect merges across lifecycle boundaries, incompatible close reasons, identity/correlation conflicts, overlapping conflicting open sessions, and gaps beyond the grace window.
- [x] Record lifecycle boundary markers even when no session is open, so disconnect-before-shutdown gaps cannot merge into the next server run.
- [x] Keep roster-only evidence as presence/session evidence and gameplay evidence as explicit last-gameplay metadata; neither claims exact joined time or current combat stats.
- [x] Keep current players page Kills, Deaths, TK, Faction, and Role placeholder-safe until Slice E proof gates are satisfied.
- [x] Add tests for reconnect grace, reconnect after grace, lifecycle boundaries, stale absence, identity conflicts, sessionizer idempotence, and gameplay evidence metadata.

### Slice E: Session-Scoped Stats Aggregation — implemented

- [x] Count Kills, Deaths, and TK only inside the proven current play-session window capped by fresh ingest coverage.
- [x] Require normalized reliable IDs, stable event types, exact/derived occurrence times, same server-run proof, and no lifecycle boundary in the window.
- [x] Keep teamkills out of Kills, count deaths by matching victim only, and ignore AI/unknown instigators for Kills/TK.
- [x] Preserve reconnect-merged windows within grace and start new windows after grace or lifecycle boundaries.
- [x] Return nullable values and controlled unavailable reasons; permit `0` only when a complete fresh scoped query proves zero.
- [x] Return last-known Faction as session evidence, keep Role as `—`, and add no K/D column.
- [x] Keep `/players` and `/players/current.json` read-only: no ingest, scanner, sessionizer, maintenance, counter persistence, or `players.db` creation from GET.
- [x] Expose only safe source/freshness/window terms; no raw log lines, paths, IPs, secrets, raw correlation IDs, Discord/public enrichment, daemon, or timer.

### Slice F: UI Smoke And Cleanup

- Verify the implemented `/players` details, nullable rendering, safe unavailable wording, and browser polling behavior in an approved environment.
- F2-b VM acceptance is complete on Serhiivka and Chervonopilya: the timer was explicitly enabled, repeated cycles stayed fresh/non-overlapping, and the separate player-session scheduler remained disabled.
- Verify no fake zeroes and no accumulation across real new sessions; stats still require a proven open play session.
- Chervonopilya deployment followed successful Serhiivka evidence and explicit approval.

### Slice G: Discord/Public Evaluation

- Revisit Discord player columns only after Slice F is stable.
- Keep role blocked unless a source was added.
- Decide whether Discord should show current session stats, last session stats, or no combat stats.

## Acceptance Checklist

- [x] Ingest foundation has checkpoint/freshness metadata and no longer requires rescanning unchanged logs from the manual button.
- [x] Log freshness does not require manual Update events from logs.
- [x] Manual log collection remains available but is not the only freshness path.
- [x] A player reconnecting within 10 minutes after a compatible network/drop absence continues the same play session.
- [x] A player reconnecting after the grace window starts a new play session.
- [x] Server lifecycle boundary always starts a new play session.
- [x] Reliable ID conflict blocks session merge.
- [x] Kills, deaths, and TK are counted only inside the current proven play-session window.
- [x] Teamkills do not increment Kills.
- [x] AI/unknown instigator does not increment a player kill/TK.
- [x] Faction is labelled as last-known session evidence.
- [x] Role remains placeholder-only unless a reliable source is added.
- [x] No fake zeroes when stats freshness/session binding is missing.
- [x] Re-running ingest/session jobs does not duplicate stats because stored events are deduped and counters are recomputed read-only.
- [x] /players and /players/current.json stay GET-read-only for players.db and session/stat state.
- [x] Job output and audit details are counts-only and sanitized.
- [x] No raw log lines, raw paths, raw RCON rows, IPs, secrets, public player IDs, or Discord enrichment are introduced.
- [x] Serhiivka VM smoke passed before Chervonopilya deployment and observation.

## Immediate Next Recommended Slice

F2-a/F2-b/F2-c automatic log freshness is accepted on both target VMs. The next player-truth slice should prove the complete automatic session pipeline with real players: reliable session open/close and reconnect behavior, coverage from session opening, and current-player nullable-to-real stat transitions without pressing manual buttons. Design any supervised session scheduler/service explicitly before enabling it; keep GET/browser triggers, fake zeroes, and hidden threads forbidden. Discord/public enrichment and historical oversized-log backfill remain separate later decisions.
