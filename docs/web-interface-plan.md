# Web Dashboard

The armactl web dashboard is a local browser interface for managing the same Arma Reforger Dedicated Server that CLI and TUI manage. It runs on the server host and reuses existing backend modules instead of reimplementing server control logic.

## Goals

- Provide a browser dashboard for common operator workflows.
- Keep CLI/TUI behavior and web behavior consistent by sharing backend modules.
- Keep the dashboard local-first and useful without any external service.
- Keep risky operations explicit, authenticated, and recoverable through CLI/TUI fallback paths.

## Current Capabilities

- Login/logout with sessions and CSRF protection.
- Dashboard status with service, runtime, config, metrics, and recent job state.
- Safe config editing for selected non-secret fields.
- Mods management with add/remove, enable/disable, bulk paste, import/export, dedupe, and unused-addon cleanup.
- Game-admin management foundation.
- Restart schedule and game-service autostart controls with browser-local input/display and UTC backend normalization.
- File browser with bounded preview, single-file download, and no-overwrite upload.
- Logs and diagnostic report views.
- Background jobs for install, repair, update checks, and updates.
- Player registry foundation with reliable IDs and no IP storage by default.
- Player log event DB ingest foundation for sanitized parser output in the existing `players.db`, with dedupe and no raw log-line/IP storage.
- Manual player log collector/import foundation through CLI for explicitly supplied bounded text log files, with dry-run/write modes and safe basename+file-marker+line source refs.
- Read-only player history web view for already stored player log events, with bounded filters and no web-request log reads or mutations.
- Manual player log collection web job from allowlisted instance config profile logs, with background-job dedupe, audit counts, and no arbitrary path input or automatic poller; stored history/stat freshness depends on operators running this collection until polling exists.
- Current-roster refresh foundation through an explicit `players:refresh-current` background job, with active-job dedupe, audit/job counts, no GET writes, and no session/K/D/role claims.
- Phase 4a session tracking design documented before implementation, covering session boundary events, source confidence, roster-versus-session truth, no-IP schema, retention/cleanup, conflict rules, and truthful UI/API scope.
- Phase 4b session schema/vocabulary foundation in `players.db`, with no live scanner/job/UI, no session writers from web routes, no IP/raw log/raw path columns, and no current-session claims.
- Phase 4c session writer foundation in the registry service layer, with explicit reliable-ID observe/close helpers that sanitize names, sources, refs, faction/side/correlation evidence, enforce one open session per reliable ID, and do not add live scanners, routes, UI pages, retention cleanup, IP storage, raw source storage, or current-session claims.
- Phase 4d-a stored-log sessionization job foundation, with explicit `players:sessionize-log-events` jobs over already stored `player_log_events`, active-job dedupe, counts-only audit/job output, per-session checkpoint idempotence, and no live scanner/poller, web route/UI/API page, disconnect pairing, retention cleanup, IP storage, raw source storage, or current-session K/D/role/joined claims.
- Action records and pending operator work for changes that need follow-up.

## Package Shape

```text
src/armactl/web/
├── routes/       # HTTP glue and request handling
├── services/     # workflow services and state changes
├── page_models/  # read models for templates
├── jobs/         # background jobs
├── runtime/      # runtime paths and database setup
├── auth/         # users, sessions, CSRF, permissions
├── templates/    # HTML templates
└── static/       # CSS, JS, images
```

Routes should stay thin. Services own validation, backend calls, backups, pending work, action records, and controlled errors. Page models prepare read-only state for templates.

## Route Areas

- `/dashboard` - server overview and status polling.
- `/config` - selected safe config fields.
- `/mods` - mod list, single/bulk add, import/export, enable/disable/remove, dedupe, and unused-addon cleanup.
- `/admins` - game admin list and current-player quick-add.
- `/schedule` - restart schedule and autostart controls.
- `/files` - bounded browse, preview, download, and upload.
- `/logs` - fixed log/report sources.
- `/jobs` - background job status.
- `/players` - authenticated live current-player roster, with explicit background refresh button.
- `/players/known` - authenticated reliable identity directory, not a combat-stat board.
- `/players/history` - authenticated read-only stored player event history.
- `POST /players/refresh-current` - authenticated current-roster registry refresh job; accepts CSRF only, not paths.
- `POST /players/history/collect-logs` - authenticated manual background collection from allowlisted instance config logs; accepts CSRF only, not paths.
- `/updates` - server version/update views, controlled post-action notices, fresh-check reuse feedback, and active update/check job links to `/jobs`.

## Auth And Safety

The dashboard uses:

- owner setup during web initialization;
- password-based login;
- signed session cookies;
- CSRF protection for mutating browser flows;
- named permissions checked in route/service code;
- login throttling;
- bounded output and redaction for logs, reports, and job tails.

Operators should keep CLI/TUI access available for recovery and maintenance.

## Config Editing

The normal web config page edits selected non-secret fields backed by shared config metadata so validation, labels, restart behavior, and pending work stay consistent.

The config page also includes a guarded advanced JSON editor for `config.json`. It validates JSON and server-facing config shape, creates a backup before saving, writes audit intent/outcome events, redacts existing secret values in the browser, rejects secret changes from the web editor, and updates restart-pending tracking. Broad arbitrary file editing remains separate future work.

The advanced editor keeps operator-entered JSON visible after validation errors and provides a reset action back to the last loaded disk config without writing files, pending work, or audit events.

## Schedule Timezones

The schedule page keeps the backend timer source of truth in UTC `OnCalendar` entries. The browser UI projects those UTC values into the operator's local timezone, posts the browser timezone with schedule changes, and shows local time alongside UTC so saved values do not look like silent time shifts. Schedule routes remain auth/permission/CSRF glue while the schedule service and page model own validation, timezone conversion, display DTOs, backend calls, and audit summaries.

## Files And Logs

The file browser is intentionally narrow:

- fixed roots;
- path containment checks;
- bounded previews;
- single-file download;
- no-overwrite upload by default.

The logs page reads fixed sources and should render bounded, redacted output.

## Background Jobs

Install, repair, update checks, and other slow operations should run as background jobs. The dashboard should show status, progress, bounded output tails, and final outcomes without blocking normal HTTP requests.

## Public Statistics

Community-facing statistics must stay read-only. Public Discord/website-style output can include server status, map/scenario summary, player counts and names, FPS freshness, and mod counts, but must not expose secrets, raw paths, admin actions, or server-control commands. The Discord webhook publisher creates one message and updates it on later runs instead of spamming the channel; the normal unattended loop is `armactl-discord-stats.service`, while `/bot` can save the webhook and interval without echoing the secret. Transient Discord/network publish failures should log only redacted warnings and retry on the configured interval; invalid config should remain a setup error. Timestamps should use Discord-native local rendering. Because a local publisher cannot update Discord after the host or VM loses network/power, public messages should frame their timestamp as a last heartbeat and warn that stale heartbeats may mean stale status. Per-user message-language localization is future full-bot scope, not webhook scope. Rich per-player columns such as K/D, teamkills, playtime, faction, or role must wait for reliable player history/session data or another verified source, not be guessed from the current roster.

## Player Data And Moderation

See [player-log-event-inventory.md](player-log-event-inventory.md) for the real log-event inventory before adding player history/statistics. Combat statistics are feasible from the observed script-emitted `INFO: KILL ...` lines, but remain source/capability-dependent rather than vanilla/no-mod.

See [player-data-inventory.md](player-data-inventory.md) for the current source/storage audit before expanding players, history, or banlist behavior. The current implementation separates live current-player observation from persisted registry data: A2S is count-only, RCON can provide names and reliable IDs, `/players` reads the current roster without persistence, `POST /players/refresh-current` queues a background refresh that persists reliable IDs into `players.db`, and public status stays count-only.

Phase 1 player-history foundation is code-only parser/event-model prep. `armactl.player_log_events` parses sanitized backend authentication, network player update, faction join, script combat, and optional ServerAdminTools kill-wrapper lines into a bounded DTO. It does not read live logs, store raw log lines, store player addresses, or expose UI.

Phase 2 player-history DB ingest foundation is also code-only. `player_registry.ingest_player_log_events` stores sanitized `PlayerLogEvent` fields in the existing instance `players.db` as `player_log_events`, records source/ref/confidence metadata, dedupes repeated events, and updates the existing reliable-ID registry only for newly stored events. It does not store raw log lines or player addresses, read live logs, run a background scanner, expose history views, calculate Discord K/D, or manage bans.

Phase 3a manual collector/import foundation is CLI-only. `armactl player-history collect` accepts explicit log file paths, defaults to dry-run, writes only with `--write`, resolves the instance-scoped `players.db` from `--instance` and `--data-root`, reads regular UTF-8 text logs line-by-line within max byte/line bounds, fails closed for files over the byte limit, and sends parsed events to the existing ingest path with sanitized basename+file-marker+line source refs. It does not read live `journalctl`, run a daemon/background poller, expose web routes/templates, store raw log lines, store IP/address values, or create sessions/history views.

Phase 3b read-only history view is web-only. `/players` defaults to the live current-player table, `/players/known` is a reliable identity directory for known names/IDs/first-seen/last-seen/source, and `/players/history` lists already stored `player_log_events` newest first with bounded limit, event-type, reliable-ID, and text/name filters for diagnostics and investigation. Historical combat events stay out of `/players/known`; they must not be shown as current-session K/D/playtime/role truth until session tracking exists. The view uses the existing `players:view` permission, displays sanitized source refs only, and does not read log files, run a scanner, mutate data from GET routes, store or display IPs, show raw log lines, create sessions, calculate K/D, manage bans, or enrich Discord output.

Phase 3c manual web collection adds the `/players/history/collect-logs` button/job. The route only handles auth, permission, CSRF, and redirect notice glue. The job scans only allowlisted current-instance server profile logs already used by armactl telemetry (`config/logs/*/console.log`), writes parsed events through the existing collector/ingest path, dedupes active queued/running jobs, and audits intent plus completion counts. It does not accept a path from the request, expose raw absolute paths, store raw log lines or IPs, run a live poller/service, sessionize playtime, calculate Discord K/D, or manage bans. Until a full session/history poller exists, stored player-history/stat freshness depends on operators running this manual collection.

Phase 3d current-roster refresh foundation adds `POST /players/refresh-current` and the `players:refresh-current` background job. The route only handles auth, `players:view`, CSRF, and redirect notice glue; the service/job read the existing current roster source, update `players.db` known-player first/last seen for reliable IDs, dedupe active queued/running jobs, and audit intent plus counts-only completion outcome. Job result/output includes observed/stored/ignored/source/status counts only. It does not accept request paths, write from GET routes, store raw paths, raw log lines, IPs, or player secrets, create sessions, infer joined time or role, calculate current-session K/D, manage bans/kicks, or enrich Discord output.

Phase 4a session tracking design is docs-only. It defines how future sessions can open from reliable log auth/update events or first reliable roster observations, close from disconnect/lifecycle/stale observations with confidence, and treat A2S as count-only evidence. It keeps `/players` current-roster GETs as read-only observations, keeps `players:refresh-current` as registry-only persistence, and reserves persisted online/offline truth for a future scanner/job that writes explicit session rows. The planned schema stores reliable IDs, sanitized name snapshots, observed/open/last/closed timestamps, source refs, confidence, end reason, and correlation fields such as RPL identity, connection ID, session player ID, and slot, but not IPs, raw lines, raw paths, public IDs, or secrets. It also requires retention/cleanup rules before long-lived session storage and blocks exact joined time, current role/faction/K/D, banlist, and Discord enrichment until implementation proves the source.

Phase 4b implements only the session schema/vocabulary foundation. The `players.db` migration creates `player_sessions` with reliable IDs, sanitized name snapshots, observed timestamps, source labels/refs, confidence/status/end-reason vocabulary, correlation fields, optional faction/side snapshots, scanner checkpoint metadata, timestamps, indexes, and one-open-session-per-reliable-ID enforcement. It does not add a live scanner, session open/close writer, retention cleanup, UI/API pages, IP storage, raw log lines, raw absolute paths, public IDs, or current-session joined/K/D/role/faction claims.

Phase 4c implements only the service-layer session writer foundation. `player_registry.observe_player_session` opens or updates one open reliable-ID session row, updates last-seen/name/source/faction/side/correlation evidence, and records the known-player identity observation; `player_registry.close_player_session` closes the current open row with sanitized close source/ref/confidence/end reason. These helpers reject unreliable IDs, store no IP/raw/source-path columns, sanitize path/IP/secret-looking values, and remain explicit service APIs only. They are not called from `/players` GET, current-roster refresh, a daemon, or a background scanner.

Phase 4d-a implements only explicit stored-log sessionization. `player_sessionizer.sessionize_stored_player_log_events` reads already persisted `player_log_events` from `players.db` and writes `player_sessions` through the Phase 4c helpers. Backend auth and network update events with reliable IDs open/update high-confidence sessions; faction joins and combat events with stable UUIDs are presence evidence only and use inferred/medium-confidence observations. The `players:sessionize-log-events` job dedupes queued/running work, writes counts-only job output and audit details, and uses per-session checkpoint metadata to avoid repeat writes on the same stored events. It does not read live logs, poll A2S/RCON, create web session pages/APIs, close sessions, pair heuristic disconnects, run retention cleanup, store IPs/raw paths/raw lines/secrets, or claim joined time, role, K/D, ban/kick, or Discord enrichment.

Full last-seen automation beyond explicit background refresh, explicit service helper calls, and the Phase 4d-a stored-log job, plus current-session semantics, should be added as a later poller/service slice. That slice should define retention, stale-close rules, and disconnect boundaries before promoting join time, roles, or combat counters as current-player facts.

Future player history implementation should follow the Phase 4a session design before adding automatic poller/service triggers, live sessionization, retention jobs, or richer session/history semantics, and before any public/Discord enrichment.

Next player slices should remain public/free/local core scope:

- slice 2: read-only players page / improved players view from existing sources and stored event history;
- slice 3: implement full session tracking, live scanner/sessionization, and retention policy using the Phase 4a design plus the parser/import/storage/session-schema/session-writer foundation, with no IP storage by default;
- slice 4: search/filter over reliable IDs, names, and session metadata;
- slice 5: audited banlist manager after source-of-truth, rollback, and identity rules are settled;
- slice 6: Discord stats enrichment after stable player history exists.

Do not add ban/kick mutations, aggregate kill/death stats, IP tracking, live journal readers, long-running automatic pollers, live sessionization, retention jobs, or Discord enrichment until later slices explicitly choose those sources and retention rules. Manual operator-triggered log collection is limited to the allowlisted background job above; current-roster refresh is limited to the explicit `players:refresh-current` job; stored-log sessionization is limited to `players:sessionize-log-events` and is not full live session tracking.

## Deployment

See [web-deployment.md](web-deployment.md) for setup, service commands, health checks, HTTPS, and reverse proxy guidance.

## Public Roadmap

Near-term dashboard work focuses on:

- production hardening;
- server update flow polish;
- safer config controls after behavior is verified;
- mod cleanup edge-case recovery improvements;
- player history/moderation improvements with reliable identity rules;
- read-only community statistics smoke and operational polish for Discord/Telegram publishing automation;
- schedule timezone edge-case smoke after browser/timezone changes;
- clearer logs and report download/export flows.

## Before Main Merge

Before treating the web dashboard as the primary free/local operator UI, run one final review pass that covers:

- VM smoke for login, dashboard, config, mods, admins, files, logs, jobs, updates, and service controls;
- TUI/Web parity decisions for install, repair, update, config, mods, cleanup, logs, bot, and host-test workflows;
- config-focused editor scope, including safe fields, advanced/raw JSON boundaries, backups, audit, pending restart, and recovery;
- schedule timezone UX with browser-local display/input and UTC backend normalization;
- player history, moderation, and banlist scope with reliable identity rules;
- lightweight file-editing scope, if any, separated from broad destructive file management;
- architecture, security, dead-code, source-of-truth, and public-docs drift checks.
