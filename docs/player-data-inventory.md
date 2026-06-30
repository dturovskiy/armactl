# Player Data Inventory

This inventory records the current implemented player-data flow before the players/history/banlist work. It is based on the current code in `src/armactl`, `src/armactl/web`, `src/armactl/tui`, `src/armactl/cli.py`, and the related tests.

## Current Implemented Sources

| Feature / page / module | Source | Storage | Freshness | Privacy risk | Notes |
| --- | --- | --- | --- | --- | --- |
| `player_view.query_player_view` | A2S count via `query_player_status`; optional RCON roster via `query_player_roster` | None | Live per call, bounded by caller timeouts | Medium when roster is enabled, because names and GUID-like IDs may be observed | RCON roster wins for current count when available; A2S remains fallback for count and `maxPlayers`. |
| A2S status (`src/armactl/a2s.py`) | Steam A2S_INFO on configured local query host/port | None | Live per UDP query; returns zero when the server is stopped | Low | Count, max players, map/server strings only; no player identities. |
| RCON roster (`src/armactl/rcon.py`) | BattlEye RCON commands `#players` then `players` | None | Live per RCON query | High/moderation data | Parses player name, slot ID, and GUID-like values. Uses the configured RCON password only for the session; no ban/kick commands are implemented. |
| Dashboard player count (`/dashboard`) | `player_view.query_player_view(..., include_roster=False)` from `web/page_models/dashboard.py` | None | Live dashboard snapshot / poll | Low | Count-only dashboard view. It avoids roster names/IDs for the overview card. |
| Public website status (`/public/server-status.json`) | Dashboard snapshot transformed by `web/routes/public_status.py` | None | Live per public request | Low/public | Publishes server status, scenario, player count/max text, performance and operational status. It does not publish player names, IDs, paths, sessions, or CSRF/session data, and fails closed on snapshot errors. |
| Public stats / Discord (`public_stats.py`, `discord_stats.py`, CLI `stats public`) | `player_view.query_player_view(..., include_roster=True)`, config summaries, FPS metrics | Discord config stores webhook/message metadata; no player history storage | On CLI request or Discord publisher interval | Medium/public | Discord/public text may include up to 8 sanitized current player names. It does not include player IDs, IPs, admin actions, paths, or secrets. Mentions are neutralized. |
| Admins current-player quick-add (`/admins`) | `players_page_model.load_player_moderation_panel` -> `player_sources.load_current_player_roster` -> RCON roster | None until an admin action is submitted | Live per page load/search | High/moderation data | Shows sanitized current names and reliable IDs to authenticated operators. `Add as admin` is available only when `admin_reference` is reliable. |
| Admin add/update/remove (`/admins/add`, `/admins/remove`) | Operator form or quick-add player row | Official IDs in `config.json` `game.admins`; labels in `admins-state.json`; pending restart in `web.db` / fallback file | Mutates immediately; server restart may be required | High/moderation data | Existing audited admin workflow, not a ban/kick workflow. Supports SteamID64, IdentityId, Steam profile URL, and vanity lookup. |
| Current players page (`/players`) | `player_sources.load_current_player_roster` through the page model | None | Live per GET, bounded by roster timeouts | High/moderation data | Shows sanitized current roster names/IDs/source to authenticated operators. GET does not update `players.db`, calculate session K/D, infer role/joined time, or manage bans/kicks. |
| Known players directory (`/players/known`) | `player_registry.list_known_players` | `~/armactl-data/<instance>/players.db` | As fresh as last event ingest or current-roster refresh job | High/moderation data | Identity directory for known reliable players, current name, first/last seen, count, and source. Basic query matches reliable ID, current name, and historical names. Not a combat-stat board. |
| Current-roster registry refresh (`POST /players/refresh-current`, compatibility alias `/players/refresh`) | Current roster from `player_sources.load_current_player_roster` | `players.db` tables `players`, `player_names`, `player_registry_schema_meta`; `web.db` job metadata; web audit log | Explicit operator POST; background job dedupes queued/running refresh | High/moderation data | Route accepts CSRF only and enqueues `players:refresh-current`. Service/job record only reliable player IDs and first/last seen. Unreliable/slot-only rows are counted and ignored. Job/audit output stores observed/stored/ignored/source/status counts only, not player names, paths, IPs, raw lines, joined time, role, session K/D, ban/kick, or Discord enrichment. |
| Player log event DB ingest foundation (`player_registry.ingest_player_log_events`) | Parsed `PlayerLogEvent` DTOs from `player_log_events`; no live reader or collector | `players.db` table `player_log_events`, plus existing `players`/`player_names` observations for reliable IDs | Only when a caller submits parsed events; no automatic freshness | High/moderation data | Stores sanitized structured auth/update/faction/combat fields with source/ref/confidence and a dedupe key. Does not store raw log lines or player addresses. No live scanner, sessionization, Discord K/D, or banlist behavior. |
| Manual player log collector/import CLI (`player_log_collector`, `player-history collect`) | Explicit operator-supplied regular text log file paths only | Existing `players.db` through `player_registry.ingest_player_log_events`; dry-run writes nothing | Manual CLI run only | High/moderation data | Reads accepted files line-by-line with max byte/line bounds; files over the byte limit fail closed. Source refs are sanitized basename+file-marker+line only. Summaries/errors use safe labels. No live `journalctl`, daemon, web UI, raw log-line storage, absolute path storage, or IP/address storage. |
| Player history events page (`/players/history`) | `player_registry.list_player_log_events` over already stored `player_log_events` | Existing instance `players.db`; read-only | As fresh as the last manual collector/import or other ingest caller | High/moderation data | Authenticated `players:view` page lists bounded stored events newest first, with event-type, reliable-ID, and text/name filters. It shows sanitized source refs only and never reads log files, mutates state, displays raw paths, raw log lines, IPs/addresses, secrets, ban/kick state, sessions, playtime, K/D, or Discord enrichment. |
| Manual player log web collection (`/players/history/collect-logs`) | Allowlisted current-instance server profile logs already used by armactl telemetry: `config/logs/*/console.log`; no request-supplied paths | Existing `players.db` through `player_log_collector.collect_player_log_events` and `player_registry.ingest_player_log_events`; web job metadata in `web.db`; web audit log | Manual operator POST only; background job dedupes queued/running collection | High/moderation data | The route accepts CSRF only and never reads arbitrary paths. The job bounds max files/bytes/lines, fails closed for oversize files through the collector, audits intent/outcome counts, and does not store/display raw absolute paths, raw log lines, IPs/addresses, sessions, playtime, ban/kick state, automatic poller state, or Discord K/D. |
| Player session writer helpers (`player_registry.observe_player_session`, `player_registry.close_player_session`) | Explicit service-layer caller evidence with normalized reliable IDs; no route/job invokes them yet | `players.db` tables `player_sessions`, plus `players`/`player_names` identity observations | Only when a service caller explicitly invokes the helper; no automatic freshness | High/moderation data | Opens/updates one open session per reliable ID and closes open sessions with sanitized source/ref/confidence/end-reason/faction/side/correlation fields. Rejects unreliable IDs and stores no IPs, raw log lines, raw absolute paths, public IDs, or secrets. Not a live scanner, poller, retention job, UI/API page, current-roster refresh side effect, or current-session K/D/role/joined source. |
| TUI player status (`src/armactl/tui/**`) | Shared `player_view` and config/status summaries | None | Live per TUI refresh/action | Medium | TUI can show RCON roster names when configured; no persistent player history. |
| Telegram bot player/status output (`telegram_bot.py`) | Shared `player_view` | Bot `.env` stores bot config, not player history | Live per bot command/callback | Medium/private chat | Can show count and roster details when RCON is configured. Not part of public Discord stats. |
| FPS/logStats metrics (`metrics.query_server_fps_metrics`) | Latest `config/logs/*/console.log` tail | Server runtime log files, not a player DB | Latest console log mtime; stale after configured age | Low | Parses aggregate FPS/frame/memory/players/AI from `-logStats`; not identities, sessions, or connect/disconnect events. |
| Web logs/report (`/logs`, `/report`) | Allowlisted audit log, server journal, bot journal, web journal, diagnostic report | Source logs only | Tail at request time | Medium | Bounded and redacted. Potential server log lines are visible to authenticated log viewers but are not parsed into player history. |
| ServerAdminTools guard (`sat_admin_guard.py`) | `ServerAdminTools_Config.json` and optional SAT UUID map | SAT config and `sat-admin-uuid-map.json` | Pre-start/check-time | Medium | Only repairs admin/gameMaster placeholders and clears placeholder-only `bans`. It is not a banlist manager. |

### Dashboard / Public Status

Dashboard and public status intentionally use count-first paths. The dashboard calls `player_view` with `include_roster=False`, so it avoids player names and IDs. The public JSON route derives a smaller DTO from the dashboard snapshot and publishes only counts/text plus server/performance status.

### Admins Quick-Add

The quick-add player list on `/admins` is not a separate registry. It is a live sanitized current roster. Reliable player IDs become `admin_reference` values and are posted into the existing audited admin add/update flow. Slot-only or unreliable rows remain read-only.

### Discord Stats

Discord stats use `public_stats.load_public_stats`, which enables roster lookup and publishes a bounded current-name preview. This is the only current public surface that intentionally exposes player names. It does not expose player IDs, admin status, IPs, RCON details, paths, or secrets.

### Player Refresh / Registry

`POST /players/refresh-current` is the browser/operator-triggered current-roster persistence foundation; `/players/refresh` remains a compatibility alias to enqueue the same job. The route accepts CSRF only, while the `players:refresh-current` job records current reliable IDs into `players.db`, updates current name/name history/first seen/last seen, dedupes queued/running refresh jobs, and writes intent/outcome audit events with counts only. `/players` GET remains read-only and does not update last seen. The manual `player-history collect` CLI can parse explicitly supplied bounded text log files and feed sanitized DTOs into the same `players.db` event ingest path, or run as a dry-run without writing. `/players/history` reads only those already stored event rows. `/players/history/collect-logs` is a manual web background job that scans only allowlisted current-instance `config/logs/*/console.log` files and never accepts a path from the request. No web request reads arbitrary log files. None of these paths creates online/offline session state, joined/role/current-session K/D facts, ban records, kick records, retention cleanup, or Discord enrichment.

### Real Server Log Inventory

See [player-log-event-inventory.md](player-log-event-inventory.md) for the read-only pass over real game logs before player history/statistics work. The observed logs can support a cautious sessions/history slice for connect/disconnect, last seen, faction snapshots, mission lifecycle, and aggregate count/FPS telemetry. Combat stats are feasible on the checked deployments through script-emitted `INFO: KILL ...` lines, with optional ServerAdminTools wrappers on some servers; they must be stored with an explicit source/capability flag and should not be treated as vanilla/no-mod functionality.

### Phase 4a Session Tracking Design

This section is a design contract for runtime behavior. Phase 4b adds a `player_sessions` schema/vocabulary foundation in `players.db`, and Phase 4c adds explicit service-layer open/close writers. The current code still has no live scanner, no automatic poller, no retention job, and no hidden writes from player GET routes.

#### Session Boundary Events

- Open high-confidence sessions from parsed backend authentication or network player-update log events when they include a normalized reliable `identityId`/`IdentityId` and an observation timestamp.
- Open medium-confidence sessions from a future scanner's first successful RCON roster observation of a reliable ID when no session is already open. The timestamp is `first observed online`, not an exact join time. The existing `players:refresh-current` job remains registry-only and must not open sessions.
- Open inferred presence sessions from faction/combat events only when they include a reliable player ID and no active session exists. These rows must be labelled as presence-inferred, not explicit connects.
- Never open an identified player session from A2S player count, FPS `Player: N` telemetry, `/players` GET, or a manual current-roster refresh by itself.
- Close high-confidence sessions from disconnect events that carry the same reliable ID as an open session. If future parsing only has `rplIdentity`, connection ID, BE slot, or name, close only when that correlation maps to exactly one active session and record the lower confidence.
- Close sessions from server lifecycle markers such as shutdown/save/service stop as forced server-boundary closes with an explicit end reason.
- Close stale sessions only after a configured grace period with repeated successful observations that no longer include the player, or after a scanner checkpoint proves the source window has advanced past the last seen event.
- Never close an identified player session from one failed RCON query, one A2S mismatch, or an old manual import that predates the current live scanner checkpoint.

#### Source Confidence

- Reliable identity sources: normalized reliable IDs from RCON roster rows, backend authentication lines, network player updates, faction lines, combat lines that include stable UUIDs, and the existing `players` registry.
- Reliable or high-confidence boundary sources: auth/update connect lines with reliable IDs, explicit disconnect lines with reliable IDs, and service shutdown markers for closing all active sessions as server-boundary events.
- Heuristic sources: RCON roster deltas, A2S/FPS counts, disconnect lines that only include per-run correlation fields, faction/combat events as presence evidence, and imported historical logs with missing start or end windows.
- Mod-dependent sources: generic script `INFO: KILL ...` lines and optional ServerAdminTools wrappers for combat evidence. They may enrich historical events after capability detection, but must not become vanilla/no-mod K/D claims.

#### Current Roster Observation Versus Session Truth

- The live current roster is a snapshot. `/players` GET may display that snapshot with source/freshness, but it remains read-only and must not update `players.db` or create session rows.
- `POST /players/refresh-current` persists reliable identity observations into `players` and `player_names`; it does not assert online status, joined time, role, faction, or session K/D.
- Persisted session rows can be written only by explicit service helpers today. Live online/offline truth starts only when a future scanner/job writes `player_sessions` rows with source refs, confidence, and open/close evidence. UI labels should say `first observed`, `last observed`, or `inferred close` unless an explicit connect/disconnect event backs the stronger wording.
- Manual historical imports should sessionize by their own observed timestamps and source refs. They must not mutate live online/offline state for the current server process.

#### Session Schema Without IP Storage

The Phase 4b `player_sessions` migration stores only bounded structured fields: `session_id`, `reliable_id`, sanitized `name_at_open` and `name_last`, `open_observed_at`, `last_seen_at`, optional `close_observed_at`, `status`, `open_source`, `last_seen_source`, `close_source`, sanitized source refs, `open_confidence`, `last_seen_confidence`, `close_confidence`, `end_reason`, nullable correlation fields such as `rpl_identity`, `connection_id`, `session_player_id`, and `be_slot`, optional last faction/side snapshots, scanner checkpoint metadata, and created/updated timestamps. It indexes reliable ID, open/last/close time, status, and source, and prevents more than one open session per reliable ID unless a future multi-server model explicitly changes that rule. Phase 4c service helpers can write rows only when explicitly called; no current route/job writes session rows.

Optional evidence/link tables can map session rows back to stored `player_log_events` or roster observations by event ID, observed time, source, source ref, confidence, and dedupe key. They must not store IP addresses, external GUID/hash fields by default, raw log lines, raw absolute paths, RCON output, public player IDs, secrets, or request payloads.

#### Retention And Cleanup Rules

- Define per-instance retention settings before enabling long-lived session writes. At minimum, separate windows are needed for closed sessions, raw parsed event rows used as evidence, scanner checkpoints, and web job/audit records.
- Do not automatically delete `players` or `player_names` identity rows during routine session cleanup; provide an explicit reset/export decision if identity erasure is later required.
- Close stale open sessions before pruning evidence, so cleanup does not strand permanently open rows.
- Run cleanup only from an explicit job/CLI or scheduled backend task, never from GET routes. Audit counts, cutoff timestamps, and reason classes only.
- Prune in bounded batches and keep source refs sanitized. Cleanup output must not include player names, raw paths, raw log lines, IPs, or secrets.
- Add migration and cleanup tests before the scanner writes long-lived data: no-IP schema assertions, stale-close behavior, retention cutoff behavior, idempotence, and redacted audit/job output.

#### Conflict Rules

- RCON roster with reliable IDs wins for identity and current roster display when available. A2S remains count-only and may disagree with RCON without creating synthetic player rows.
- Log auth/update events with reliable IDs can open/update sessions even when RCON is unavailable. Log disconnect events can close a matched session even if RCON or A2S lags.
- RCON absence should close a session only after successful fresh roster samples and a grace period. RCON failure is unknown state, not absence.
- A2S `0` or count drops can support stale/empty heuristics but cannot identify who left. Use it only with roster/log evidence or server lifecycle markers, and label low-confidence closes accordingly.
- Faction/combat events update last-seen/faction evidence for an active or inferred session. They do not prove an exact join time, role, or current K/D.
- Backfilled historical imports must not override newer live scanner state; conflict resolution uses event `observed_at`, source confidence, source ref dedupe, and scanner checkpoint order.

#### Truthful UI/API Scope

- Safe now: authenticated current roster as `current observation`, known reliable identity directory, stored event history with bounded filters, registry first/last seen, source/freshness labels, and count-only public status.
- Safe after session implementation: historical session list/detail with first observed, last observed, close reason, duration when both ends exist, confidence/source labels, stale/open warnings, and source disagreement notices.
- Blocked until better evidence: exact joined time from roster-only data, current role/loadout, current-session faction without a recent event timestamp, current-session K/D, public reliable IDs, public session pages, Discord K/D/playtime/faction columns, ban/kick/banlist manager state, IP storage, raw source display, automatic long-running poller, and any hidden DB write from GET routes.

## Existing Storage

| Storage | Owner / path | What is stored | What is not stored | Retention / cleanup |
| --- | --- | --- | --- | --- |
| Player registry DB | `~/armactl-data/<instance>/players.db` | `players`: reliable ID, current name, first/last seen, seen count, last source. `player_names`: reliable ID, name, first/last seen, seen count. `player_log_events`: deduped parsed event kind, observed/log timestamp, source/ref/confidence, sanitized identity/name/session/faction/combat fields. `player_sessions`: Phase 4b schema/vocabulary plus Phase 4c explicit service writer foundation for reliable session observations/closes. Schema meta. | IPs/player addresses, raw log lines, raw absolute paths, secrets, RCON password, admin password, live online/offline truth, ban/kick history, Discord K/D projections, raw RCON rows. | No player retention/cleanup currently. File is forced to mode `0600`. |
| Web runtime DB | `~/armactl-data/web/web.db` | Web users, sessions, CSRF tokens, jobs, server version checks, login rate limits, pending restarts/work. | Player registry/history/session tables. | Sessions/CSRF have expiry fields; login rate-limit rows are pruned by auth code; job rows have no player retention. File is mode `0600`. |
| Web audit log | `~/armactl-data/logs/web/audit.log` | JSON-lines records for mutating web actions. Player refresh stores phase/source/counts and controlled failure metadata. Player log web collection stores intent/outcome phases, job kind, allowlist scope, limits, and aggregate counts only. Admin actions store target ID and changed status. | Raw player names for player refresh, raw log lines, raw absolute log paths, IPs, passwords, RCON secrets, tracebacks. | No audit retention/rotation in current code. Web log view tails and redacts output. |
| Game config | `~/armactl-data/<instance>/config/config.json` | Official `game.admins` ID list, server config including A2S/RCON ports and configured secrets. | Player registry, sessions/history, banlist manager state. | Config backups are managed by config save flows; no player-specific retention. |
| Admin label sidecar | `~/armactl-data/<instance>/admins-state.json` | Local display labels and sources for game admins. | Player history, IPs, secrets, ban/kick state. | Written atomically with mode `0600`; no retention. |
| SAT UUID map / SAT config | `sat-admin-uuid-map.json`, `config/ServerAdminTools_Config.json` | Optional SAT UUID mappings; SAT config admins/gameMasters and existing SAT `bans` field. | No armactl ban manager records. | SAT guard can clear placeholder-only `bans`; no ban retention policy. |
| Discord stats env | `~/armactl-data/<instance>/bot/discord-stats.env` | Enabled flag, webhook URL, interval, Discord message ID, instance. | Player history and roster cache. | Written with mode `0600`; no player retention. |
| Server console logs | `~/armactl-data/<instance>/config/logs/*/console.log` | Server runtime logs and `-logStats` telemetry. | Structured armactl player sessions/history. | `cleaner.py` can delete log files under the instance config tree when operator cleanup is run; metrics only reads bounded tails. |

## Existing Audit/Security Behavior

- Current-roster registry refresh is queued as `players:refresh-current` and audited as `players.refresh-current` with intent before enqueue and outcome after job completion. The legacy synchronous helper still audits `players.refresh` in direct service tests.
- Player refresh audit/job details include phase, job kind, observed/stored/ignored counts, source, status, and controlled failure class/message. Tests assert display names, raw paths, raw log lines, IPs, and raw secrets do not enter player refresh job output or audit details.
- Admin add/update/remove is audited as `admin.add`, `admin.update`, or `admin.remove`; it records the target admin reference and changed status. Admin changes also mark pending restart/work when needed.
- Discord stats configuration, publish, and service actions have secret-safe audit paths. Webhook URLs are masked or omitted from operator-facing output.
- Logs/report views are allowlisted, line-limited, byte-bounded, and redacted for common secrets, session/CSRF values, and Argon2 password hashes.
- Public status fails closed with a generic error. Unknown log sources and permission failures return controlled responses without tracebacks.
- Player IDs/names are visible on authenticated moderation pages and stored in `players.db`; treat them as moderation data. Public status does not publish names or IDs. Discord stats publishes a bounded current-name preview by design.
- No code currently adds IP storage for players. Tests assert player registry/event tables do not contain `ip`, `ip_address`, or raw-line columns, event ingest redacts address-like values before storage, and `/players/history` does not render raw source paths, IPs, or raw log lines.
- Existing RCON code reads the configured password to query the roster but does not persist it and does not implement ban/kick commands.
- One should-fix for future mutation work: unexpected admin backend exceptions are rendered as generic 500 without traceback/secrets, but tests show a backend mutation can occur before the exception without pending-work recovery. New moderation mutations should avoid that shape by using explicit rollback/transaction boundaries.

## Gaps Before Richer Player History

### Sessions / History

- Read-only stored event history is implemented for `player_log_events`, Phase 4b adds the `player_sessions` schema foundation, and Phase 4c adds explicit service writer helpers; richer session/history semantics still need a scanner job, retention policy, and conflict handling before any live online/offline/session truth is produced.
- Phase 4a now distinguishes reliable connect/session signals from heuristic disconnect pairing, roster deltas, A2S count hints, and mod-dependent combat events; implementation still needs to enforce those rules in code.
- Manual explicit log-file CLI import exists for bounded text files, and manual web collection exists only for allowlisted current-instance config console logs. Future work still needs to decide any live scanner/dashboard poll trigger; the current code has no automatic session recorder or poller.
- Implement the Phase 4a freshness/conflict rules for A2S count, RCON roster, and stored log events. A2S cannot identify players; RCON can identify some players but can be unavailable.
- Implement bounded retention and cleanup before storing long-lived session data.
- Keep IP storage out unless there is a separate explicit product/security decision and migration.

### Search By Nickname / ID

- Existing `list_known_players` already supports basic `LIKE` search across reliable ID, current name, and historical names.
- Existing `list_player_log_events` supports bounded event-type, reliable-ID, and text/name filters across stored event fields.
- Add normalized/case-stable search helpers and indexes if the registry grows.
- Add session-aware filters only after session storage exists: last seen range, online/offline status, source, and known-name history.
- Add detail views or APIs that expose bounded name history without leaking raw logs or secrets.

### Banlist Manager

- Pick and document the actual source of truth before implementation: game config, SAT config, or RCON command flow. Current code only has SAT placeholder cleanup and no RCON ban/kick commands.
- Validate ban targets with the same reliable identity rules used by admins/player registry.
- Add audited dry-run/confirm flows, backups for config/SAT mutations, controlled rollback, and pending restart/work behavior where applicable.
- Do not store RCON/admin passwords, raw secrets, or IPs in ban records by default.
- Keep ban/kick implementation separate from the current inventory and read-only player slices.

### Rollback / Audit

- Keep the existing intent-before-mutation audit pattern for any player/moderation mutations.
- For mutations touching multiple files or DBs, write rollback tests like admin sidecar/config rollback tests.
- Audit bounded summaries, not raw player rosters. Use IDs/counts/reason classes carefully and redact messages.
- Add tests that prove no tracebacks, raw secrets, webhook URLs, RCON passwords, or IPs appear in operator-facing errors.

## Privacy/Safety Rules

- Do not add IP storage without a separate explicit decision.
- Do not store raw secrets, RCON passwords, admin passwords, webhook URLs in audit details, or session/CSRF tokens in player storage.
- Treat player IDs and names as moderation data.
- Keep audit bounded and redacted. Prefer counts, source labels, reason classes, and controlled messages over raw rows.
- Public status must not expose extra personal data. Keep `/public/server-status.json` count/status only unless a future public-data review explicitly changes it.
- Discord player-name enrichment must stay bounded and mention-safe; richer public columns should wait for reliable history/session data.

## Recommended Next Slices

- Slice 2: read-only players page / improved players view from the existing live roster, registry, and stored event history, without new schema or moderation mutations.
- Slice 3: live scanner/sessionization and retention policy on top of the parser/import/storage/session-schema/session-writer foundation, with no IP storage by default.
- Slice 4: search/filter across reliable IDs, known names, and session metadata after slice 3 exists.
- Slice 5: banlist manager with chosen source of truth, audited confirmation, backup/rollback, and redacted errors.
- Slice 6: Discord stats enrichment after stable player history exists; do not guess K/D, playtime, faction, role, or moderation state from the current roster alone.
