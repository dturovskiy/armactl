# Player Session Detail And Search Contract

Status: **Slice 6b query/DTO foundation complete. Slice 6c UI remains gated.**

This document is the source of truth for the next authenticated player-session read surface after F3-c production acceptance. It defines search, one-session detail, conflict semantics, reuse ownership, privacy, pagination, and implementation slices without adding a second player truth pipeline.

## Decision Summary

Implement one read-only server-rendered session detail page and extend the existing session list search. Do not add a public JSON API in the first implementation. Do not load event details for every list row.

The list remains `/players/sessions`. A new authenticated detail route may use `/players/sessions/{session_id}` where `session_id` is an internal record locator, not a public player identifier. The list keeps its compact columns and existing closed-by-default evidence row; the dedicated detail page carries richer session evidence and bounded event/stat context.

## Current State Audit

Existing reusable owners:

- `player_registry.list_player_sessions(...)` already provides a bounded SQLite read-only list with exact reliable-ID, session-name, status, end-reason, source, and limit filters.
- `player_registry.summarize_player_sessions(...)` already owns the counts-only list summary.
- `player_registry.PlayerSessionRecord` already sanitizes persisted session fields.
- `page_models.players.load_player_sessions_page(...)` already normalizes list filters and builds the existing page DTO.
- `players_sessions.html` already keeps the main table compact and reveals safe evidence through an explicit Details action.
- `player_current_enrichment` already owns the proven play-session/fresh-ingest gates and Kills/Deaths/TK/Faction aggregation rules for an open current session.
- `player_registry.list_player_log_events(...)` and the player-history page already own safe event labels and default-versus-diagnostic event classification.
- shared browser-local `<time data-local-time>` rendering remains the only normal timestamp presentation path.

Slice 6b foundation now implemented:

- `player_registry.get_player_session_readonly(...)` returns a typed controlled result through SQLite URI `mode=ro` plus `PRAGMA query_only = ON`; it never calls schema ensure/migration. Existing mutation-oriented `get_player_session(...)` and `list_player_sessions_for_reliable_id(...)` callers remain unchanged for the sessionizer/writer path.
- `player_registry.query_player_sessions(...)` owns the bounded parameterized alias-aware query, UTC range filters, and allowlisted status/end-reason/source filters. Invalid filters fail closed. Alias matching uses `EXISTS` against `player_names`, so one stored session appears once and same-name reliable identities stay separate.
- The new session query uses deterministic keyset pagination over validated evidence time and descending `session_id`, fetches at most bounded `limit + 1`, and uses no `OFFSET`.
- `player_registry.query_player_session_events(...)` owns the bounded trusted-occurrence-time event query for one proven session window. It uses exact/derived occurrence evidence, reliable identity matching, and an event-time plus `event_id` keyset cursor.
- `player_session_stats` now owns the shared freshness, session-window, lifecycle/server-run, combat, faction, and reconnect evaluation used by both current-player enrichment and future detail DTOs. It supports proven open and closed stored sessions.
- `player_session_details` provides sanitized typed search/detail DTOs and controlled invalid/not-found/unavailable results without routes, templates, CSS, browser JavaScript, or JSON API work.
- `list_player_summaries(...)` remains all-time stored-event data and is not used for session detail or current-session truth.

Remaining runtime gaps:

- There is still no one-session detail route or template.
- List cursor controls, alias/time filter UX, browser-local rendering, and presentation of the already bounded high-signal detail timeline remain Slice 6c work.
- Slice 6d VM smoke remains staged and approval-gated.

## P1 Gates Before Runtime UI - closed by Slice 6b

1. The new detail lookup is explicitly query-only and covered for missing/current/legacy storage without changing sessionizer mutation helpers.
2. Current enrichment and detail stats use the same evaluator and classification SQL.
3. Session-list and proven-window event history now have bounded deterministic keyset queries with no unbounded Python load.
4. Alias search discovers sessions by reliable identity without identity merging or duplicate rows.

Runtime implementation must stop if any gate would require a parallel storage layer, GET mutation, or duplicated stats SQL.

## Route And UX Contract

### Session List

Keep `/players/sessions` authenticated and GET-read-only.

Main columns remain:

- Player
- Session state
- First evidence
- Last evidence
- Close evidence
- Available actions

Existing Details continues to reveal only lightweight evidence in-row. Add an `Open session` action only when a valid stored `session_id` exists.

Supported filters:

- `q`: bounded case-insensitive nickname search across session names and known aliases.
- `player_id`: exact normalized reliable player ID.
- `status`: allowlisted stored session state.
- `end_reason`: allowlisted close reason.
- `source`: allowlisted open/last/close evidence source.
- `from` and `to`: optional validated UTC bounds submitted from browser-local controls.
- `limit`: bounded page size.
- `before_time` plus `before_session_id`: validated keyset cursor for older rows.

Ordering remains newest evidence first, then descending `session_id` as a stable tie-breaker. Filter and cursor values must never be interpolated into SQL.

### Session Detail

The initial detail surface is server-rendered HTML only. It must require the same authenticated `players:view` permission as the existing player pages. Missing or invalid session IDs return the normal sanitized not-found page, not a traceback or database detail.

Visible summary:

- current/last recorded nickname and first recorded nickname when different;
- stored session state and truth-safe explanation;
- first, last, and close evidence times in browser-local time;
- safe source labels and confidence labels;
- close reason;
- reconnect merge count and last reconnect evidence time;
- last gameplay evidence time/source/confidence;
- last-known faction/side evidence, explicitly not guaranteed current;
- Kills, Deaths, and TK only from the shared proven session-window evaluator;
- freshness/coverage status and a controlled unavailable reason;
- link back to the preserved list filters.

Bounded high-signal event timeline:

- default to stable player events associated with the session reliable ID and proven session window;
- exclude ambiguous-time rows from stats and label them only in an explicit diagnostics disclosure if retained for evidence;
- use the existing player-history labels and sanitization;
- cap the initial page and use a keyset cursor for older events;
- never render raw lines, raw source refs, paths, IPs, connection IDs, RPL identities, BattlEye slots, or internal server-run keys.

Do not expose `play_session_id`, `server_run_key`, raw correlation fields, or source refs. `session_id` may appear only as the authenticated route record locator and optional operator record label.

## Session Stats Reuse Contract

One evaluator must accept a sanitized stored session plus freshness coverage and produce a nullable DTO:

- `stats_available`
- `stats_unavailable_reason`
- `kills`
- `deaths`
- `teamkills`
- `faction`
- `covered_from`
- `covered_through`
- `window_started_at`
- `window_ended_at`
- `reconnect_merged`

For an open session, the window ends at fresh ingest `last_success_at`. For a closed session, it ends at `close_observed_at` only when fresh proven coverage reaches that close. The evaluator must keep the existing stable event/time, lifecycle-boundary, server-run, AI, victim/instigator, teamkill, and faction rules.

A value of `0` is allowed only after the complete proven query returns no matching events. Missing coverage, schema, reliable identity, lifecycle proof, or a valid session window returns null values and a controlled reason.

## Identity And Conflict Policy

- Reliable ID is the identity key. Nickname is evidence, never identity truth.
- Alias search may find sessions through `player_names`, but it must return each stored session once.
- Two reliable IDs sharing a nickname remain separate players and separate result rows.
- A reliable ID changing names remains one identity; detail may show safe alias history separately.
- Conflicting or overlapping historical evidence is displayed as a diagnostic state; GET never repairs, merges, closes, or deletes it.
- Reconnect metadata reports the stored F3 decision. The UI does not recompute or override reconnect merges.
- Lifecycle/server-run compatibility is reported as proven/unavailable without exposing the raw server-run key.
- Name-only, A2S count-only, ambiguous-time, or unreliable evidence cannot create a detail identity or make stats available.

## Read-Only And Security Rules

All list/detail/event queries must:

- open an existing database with SQLite `mode=ro` and `PRAGMA query_only = ON`;
- return an empty/not-found/unavailable DTO when the database or required schema is absent;
- never call registry ensure/migration, ingest, sessionizer, scanner, maintenance, jobs, current-cache mutation, or pending-work code;
- use bounded limits and parameterized SQL;
- sanitize operator-facing errors and labels;
- preserve CSRF only for existing POST job controls; the new detail/search GET adds no mutation;
- retain existing auth and `players:view` authorization;
- render times through the shared browser-local renderer with UTC only as the machine/storage contract.

Forbidden output:

- raw log lines or source references;
- filesystem paths;
- IP addresses;
- secrets/tokens;
- RCON rows;
- connection IDs, RPL identities, session-player IDs, or BattlEye slots;
- raw server-run/play-session keys;
- public player IDs;
- exact joined time, playtime, Role, K/D, or guaranteed current faction claims.

## Implementation Slices

### Slice 6a: Audit And Contract - complete

- [x] Trace existing list route, page model, registry queries, current stats evaluator, event history, template, and tests.
- [x] Record reuse owners and P1 gates.
- [x] Decide server-rendered detail first; no new JSON API.
- [x] Define query, pagination, identity conflict, privacy, and acceptance contracts.

### Slice 6b: Query And DTO Foundation - complete

- [x] Add query-only single-session read plus bounded keyset session-list and proven-window event queries; keep only timeline presentation/integration for Slice 6c.
- [x] Add alias-aware session search without identity merging or duplicate rows.
- [x] Extract/reuse one session-window stats evaluator for open and closed session detail.
- [x] Add typed sanitized detail/search DTOs and missing-schema/unavailable behavior.
- [x] Add focused service tests for query-only access, bounds, ordering, conflicts, freshness, lifecycle, and no fake zeroes.

Stop after Slice 6b if a route/template would need duplicated SQL or if closed-session coverage cannot be proven truthfully.

### Slice 6c: Authenticated UI

- [ ] Add the thin authenticated detail route and server-rendered template.
- [ ] Add list keyset navigation, alias/time filters, preserved back-link state, and browser-local time inputs/display.
- [ ] Keep details responsive and closed/secondary by default; do not widen the compact table.
- [ ] Add EN/UK labels and route/template/i18n/read-only regression tests.

### Slice 6d: VM Smoke

- [ ] Deploy to Serhiivka first without restarting the game server.
- [ ] Verify list filters, aliases, pagination, detail, nullable stats, local time, not-found, and no sensitive output with a normal authenticated session.
- [ ] Review web journal for 500/traceback and confirm GET causes no player DB/session/job writes.
- [ ] Proceed to Chervonopilya only after Serhiivka acceptance and explicit approval.

## Likely Files

- `src/armactl/web/services/player_registry.py`
- `src/armactl/web/services/player_current_enrichment.py`
- possible shared `src/armactl/web/services/player_session_stats.py`
- possible `src/armactl/web/services/player_session_details.py`
- `src/armactl/web/page_models/players.py`
- `src/armactl/web/routes/players.py`
- `src/armactl/web/templates/players_sessions.html`
- new session detail template
- `src/armactl/web/static/css/app.css`
- `src/armactl/locales/en.json`
- `src/armactl/locales/uk.json`
- focused player registry/current enrichment/route/i18n tests

Do not create every optional module by default. Add a shared stats module only when it removes real duplication between current enrichment and session detail.

## Out Of Scope

- session mutation or conflict repair;
- ban/kick/banlist;
- moderation actions;
- public or Discord enrichment;
- K/D, Role, exact joined time, or playtime;
- IP or raw evidence storage;
- materialized counters;
- full REST/JSON player API;
- bulk export;
- live journal readers;
- browser/GET/app-start/background-thread mutation;
- historical oversized-log backfill.

## Acceptance Criteria

- Existing `/players/sessions` behavior and filters remain compatible.
- New search finds aliases without merging same-name reliable identities.
- Detail selects exactly one stored session and uses query-only SQLite access.
- Closed/open stats share one evaluator and remain nullable unless coverage and lifecycle proof pass.
- Keyset pagination is deterministic with no duplicates across pages.
- GET with missing/legacy DB returns controlled empty/not-found/unavailable output and performs no migration or write.
- UI stays compact, responsive, browser-local for human times, and explicit about stored evidence versus live truth.
- No forbidden sensitive/internal fields appear in HTML, URLs except the authenticated session record locator, logs, errors, or DTOs.
- Focused tests, Ruff, full pytest when shared stats/query behavior changes, and `git diff --check` pass before deployment.
