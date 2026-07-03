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
- Phase 4d-b stored-log close/lifecycle foundation, with sanitized disconnect/lifecycle parsing and safe stored-log closes through `close_player_session(...)` only when correlation is unambiguous or an accepted server-boundary marker is present, while keeping job/audit output counts-only and avoiding live scanner/poller, stale-close, retention cleanup, session UI/API, IP/raw source storage, or current-session claims.
- Phase 4e-a explicit live session scanner foundation, with `scan_live_player_sessions_once(...)` and `players:scan-live-sessions` converting reliable current-roster IDs into medium-confidence session observations through `observe_player_session(...)`, ignoring unreliable/count-only evidence, keeping stale-close/retention separate, and avoiding automatic daemon/poller, GET mutation, session UI/API, IP/raw source storage, or current-session claims.
- Phase 4e-b live session conflict-window foundation, with a safe `players.db` scan-window ledger and explicit one-shot scanner closes only after repeated successful reliable RCON roster absence through `close_player_session(...)` using low-confidence `stale_absence`; source failure, roster unavailable, A2S count-only, and unreliable/name-only or mixed-unreliable rows do not advance absence windows, while automatic daemon/poller, GET mutation, session UI/API, IP/raw source storage, and current-session claims remain out of scope.
- Read-only player sessions web surface at `/players/sessions`, with bounded reliable-ID/name/status/end-reason/source/limit filters over existing stored `player_sessions`, truth-safe observed/last observed/inferred close labels, no GET mutations, no scanner/current-cache side effects, and no IP/raw path/raw line/secret/public player ID/K/D/role/faction/playtime/Discord/ban/kick display.
- Manual player-session operator controls on `/players/sessions`, with POST-only CSRF-protected `players:view` buttons for `players:scan-live-sessions`, `players:sessionize-log-events`, and `players:session-maintenance`, `/jobs` notices, service/job-layer active dedupe, counts-only audit intent/outcome, and no automatic scheduler/poller or GET mutation.
- Session freshness/operator UX polish on `/players/sessions`, with compact read-only stored-session counts and safe queued/running session-job links to `/jobs`; this is not new tracking truth and does not add a scheduler, poller, raw output, or GET mutation.
- Phase 4e/4f automatic session tracking planning/prep, with declarative scheduler policy constants, conservative cadence/backoff/close-scope rules, GET no-start tests, and no enabled automatic scheduler, daemon, timer, service, or JS polling for sessions.
- Read-only explicit player-session scheduler status through `armactl players sessions scheduler status`, showing only allowed job kinds, last attempt/success/failure, next due, failure count, and due state from existing `web.db` state; missing `web.db` or missing scheduler state renders empty/disabled without creating DBs or jobs, and no scheduler service, timer, daemon, app-start hook, JS trigger, or GET trigger is installed/enabled.
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
- `/players/sessions` - authenticated read-only stored player sessions list with bounded filters, compact stored-session summary counts, safe active session-job links to `/jobs`, and truth-safe observed/last observed/inferred close labels.
- `POST /players/refresh-current` - authenticated current-roster registry refresh job; accepts CSRF only, not paths.
- `POST /players/history/collect-logs` - authenticated manual background collection from allowlisted instance config logs; accepts CSRF only, not paths.
- `/updates` - server version/update views, controlled post-action notices, fresh-check reuse feedback, active update/check job links to `/jobs`, retry/failure guidance, and stale active-job guidance.

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

See [player-data-inventory.md](player-data-inventory.md) for the current source/storage audit before expanding players, history, or banlist behavior. The current implementation separates current-player observation, safe current-roster caching, and persisted registry data: A2S is count-only and never creates roster rows, RCON roster rows can provide names and reliable IDs, observed count is tracked separately from row count, `/players` reads fresh in-process cache, then shared safe `web.db` cache, then bounded live fallback, `POST /players/refresh-current` queues a background refresh that persists reliable IDs into `players.db`, and public status stays count-only. The automatic current-roster cache updater warms only the shared safe snapshot cache; it is not session truth.

Phase 1 player-history foundation is code-only parser/event-model prep. `armactl.player_log_events` parses sanitized backend authentication, network player update, faction join, script combat, and optional ServerAdminTools kill-wrapper lines into a bounded DTO. It does not read live logs, store raw log lines, store player addresses, or expose UI.

Phase 2 player-history DB ingest foundation is also code-only. `player_registry.ingest_player_log_events` stores sanitized `PlayerLogEvent` fields in the existing instance `players.db` as `player_log_events`, records source/ref/confidence metadata, dedupes repeated events, and updates the existing reliable-ID registry only for newly stored events. It does not store raw log lines or player addresses, read live logs, run a background scanner, expose history views, calculate Discord K/D, or manage bans.

Phase 3a manual collector/import foundation is CLI-only. `armactl player-history collect` accepts explicit log file paths, defaults to dry-run, writes only with `--write`, resolves the instance-scoped `players.db` from `--instance` and `--data-root`, reads regular UTF-8 text logs line-by-line within max byte/line bounds, fails closed for files over the byte limit, and sends parsed events to the existing ingest path with sanitized basename+file-marker+line source refs. It does not read live `journalctl`, run a daemon/background poller, expose web routes/templates, store raw log lines, store IP/address values, or create sessions/history views.

Phase 3b read-only history view is web-only. `/players` defaults to the live current-player table, `/players/known` is a reliable identity directory for known names/IDs/first-seen/last-seen/source, and `/players/history` lists already stored `player_log_events` newest first with bounded limit, event-type, reliable-ID, and text/name filters for diagnostics and investigation. Historical combat events stay out of `/players/known`; they must not be shown as current-session K/D/playtime/role truth until session tracking exists. The view uses the existing `players:view` permission, displays sanitized source refs only, and does not read log files, run a scanner, mutate data from GET routes, store or display IPs, show raw log lines, create sessions, calculate K/D, manage bans, or enrich Discord output.

Phase 3c manual web collection adds the `/players/history/collect-logs` button/job. The route only handles auth, permission, CSRF, and redirect notice glue. The job scans only allowlisted current-instance server profile logs already used by armactl telemetry (`config/logs/*/console.log`), writes parsed events through the existing collector/ingest path, dedupes active queued/running jobs, and audits intent plus completion counts. It does not accept a path from the request, expose raw absolute paths, store raw log lines or IPs, run a live poller/service, sessionize playtime, calculate Discord K/D, or manage bans. Until a full session/history poller exists, stored player-history/stat freshness depends on operators running this manual collection.

Phase 3d current-roster refresh foundation adds `POST /players/refresh-current` and the `players:refresh-current` background job. The route only handles auth, `players:view`, CSRF, and redirect notice glue; the service/job read the existing current roster source, update `players.db` known-player first/last seen for reliable IDs, dedupe active queued/running jobs, and audit intent plus counts-only completion outcome. Job result/output includes observed/stored/ignored/source/status counts only. It does not accept request paths, write registry/session state from GET routes, store raw paths, raw log lines, IPs, or player secrets, create sessions, infer joined time or role, calculate current-session K/D, manage bans/kicks, or enrich Discord output.

Phase 3e automatic current-roster cache updater foundation adds the shared safe `web.db` current-roster cache and the foreground CLI/service loop `armactl players current-cache run` with `--once`, default 60s interval, and redacted retry warnings. `/players` and `/players/current.json` read fresh memory cache first, then fresh persistent safe cache, then bounded live fallback; successful live fallback updates only the safe current-roster cache, and live failures can serve a stale persistent snapshot with safe stale/error metadata. The current players page now polls `/players/current.json` every 60s with no-store fetch, preserves the current `player_search` filter, updates rows/source/status/freshness/count in place, and shows a count-only label when A2S reports players but the RCON roster is unavailable. Manual `Refresh current players` remains the immediate operator action for registry update and current-cache warming; the background updater is only a performance/freshness optimization, not session truth. The cache stores sanitized snapshot metadata and RCON player rows only: instance, collected/updated timestamps, observed count, count source, roster availability/configuration flags, source/status/error text, display names, reliable IDs, and row source. It does not write `players.db`, `players`, `player_names`, `player_sessions`, raw RCON output, raw paths, raw log lines, IPs, secrets, joined time, current role, exact faction truth, current-session K/D, ban/kick state, or Discord enrichment.

Phase 4a session tracking design is docs-only. It defines how future sessions can open from reliable log auth/update events or first reliable roster observations, close from disconnect/lifecycle/stale observations with confidence, and treat A2S as count-only evidence. It keeps `/players` current-roster GETs as read-only observations, keeps `players:refresh-current` as registry-only persistence, and reserves persisted online/offline truth for a future scanner/job that writes explicit session rows. The planned schema stores reliable IDs, sanitized name snapshots, observed/open/last/closed timestamps, source refs, confidence, end reason, and correlation fields such as RPL identity, connection ID, session player ID, and slot, but not IPs, raw lines, raw paths, public IDs, or secrets. It also requires retention/cleanup rules before long-lived session storage and blocks exact joined time, current role/faction/K/D, banlist, and Discord enrichment until implementation proves the source.

Phase 4b implements only the session schema/vocabulary foundation. The `players.db` migration creates `player_sessions` with reliable IDs, sanitized name snapshots, observed timestamps, source labels/refs, confidence/status/end-reason vocabulary, correlation fields, optional faction/side snapshots, scanner checkpoint metadata, timestamps, indexes, and one-open-session-per-reliable-ID enforcement. It does not add a live scanner, session open/close writer, retention cleanup, UI/API pages, IP storage, raw log lines, raw absolute paths, public IDs, or current-session joined/K/D/role/faction claims.

Phase 4c implements only the service-layer session writer foundation. `player_registry.observe_player_session` opens or updates one open reliable-ID session row, updates last-seen/name/source/faction/side/correlation evidence, and records the known-player identity observation; `player_registry.close_player_session` closes the current open row with sanitized close source/ref/confidence/end reason. These helpers reject unreliable IDs, store no IP/raw/source-path columns, sanitize path/IP/secret-looking values, and remain explicit service APIs only. They are not called from `/players` GET, current-roster refresh, a daemon, or a background scanner.

Phase 4d-a implements only explicit stored-log sessionization. `player_sessionizer.sessionize_stored_player_log_events` reads already persisted `player_log_events` from `players.db` and writes `player_sessions` through the Phase 4c helpers. Backend auth and network update events with reliable IDs open/update high-confidence sessions; faction joins and combat events with stable UUIDs are presence evidence only and use inferred/medium-confidence observations. The `players:sessionize-log-events` job dedupes queued/running work, writes counts-only job output and audit details, and uses per-session checkpoint metadata to avoid repeat writes on the same stored events. It does not read live logs, poll A2S/RCON, create web session pages/APIs, close sessions, pair heuristic disconnects, run retention cleanup, store IPs/raw paths/raw lines/secrets, or claim joined time, role, K/D, ban/kick, or Discord enrichment.

Phase 4d-b implements only the stored-log close/lifecycle foundation. The parser records sanitized RPL disconnect, network disconnect, BattlEye slot/name disconnect, and server lifecycle events; the sessionizer closes open sessions only through `player_registry.close_player_session(...)`. RPL identity, connection ID, and slot evidence close only when exactly one currently open session has the stored correlation field. BattlEye name-only or ambiguous slot evidence is skipped. Accepted lifecycle markers close currently open sessions with `end_reason=server_boundary`. Repeated runs over the same stored events do not duplicate, reopen, or rewrite closed rows. The job remains counts-only and still does not read live logs, poll A2S/RCON, add stale-close windows, run retention cleanup, expose session UI/API pages, store IPs/raw paths/raw lines/secrets, or claim joined time, role, K/D, ban/kick, or Discord enrichment.

Phase 4e-a implements only the explicit live scanner foundation. `player_live_session_scanner.scan_live_player_sessions_once(...)` and the `players:scan-live-sessions` job read the existing safe current-roster source once and write reliable roster IDs as medium-confidence live presence observations through `player_registry.observe_player_session(...)`. Unreliable/name-only rows and A2S count-only state are counted and ignored, source failure or roster unavailability does not close sessions, and stale-close/retention remains only in the separate explicit maintenance helper/job. Job output and audit details are counts-only. Later manual web controls expose it only as a POST-queued operator job. It does not add an automatic daemon/poller/scheduler, GET mutation, IP/raw path/raw line/secret storage, ban/kick, Discord enrichment, or current-session joined time/playtime/K-D/role/faction truth.

Phase 4e-b implements only the conservative live conflict-window foundation. The live scanner stores safe scan-window/checkpoint state in `player_session_live_scan_windows` and can close an open session only after repeated successful reliable RCON roster scans omit that reliable ID, using `player_registry.close_player_session(...)` with low-confidence `end_reason=stale_absence`. Source failure, roster unavailable, A2S count-only, and unreliable/name-only or mixed-unreliable rows do not advance absence windows or close sessions. Job stdout/audit details remain counts-only. Later manual web controls expose it only as a POST-queued operator job. It does not add an automatic daemon/poller/scheduler, GET mutation, IP/raw RCON row/raw path/raw line/secret storage, ban/kick, Discord enrichment, or current-session joined time/playtime/K-D/role/faction truth.

The player sessions surface adds an authenticated list over already stored `player_sessions` plus a manual operator panel for the existing explicit session jobs. It uses `players:view`, bounded reliable-ID/name/status/end-reason/source/limit filters, sanitized name snapshots and reliable IDs, compact read-only open/closed/inferred-or-stale/latest-observed summary counts, safe queued/running session-job indicators that link to `/jobs`, and truth-safe `Observed`, `Last observed`, and `Inferred close` labels. GET does not run scanners/jobs, start workers, update the current-roster cache, mutate or create `players.db`, or enqueue work. The POST controls only queue `players:scan-live-sessions`, `players:sessionize-log-events`, and `players:session-maintenance` with CSRF and redirect notices linking to `/jobs`; active dedupe remains in service/job layers. The page does not expose raw source refs/correlation fields, raw job output, or claim K/D, role, faction, playtime, Discord, ban, or kick truth.

Phase 4e/4f scheduler runner foundation is explicit opt-in only. `armactl players sessions scheduler run --once` checks due scheduler state in `web.db` and can enqueue only `players:scan-live-sessions`, `players:sessionize-log-events`, and `players:session-maintenance` through the existing service/job dedupe layer. `armactl players sessions scheduler status` is read-only: it opens only existing `web.db` scheduler state, reports empty/disabled state when `web.db` or the state table is missing, and never enqueues jobs or creates `players.db`/sessions. Minimum intervals are 120s for live scans, 5 minutes for stored-log sessionization, and 1 hour for maintenance, with bounded failure backoff and active queued/running dedupe. The scheduler state and status surface expose only safe instance/job-kind timestamps (`last_attempt_at`, `last_success_at`, `last_failure_at`, `next_due_at`), `failure_count`, and due/not-due state; they store or display no raw output, paths, RCON rows, IPs, secrets, source refs, or public player IDs. Live scans must stay offset from the 60s current-roster cache updater, must not call the cache updater or write the snapshot cache, and must write only `players.db` session observations through the existing helpers. Automatic closes are limited to unambiguous stored disconnect correlation, accepted server-boundary markers, repeated successful reliable RCON absence, or explicit stale-timeout maintenance. A2S/FPS counts and job/audit output remain count-only; names, IDs, sources, source refs, and faction/side evidence remain sanitized. IPs, raw lines, raw paths, raw RCON rows, secrets, public player IDs, K/D, role, playtime, current faction truth, Discord enrichment, ban/kick, and banlist behavior remain forbidden. No scheduler service, timer, daemon, app-start hook, JS trigger, GET route trigger, systemd unit, session detail/API page, or richer current-session truth is installed or enabled yet.

Full last-seen automation beyond explicit background refresh, the snapshot-only current-roster cache updater, explicit service helper calls, the Phase 4d-a/4d-b stored-log job, the Phase 4e-a/4e-b one-shot live scan job, and the read-only stored-session list, plus full session detail/API and current-session semantics, should be added as a later session poller/service slice. That slice should define automatic scheduling, UI/API truth labels, and broader conflict policies before promoting join time, roles, or combat counters as current-player facts.

Future player history/session implementation should follow the Phase 4a session design before adding automatic session/log poller triggers, live sessionization, retention jobs, richer session/history semantics, or public/Discord enrichment.

Next player slices should remain public/free/local core scope:

- slice 2: read-only players page / improved players view from existing sources and stored event/session history;
- slice 3: implement full automatic session tracking, session detail/API, live scanner/sessionization scheduling, and retention policy using the Phase 4a design plus the parser/import/storage/session-schema/session-writer foundation, with no IP storage by default;
- slice 4: richer search/filter over reliable IDs, names, and session metadata;
- slice 5: audited banlist manager after source-of-truth, rollback, and identity rules are settled;
- slice 6: Discord stats enrichment after stable player history exists.

Do not add ban/kick mutations, aggregate kill/death stats, IP tracking, live journal readers, automatic session/log pollers, live sessionization beyond the explicit one-shot scanner's reliable-ID observe and repeated reliable absence close behavior, retention scheduling beyond the explicit maintenance job, full session detail/API, or Discord enrichment until later slices explicitly choose those sources and truth labels. Manual operator-triggered log collection is limited to the allowlisted background job above; current-roster registry refresh is limited to the explicit `players:refresh-current` job; automatic current-roster cache refresh is limited to the safe `players current-cache run` snapshot cache; stored-log sessionization and stored-log close/lifecycle handling are limited to `players:sessionize-log-events`, live roster observation/absence close behavior is limited to `players:scan-live-sessions`, and the sessions web page offers only a read-only stored-row list plus POST-only manual controls for the existing explicit jobs; these are not full automatic live session tracking.

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

## Post Phase 4 Hardening Audit Plan

This audit snapshot follows the Phase 4 player/session foundation. Keep the next implementation slices narrow: prefer VM smoke, operator feedback, and small hardening fixes over new feature surface.

Do not commit private hostnames, IP addresses, provider details, or router rules to this public repo. Production-host smoke targets belong in private operator notes.

### Priorities

P0 before merge:

- Run final VM smoke across login, dashboard, config, mods, admins, schedule, files, logs/report, jobs, updates, service actions, players/session pages, and public status.
- Verify production hardening assumptions: localhost-first binding, HTTPS-required cookies behind TLS, exposure warnings, redacted logs/reports/job output, and `web.db` job integrity diagnostics.
- Re-smoke update check/update behavior on every production host from private ops notes before treating `/updates` as primary.
- Keep future user-affecting mutation flows behind the transaction/recovery pattern below; do not add moderation, banlist, broad config, or file editing before that pattern exists.

P1 next slices:

- Server update UX after VM feedback: clearer retry/failure states, stale active-job guidance, and a safe queued-job cancellation decision if needed.
- Production readiness polish for health/readiness checks and startup/runtime warnings without widening dashboard exposure.
- Safe config control expansion only for fields with proven validation, backup/restart behavior, and recovery.
- Mod cleanup recovery: preflight manifest, stronger partial-failure messaging, and a restore/quarantine design before more deletion behavior.
- Lightweight file editing design with allowlists, size limits, diff preview, backups, validation, audit, and recovery.

P2 later:

- Full automatic player-session scheduler/service enablement, richer session UI, and retention scheduling.
- Audited banlist/moderation manager after identity, rollback, source-of-truth, and recovery rules are settled.
- Broader web/TUI parity where operators prove it matters.
- Rich Discord/player statistics after reliable player history/session data is stable enough.

Out of scope:

- Direct public dashboard exposure without VPN, firewall, identity-aware proxy, or HTTPS reverse proxy.
- General-purpose web file manager, recursive delete/move, overwrite upload, or arbitrary path editing.
- Web editing for secrets, RCON exposure policy, game/public/RCON bind/port changes, or network/firewall changes without dedicated recovery.
- Ban/kick/banlist mutations, Discord K/D enrichment, and current-session K/D, role, faction, or playtime truth claims.
- Pulling web-primary player/session views back into TUI before an operator need is demonstrated.

### Findings

#### 1. Production Hardening For Local Dashboard

- Current state: auth has owner setup, session-token digests, CSRF, login throttling, runtime-scoped cookies, `HttpOnly`, `SameSite=Lax`, and optional `Secure` cookies through `ARMACTL_WEB_HTTPS_REQUIRED`. Exposure warnings exist for non-local binds. `/healthz` is minimal liveness only. Logs, reports, previews, audit details, and job tails are bounded and redacted. `web.db` has migrations, active-job dedupe, duplicate-active repair diagnostics, and pending-work fallback sidecar storage.
- Risk: HTTPS/proxy/firewall guarantees are still operator deployment assumptions. `/healthz` does not prove DB, auth, template, job-store, or filesystem readiness. Background jobs run in daemon threads inside the web process, so interrupted `running` jobs may need operator-visible recovery.
- Proposed slice: keep `/healthz` as liveness, then add a small authenticated runtime/readiness diagnostic or CLI status if VM smoke shows a need. Confirm secure cookies and exposure warnings on local, LAN, and reverse-proxy paths.
- Files/modules likely touched: `src/armactl/web/routes/health.py`, `src/armactl/web/page_models/dashboard.py`, `src/armactl/web/security/exposure.py`, `src/armactl/web/runtime/db.py`, `src/armactl/web/jobs/store.py`, `src/armactl/web/services/job_integrity.py`, `docs/web-deployment.md`.
- Validation/smoke needed: `curl -fsS http://127.0.0.1:8765/healthz`; login/logout; bad password; CSRF failure; secure-cookie flag with `--https-required`; `/dashboard`, `/logs`, `/report`, `/jobs`; real `journalctl` redaction review; focused auth/exposure/logs/jobs/runtime/pending-work tests.
- Stop condition: operators can tell whether the web process is alive, runtime state is degraded, dashboard exposure is intentionally protected, and stale jobs have a recovery path.

#### 2. Server Update UX After VM Smoke

- Current state: `/updates` shows installed/latest build state, cached check reuse, failure reason, safe retry labels, failed update/check job guidance, stale active-job guidance, active update/check job links, and a server-running block before update. Update checks and updates run as deduped background jobs with redacted output. Job cancellation exists in store/maintenance code, but not as a normal operator-facing update workflow.
- Risk: after a failed or interrupted update, operators may not know whether to retry, wait, inspect `/jobs`, stop the game server, or fall back to CLI/TUI. SteamCMD/network behavior is host-specific.
- Remaining slice: continue VM smoke and operator feedback. Any cancellation should stay limited to a proven safe queued/stale metadata model unless a real worker-cancel model exists.
- Files/modules likely touched: `src/armactl/web/routes/updates.py`, `src/armactl/web/views/updates.py`, `src/armactl/web/templates/updates.html`, `src/armactl/web/routes/jobs.py`, `src/armactl/web/templates/jobs.html`, `src/armactl/web/services/server_job_actions.py`, `src/armactl/web/jobs/server.py`.
- Validation/smoke needed: fresh update check, cached check reuse, failed check, update available while server running, update queued while stopped, active job links, failed job details, retry behavior. On production hosts from private notes, smoke the default instance unless private notes name another instance.
- Stop condition: an operator can see the active job, understand why update is blocked or failed, retry safely, and know when to use CLI/TUI fallback. Do not add live SteamCMD cancellation unless it can be proven safe.

#### 3. Safe Config Controls After Field Behavior

- Current state: basic web config editing is limited to non-secret fields: `name`, `scenario_id`, `max_players`, `visible`, `disable_third_person`, `battleye`, `server_max_view_distance`, and `server_min_grass_distance`. The guarded raw JSON editor validates JSON/config shape, blocks secret changes, creates backups, preserves safe error input, and updates pending restart tracking.
- Risk: advanced network fields can break reachability or expose services. Secrets must not be echoed, diffed, or changed through generic web editors. Raw JSON remains powerful even with validation and backups.
- Proposed slice: expand only one field group at a time after real server behavior is verified. Each new field needs schema metadata, UI copy, validation, backup, pending restart behavior, and rollback/recovery instructions. Keep bind addresses, public address/ports, RCON address/permission/password, generated secrets, and game passwords out of normal web editing.
- Files/modules likely touched: `src/armactl/server_config_schema.py`, `src/armactl/web/services/config_edit.py`, `src/armactl/web/page_models/config.py`, `src/armactl/web/templates/config.html`, `tests/test_server_config_schema.py`, `tests/test_web_config_edit.py`.
- Validation/smoke needed: unit tests for validation, secret rejection, backups, pending restart, no-op saves, raw reset/error UX; VM save/restart/verify/revert smoke for each new field.
- Stop condition: the new field can be changed, validated, backed up, reverted, and restarted without hiding secrets or requiring SSH-only repair for expected mistakes.

#### 4. Mod Cleanup Edge-Case Recovery

- Current state: unused-addon cleanup is constrained to canonical `<instance>/config/addons`, rejects symlinks and unsafe paths, supports dry-run, requires confirmation, and reports bounded redacted summaries. Disabled mods are preserved. Config/disabled-sidecar updates have rollback helpers.
- Risk: cleanup deletion is not rollbackable after a directory is removed. Partial failure can delete some addon directories and fail others. Local Workshop files may contain operator-added content under the allowed addon directory.
- Proposed slice: add a cleanup recovery design before changing deletion behavior: dry-run manifest, confirmation bound to that manifest, optional quarantine/move first, restore command or documented restore path, and clearer partial-failure UI.
- Files/modules likely touched: `src/armactl/addon_cleanup.py`, `src/armactl/mods_manager.py`, `src/armactl/web/services/mod_actions.py`, `src/armactl/web/routes/mods.py`, `src/armactl/web/templates/mods.html`, `tests/test_addon_cleanup.py`, `tests/test_web_mod_actions.py`.
- Validation/smoke needed: dry-run no delete, confirmed cleanup, symlink refusal, invalid path refusal, disabled mod preservation, partial `rmtree` failure, and restore/quarantine behavior if implemented.
- Stop condition: a partial cleanup failure leaves an operator-visible manifest and a clear recovery path. Do not expand cleanup beyond instance `config/addons`.

#### 5. Safe File Editing Scope

- Current state: the file browser has fixed roots, containment checks, source/system path denial, bounded redacted previews, single-file download, and no-overwrite upload only under the `server` root. Config editing is handled separately by config-specific services.
- Risk: general web editing could become an accidental arbitrary file manager. Editing server files without validation can break installs, overwrite Workshop content, or leak secrets in previews/diffs.
- Proposed slice: keep broad file editing out of scope. If lightweight editing is added, allow only small text files under a narrow allowlist, with max bytes, UTF-8/text detection, diff preview, backup, atomic write, validation hook, intent/outcome audit, and recovery handle. Leave logs/backups read-only and keep uploads no-overwrite.
- Files/modules likely touched: `src/armactl/web/services/filesystem_roots.py`, `src/armactl/web/services/filesystem_paths.py`, `src/armactl/web/services/filesystem_preview.py`, `src/armactl/web/services/filesystem_transfer.py`, `src/armactl/web/routes/files.py`, `src/armactl/web/templates/files.html`, `tests/test_web_files.py`.
- Validation/smoke needed: path traversal, symlink, source tree/system path denial, binary/oversize refusal, backup creation, diff preview, validation failure, no-overwrite upload, and failed-write recovery.
- Stop condition: operators can edit only explicitly allowed small text targets and can inspect, validate, back up, and recover every change.

#### 6. TUI/Web Parity Gaps

- Current state: TUI covers install, repair, structured/raw config, mods, schedule, logs, cleanup, bot settings/service, host tests, and some port workflows. Web covers authenticated dashboard, config, mods, admins, schedule, files, logs/report, jobs, updates, public status, bot, and player/session surfaces.
- Risk: chasing full parity can bloat the merge and duplicate workflows that should remain CLI/TUI fallback. Some web-primary features, especially player/session views, should not be pulled into TUI without operator demand.
- Proposed slice: decide only operator-critical gaps before merge: install/repair/update status clarity, logs/report access, bot/Discord operational controls, ports/exposure visibility, and host-test guidance. Keep players/session UI web-primary for now.
- Files/modules likely touched: `docs/web-interface-plan.md`, `docs/checklist.md`, `src/armactl/tui/screens.py`, `src/armactl/web/routes/*.py`, `src/armactl/web/templates/*.html`.
- Validation/smoke needed: operator walkthrough comparing CLI/TUI/web for install, repair, update, config, mods, logs, bot, and host-test workflows.
- Stop condition: each gap is marked web-needed, TUI-needed, CLI-only fallback, or deferred. No parity work is done only because another adapter has a feature.

#### 7. Final VM Smoke And Merge Review

- Current state: deployment, architecture, checklist, and hardening runbook docs exist. Public docs intentionally do not store production hostnames, IP addresses, or provider/router details.
- Risk: a code-complete dashboard can still fail on real service state, proxy state, SteamCMD behavior, cookie settings, or logs/report redaction. Merge gates can drift unless exact commands and pages are named.
- Proposed slice: run final smoke on each production host/instance from private operator notes. Use private notes for hostnames and IPs; keep this repo generic.
- Files/modules likely touched: mostly docs and any small fixes found during smoke. If failures appear, touch only the owning route/service/template/test module.
- Validation/smoke needed:

```bash
git status -sb
git diff --check
.venv/bin/ruff check .
.venv/bin/pytest
./armactl status
./armactl web service status
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/public/server-status.json
systemctl status armactl-web.service armareforger.service armareforger-restart.timer --no-pager
sudo journalctl -u armactl-web.service -n 200 --no-pager
```

Also check optional services when configured: `armactl-bot.service` and `armactl-discord-stats.service`.

Pages to smoke: `/login`, `/dashboard`, `/config`, `/mods`, `/admins`, `/schedule`, `/files`, `/logs`, `/report`, `/jobs`, `/updates`, `/bot`, `/players`, `/players/known`, `/players/history`, `/players/sessions`, and `/public/server-status.json`.

- Stop condition: no P0 smoke failures remain; any P1/P2 findings are documented with owner, risk, and stop condition.

#### 8. Rollback And Transaction Boundaries For Future Mutation Flows

- Current state: config saves use intent audit, backup, apply, outcome audit, and pending-work marking with fallback sidecar. Server job enqueue audits intent before queueing and cancels a newly created job if outcome audit fails. File upload stages bytes and audits intent before publish, but if outcome audit fails after publish, the file remains. Admin/mod/service/schedule/player-session actions audit intent before mutation and outcome after mutation; some completed mutations can still surface as failures if outcome audit fails. Player registry writes use SQLite transactions and idempotent helpers, but job outcome audit happens after DB mutation.
- Risk: a backend mutation can happen before an exception returns to the route or job runner, leaving state changed without a guaranteed operator recovery handle. This is tolerable for current observation/session jobs and some external service actions, but it is not acceptable for future moderation, banlist, broad config, or file-edit flows.
- Proposed slice: create a shared mutation checklist/helper before adding new user-affecting mutation features. Required pattern: validate request/permissions/CSRF/allowlist/size; write intent audit; create backup/snapshot/stage/manifest; apply through one narrow service-layer API; verify from disk/DB/service; mark pending work when restart/review/retry/recovery is required; write outcome audit with recovery identifiers; rollback when safe or leave a visible recovery marker with a controlled message.
- Files/modules likely touched: future shared helper under `src/armactl/web/services/`, `src/armactl/web/services/config_edit.py`, `src/armactl/web/services/file_uploads.py`, `src/armactl/web/services/mod_actions.py`, `src/armactl/web/services/admin_actions.py`, and future moderation/banlist modules/tests.
- Validation/smoke needed: tests for intent-audit failure before mutation, backup/stage creation, apply failure rollback or recovery marker, verify failure, pending-work fallback, outcome-audit failure after mutation, redacted details, and operator-visible recovery messages.
- Stop condition: no future user-affecting mutation can return an ambiguous failure after a partial backend change; it either rolls back or leaves a documented recovery handle visible to the operator.

Flows that must use the pattern before implementation:

- Banlist and moderation actions, including ban, unban, kick, reason editing, and source-of-truth sync.
- Any config expansion beyond the current safe field set and guarded raw editor.
- Any file edit, overwrite, rename, delete, or bulk upload flow.
- Any future player identity merge/split or moderation state attached to player records.
- Any mod cleanup behavior that moves beyond current confirmed cleanup or starts deleting outside the instance `config/addons` scope.

### Recommended Next Implementation Slice

Start with the P0 final VM smoke and update-flow UX review. It has the highest merge value, exercises the real production assumptions, and should decide whether `/updates` needs only copy/state polish or a deeper stale-job recovery slice before merge.
