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
| Player registry page (`/players`) | `player_registry.list_known_players` | `~/armactl-data/<instance>/players.db` | As fresh as last manual refresh | High/moderation data | Read-only list of known reliable players, current name, first/last seen, count, and source. Basic query matches reliable ID, current name, and historical names. |
| Player registry refresh (`/players/refresh`) | Current roster from `player_sources.load_current_player_roster` | `players.db` tables `players`, `player_names`, `player_registry_schema_meta`; web audit log | On-demand POST only | High/moderation data | Records only reliable player IDs. Unreliable/slot-only rows are counted and ignored. Audit stores counts/source, not player names. |
| Player log event DB ingest foundation (`player_registry.ingest_player_log_events`) | Parsed `PlayerLogEvent` DTOs from `player_log_events`; no live reader or collector | `players.db` table `player_log_events`, plus existing `players`/`player_names` observations for reliable IDs | Only when a caller submits parsed events; no automatic freshness | High/moderation data | Stores sanitized structured auth/update/faction/combat fields with source/ref/confidence and a dedupe key. Does not store raw log lines or player addresses. No live scanner, sessionization, Discord K/D, or banlist behavior. |
| Manual player log collector/import CLI (`player_log_collector`, `player-history collect`) | Explicit operator-supplied regular text log file paths only | Existing `players.db` through `player_registry.ingest_player_log_events`; dry-run writes nothing | Manual CLI run only | High/moderation data | Reads accepted files line-by-line with max byte/line bounds; files over the byte limit fail closed. Source refs are sanitized basename+file-marker+line only. Summaries/errors use safe labels. No live `journalctl`, daemon, web UI, raw log-line storage, absolute path storage, or IP/address storage. |
| Player history events page (`/players/history`) | `player_registry.list_player_log_events` over already stored `player_log_events` | Existing instance `players.db`; read-only | As fresh as the last manual collector/import or other ingest caller | High/moderation data | Authenticated `players:view` page lists bounded stored events newest first, with event-type, reliable-ID, and text/name filters. It shows sanitized source refs only and never reads log files, mutates state, displays raw paths, raw log lines, IPs/addresses, secrets, ban/kick state, sessions, playtime, K/D, or Discord enrichment. |
| Manual player log web collection (`/players/history/collect-logs`) | Allowlisted current-instance server profile logs already used by armactl telemetry: `config/logs/*/console.log`; no request-supplied paths | Existing `players.db` through `player_log_collector.collect_player_log_events` and `player_registry.ingest_player_log_events`; web job metadata in `web.db`; web audit log | Manual operator POST only; background job dedupes queued/running collection | High/moderation data | The route accepts CSRF only and never reads arbitrary paths. The job bounds max files/bytes/lines, fails closed for oversize files through the collector, audits intent/outcome counts, and does not store/display raw absolute paths, raw log lines, IPs/addresses, sessions, playtime, ban/kick state, automatic poller state, or Discord K/D. |
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

`/players/refresh` remains the only browser/operator-triggered current-roster persistence path. It records current reliable IDs into `players.db`, updates current name and name history, and writes intent/outcome audit events. The manual `player-history collect` CLI can parse explicitly supplied bounded text log files and feed sanitized DTOs into the same `players.db` event ingest path, or run as a dry-run without writing. `/players/history` reads only those already stored event rows. `/players/history/collect-logs` is a manual web background job that scans only allowlisted current-instance `config/logs/*/console.log` files and never accepts a path from the request. No live reader, automatic poller, or web request reads arbitrary log files. None of these paths creates online/offline session state, ban records, kick records, retention cleanup, or Discord K/D projections.

### Real Server Log Inventory

See [player-log-event-inventory.md](player-log-event-inventory.md) for the read-only pass over real game logs before player history/statistics work. The observed logs can support a cautious sessions/history slice for connect/disconnect, last seen, faction snapshots, mission lifecycle, and aggregate count/FPS telemetry. Combat stats are feasible on the checked deployments through script-emitted `INFO: KILL ...` lines, with optional ServerAdminTools wrappers on some servers; they must be stored with an explicit source/capability flag and should not be treated as vanilla/no-mod functionality.

## Existing Storage

| Storage | Owner / path | What is stored | What is not stored | Retention / cleanup |
| --- | --- | --- | --- | --- |
| Player registry DB | `~/armactl-data/<instance>/players.db` | `players`: reliable ID, current name, first/last seen, seen count, last source. `player_names`: reliable ID, name, first/last seen, seen count. `player_log_events`: deduped parsed event kind, observed/log timestamp, source/ref/confidence, sanitized identity/name/session/faction/combat fields. Schema meta. | IPs/player addresses, raw log lines, secrets, RCON password, admin password, online/offline session state, ban/kick history, Discord K/D projections, raw RCON rows. | No player retention/cleanup currently. File is forced to mode `0600`. |
| Web runtime DB | `~/armactl-data/web/web.db` | Web users, sessions, CSRF tokens, jobs, server version checks, login rate limits, pending restarts/work. | Player registry/history/session tables. | Sessions/CSRF have expiry fields; login rate-limit rows are pruned by auth code; job rows have no player retention. File is mode `0600`. |
| Web audit log | `~/armactl-data/logs/web/audit.log` | JSON-lines records for mutating web actions. Player refresh stores phase/source/counts and controlled failure metadata. Player log web collection stores intent/outcome phases, job kind, allowlist scope, limits, and aggregate counts only. Admin actions store target ID and changed status. | Raw player names for player refresh, raw log lines, raw absolute log paths, IPs, passwords, RCON secrets, tracebacks. | No audit retention/rotation in current code. Web log view tails and redacts output. |
| Game config | `~/armactl-data/<instance>/config/config.json` | Official `game.admins` ID list, server config including A2S/RCON ports and configured secrets. | Player registry, sessions/history, banlist manager state. | Config backups are managed by config save flows; no player-specific retention. |
| Admin label sidecar | `~/armactl-data/<instance>/admins-state.json` | Local display labels and sources for game admins. | Player history, IPs, secrets, ban/kick state. | Written atomically with mode `0600`; no retention. |
| SAT UUID map / SAT config | `sat-admin-uuid-map.json`, `config/ServerAdminTools_Config.json` | Optional SAT UUID mappings; SAT config admins/gameMasters and existing SAT `bans` field. | No armactl ban manager records. | SAT guard can clear placeholder-only `bans`; no ban retention policy. |
| Discord stats env | `~/armactl-data/<instance>/bot/discord-stats.env` | Enabled flag, webhook URL, interval, Discord message ID, instance. | Player history and roster cache. | Written with mode `0600`; no player retention. |
| Server console logs | `~/armactl-data/<instance>/config/logs/*/console.log` | Server runtime logs and `-logStats` telemetry. | Structured armactl player sessions/history. | `cleaner.py` can delete log files under the instance config tree when operator cleanup is run; metrics only reads bounded tails. |

## Existing Audit/Security Behavior

- Player registry refresh is audited as `players.refresh` with intent before roster loading and outcome after persistence or controlled failure.
- Player refresh audit details include phase, source, reliable count, recorded count, ignored count, and controlled failure class/message. Tests assert display names and raw secrets do not enter player refresh audit details.
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

- Read-only stored event history is implemented for `player_log_events`; richer session/history semantics still need reliable ID, display name snapshot, source, observed-at timestamps, online/offline/session state, and count/source metadata.
- Use the real log inventory to distinguish reliable connect/session signals from heuristic disconnect pairing and mod-dependent combat events.
- Manual explicit log-file CLI import exists for bounded text files, and manual web collection exists only for allowlisted current-instance config console logs. Future work still needs to decide any live scanner/dashboard poll trigger; the current code has no automatic session recorder or poller.
- Define freshness and conflict rules for A2S count versus RCON roster. A2S cannot identify players; RCON can identify some players but can be unavailable.
- Add bounded retention or cleanup policy for session/history rows before storing long-lived moderation data.
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
- Slice 3: live scanner/sessionization and retention policy on top of the parser/import/storage foundation, with no IP storage by default.
- Slice 4: search/filter across reliable IDs, known names, and session metadata after slice 3 exists.
- Slice 5: banlist manager with chosen source of truth, audited confirmation, backup/rollback, and redacted errors.
- Slice 6: Discord stats enrichment after stable player history exists; do not guess K/D, playtime, faction, role, or moderation state from the current roster alone.
