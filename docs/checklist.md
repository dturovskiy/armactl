# Public Status Checklist

This checklist tracks public, user-facing work for the free/local `armactl` core. It is intentionally concise; detailed implementation history and review notes are not kept in public docs.

## Public Merge Boundary

- [ ] Complete the public/private docs boundary review before merging `feat/web-interface` to public `main`.
- [ ] Decide the future private `armactl-dashboard` extraction/snapshot path separately from public `armactl` backports.
- [ ] Keep public `armactl` scoped to the free/local Arma core and local dashboard; move private product, hosted, infrastructure, and commercial planning out of public docs first.

## Current Core

- [x] Repo-local launcher with automatic bootstrap and read-only bootstrap drift diagnostics.
- [x] Fresh install flow for Arma Reforger Dedicated Server.
- [x] Existing-server detection and management.
- [x] Repair flow for missing or stale generated files.
- [x] TUI for install, repair, config, mods, schedule, logs, and status.
- [x] CLI commands for common server operations.
- [x] Optional Telegram bot service.
- [x] Real server FPS/frame-time telemetry from `-logStats` output.
- [x] Runtime data separated from source code.

## Local Web Dashboard

- [x] Local web setup through `./armactl web`.
- [x] Authenticated dashboard and live status polling.
- [x] Safe config editing for selected non-secret fields.
- [x] Safe config controls audit/design for the current safe field set, forbidden scope, and reuse contract.
- [x] Safe config controls runtime grouping, helper text, impact labels, and restart labels for the current safe field set.
- [x] Mods management with add/remove, enable/disable, bulk paste, import/export, dedupe, and unused-addon cleanup.
- [x] Game-admin management foundation.
- [x] Restart schedule and autostart controls.
- [x] File browser with bounded preview, download, and no-overwrite upload.
- [x] Logs and diagnostic report views.
- [x] Background jobs for install, repair, update checks, and updates.
- [x] Read-only public statistics output, Discord webhook publisher service, and `/bot` webhook settings for community channels, without server-control commands.
- [x] Player registry foundation with reliable IDs and no IP storage by default.
- [x] Player data inventory before history/banlist implementation.
- [x] Real server log/event inventory before player history/statistics implementation.
- [x] Player history parser foundation for sanitized auth/update/faction/combat log events, without live scanning, UI, DB ingest, raw log-line storage, or IP storage.
- [x] Player history DB ingest foundation for sanitized parsed log events in existing `players.db`, with dedupe, source/ref/confidence, and no raw log-line or IP storage.
- [x] Player history manual collector/import foundation for explicitly supplied bounded text log files, with dry-run/write CLI, safe basename+file-marker+line source refs, fail-closed oversize handling, and no live journalctl, background service, UI, raw log-line, or IP storage.
- [x] Read-only player history web view for already stored `player_log_events`, with bounded filters and no live log reads, mutations, raw source paths, raw log lines, or IP display.
- [x] Manual web collection job for player log events from allowlisted instance config logs, with background-job dedupe, bounded reads, audit counts, and no arbitrary file picker, automatic poller, raw paths, raw log lines, or IP storage; player-history/stat freshness depends on running this manual collection until a poller exists.
- [x] Explicit current-roster refresh foundation through `players:refresh-current` background jobs, with active-job dedupe, counts-only audit/job output, no registry/session writes from GET routes, and no raw paths, raw log lines, IPs, joined/role/session K/D, ban/kick, or Discord enrichment.
- [x] Automatic current-roster cache updater foundation through `players current-cache run`, with shared safe `web.db` snapshot cache, observed count/count source/roster availability fields, A2S count-only UI state, dashboard/public count-only reads from the safe snapshot, protection against replacing a recent RCON nonzero snapshot with an A2S-only zero during temporary RCON roster unavailability, 60s default interval, `/players` 60s no-store polling, `--once` mode, redacted retry warnings, no `players.db`/`player_sessions` writes, no raw paths/IPs/secrets, and no session truth claims.
- [x] Current players table UX cleanup on /players, with compact Player/Status, nullable Kills/Deaths/TK/Faction cells, Role as `—`, Available actions, sanitized ID/source/freshness/window details, `—` for unavailable proof and `0` only for proven zero, no K/D, and no GET player DB/session/stat writes.
- [x] Keep `/players/known` as a reliable identity directory, not a historical combat-stat board.
- [x] Phase 4a session tracking design for boundary events, reliable versus heuristic sources, roster-observation versus session truth, no-IP schema, retention/cleanup, source conflicts, and truthful UI/API scope.
- [x] Phase 4b session schema/vocabulary foundation in `players.db`, without live scanner, session writers, UI/API pages, retention cleanup, IP storage, or raw log/path storage.
- [x] Phase 4c session writer foundation in the registry service layer, with explicit reliable-ID observe/close helpers, sanitized evidence, one-open-session enforcement, and no live scanner/job, session UI/API, retention cleanup, IP storage, raw log/path storage, or current-session K/D/role/joined claims.
- [x] Phase 4d-a stored-log sessionization job foundation, with explicit `players:sessionize-log-events` background jobs over already stored `player_log_events`, active-job dedupe, counts-only audit/job output, checkpoint idempotence, and no live poller, session UI/API, disconnect pairing, retention cleanup, IP storage, raw log/path storage, or current-session K/D/role/joined claims.
- [x] Phase 4d-b stored-log close/lifecycle foundation, parsing sanitized disconnect/lifecycle evidence and closing sessions only from unambiguous stored correlation or server-boundary markers, with counts-only output and no live poller, stale-close, retention cleanup, session UI/API, IP storage, raw log/path storage, or current-session K/D/role/joined claims.
- [x] Phase 4d-c stale-close and retention foundation, with explicit service helpers and an explicit player-session maintenance background job, active-job dedupe, counts-only output/audit, `stale_timeout` closes for overdue open sessions, closed-session-only retention cleanup, preserved identity registry/log events, and no live poller, GET mutation, session UI/API, IP storage, raw log/path storage, or current-session K/D/role/joined claims.
- [x] Phase 4e-a explicit live session scanner foundation, with `scan_live_player_sessions_once(...)` and `players:scan-live-sessions` using reliable current-roster IDs as medium-confidence live presence observations through the registry session writer, active-job dedupe, counts-only output/audit, no A2S synthetic sessions, no closes on source failure, and no automatic daemon/poller, GET mutation, session UI/API, IP storage, raw log/path storage, or current-session K/D/role/joined claims.
- [x] Phase 4e-b live session conflict-window foundation, with a safe `players.db` live scan-window ledger and explicit one-shot scanner closes only after repeated successful reliable RCON roster absence through `close_player_session(...)` using low-confidence `stale_absence`; source failure, roster unavailable, A2S count-only, and unreliable/name-only or mixed-unreliable rows do not advance absence windows or close sessions, with counts-only output/audit and no automatic daemon/poller, GET mutation, session UI/API, IP storage, raw RCON rows, raw log/path storage, or current-session K/D/role/faction/joined/playtime claims.
- [x] Read-only player sessions web surface at `/players/sessions`, with authenticated bounded reliable-ID/name/status/end-reason/source/limit filters over existing stored `player_sessions`, truth-safe Observed/Last observed/Inferred close labels, sanitized fields only, and no GET mutations, scanner/current-cache side effects, IP/raw path/raw line/secret/public player ID/K/D/role/faction/playtime/Discord/ban/kick display.
- [x] Manual player-session operator controls on `/players/sessions`, with POST-only CSRF-protected `players:view` buttons for `players:scan-live-sessions`, `players:sessionize-log-events`, and `players:session-maintenance`, active-job dedupe left in service/job layers, `/jobs` notices, counts-only audit intent/outcome, and no automatic scheduler/poller, GET mutation, raw paths/lines/source refs/IP/secrets, K/D, role, playtime, faction truth, Discord enrichment, ban/kick, or banlist behavior.
- [x] Session freshness/operator UX polish on `/players/sessions`, with compact read-only stored-session counts and safe queued/running session-job links to `/jobs`, as UX only and not new session truth, scheduler, poller, raw output, or GET mutation.
- [x] Phase 4e/4f automatic session tracking planning/prep, with side-effect-free scheduler policy constants, safe automatic job cadence/backoff/close-scope rules, GET no-start tests, and docs that the automatic scheduler remains disabled.
- [x] Phase 4e/4f explicit opt-in session scheduler runner foundation, with `armactl players sessions scheduler run --once`, read-only `armactl players sessions scheduler status`, safe `web.db` scheduler state, allowed-job enqueue through existing dedupe, bounded failure backoff, missing-state empty/disabled status, and no service/timer/daemon/app-start/GET/JS trigger.
- [x] Player data truth Slice 1 timestamp contract/storage, with separate occurred/observed/collected times, explicit time source/confidence, and legacy/ambiguous rows kept truthfully labeled.
- [x] Player data truth Slice 2 player-history noise/details UX, with high-signal default player events, a separate session-evidence diagnostics mode, structured safe details, and no new session/stat truth claims.
- [x] Player data truth Slice 3 session semantics/job UX, with Session not closed/closed and First/Last/Close evidence labels, concise manual session-job guidance, and no online/playtime/K-D/role/faction truth claims.
- [x] Player data truth Slice 4 operational status telemetry fix, with dashboard/public status sharing precedence that treats fresh FPS telemetry as ready while keeping service failure/startup blockers authoritative.
- [x] Player data truth Slice 5 wrapper/bootstrap drift recovery, with non-mutating `scripts/bootstrap.sh --check --web`, clearer non-interactive wrapper guidance, and normal bootstrap remaining the stamp refresh path.
- [x] Player stats truth audit and implemented authenticated current-player `Kills`/`Deaths`/`TK`/last-known `Faction`/first-observed contract, with source matrix, nullable verdicts, and explicit exclusions for fake zeroes, Role truth, K/D, Discord/public enrichment, GET writes, public player IDs, and raw log/path/IP/secret exposure.
- [x] Document the play-session-scoped current stats contract in [player-session-stats-contract.md](player-session-stats-contract.md), including reconnect grace, automatic log ingest freshness, parser fixture requirements, no fake zeroes, and Discord/public gating.
- [x] Player stats truth Slice A current UI guard, with `/players` and `/players/current.json` returning placeholders plus a short unavailable reason for `Kills`/`Deaths`/`TK`/`Faction`/`Role` until play-session boundaries and automatic log freshness are proven; no new DB schema, ingest, counters, GET writes, scanner/sessionizer/maintenance starts, or Discord/public enrichment.
- [x] Player stats truth Slice B parser fixture audit, with supported stable player-log patterns, blocked/diagnostic-only patterns, required future stat fields, and focused occurrence-time collector fixtures documented before automatic ingest or play-session work.
- [x] Player stats truth Slice C automatic log ingest foundation, with the explicit player-log job reusing the existing collector/parser/storage path, players.db checkpoint/freshness metadata, unchanged-log skips, controlled missing/rotated/truncated/oversized counts, safe operator status, and no GET ingest, fake stats, daemon/timer, Discord/public enrichment, or current-roster stat writes.
- [x] Player stats truth Slice D play-session/reconnect model foundation, with durable play-session window metadata, server-run lifecycle boundary markers, 10-minute compatible reconnect merge, identity-conflict blocking, and last gameplay evidence metadata consumed by Slice E; no GET mutation, daemon/timer, Discord/public enrichment, K/D column, or role truth.
- [x] Continue production hardening for the local dashboard, including gateway throttle guidance, dashboard stale-refresh recovery, restart timing/source-of-truth clarification, and current VM smoke follow-up.
- [x] Clarify web-service restart diagnostics so `armactl web service restart` reports the `systemctl` result and `/healthz` readiness as separate bounded outcomes, without changing game restart behavior.
- [x] Keep long-lived async theme preference toggles usable after stale page CSRF by allowing only the cookie-only fetch preference update to recover safely, while normal mutating POST CSRF checks remain fail-closed.
- [x] Polish server update browser flow with controlled post-action notices, active job links, retry/failure guidance, and stale active-job notices.
- [x] Background job worker heartbeat/lease foundation, with opaque worker IDs, bounded heartbeat/lease timestamps on running jobs, heartbeat refresh from worker progress and wrapper heartbeat, terminal states clearing active leases, jobs page fresh/expired lease diagnostics, duplicate queued metadata repair only on mutating maintenance/enqueue paths, no GET job mutation, no process/thread kill, no running-job cancel action, and no automatic expired-lease metadata recovery.
- [x] Harden generated scheduled restart units with an explicit root-owned bounded restart helper, service stop timeout/kill policy, SIGKILL fallback scoped to the `armareforger*.service` control group, active/running verification, and short post-start stability checking.
- [x] Improve server update check/update UX after VM smoke feedback with stale-cache notices, clearer active queued/running check/update labels, failed check/update job links, server-running update blocks, and expired running-job diagnostics only.
- [x] Add explicit stale-running job metadata recovery through a POST-only Mark abandoned action gated by worker lease freshness, with no fake cancel, process/thread kill, destructive repair, output deletion, or GET mutation.
- [ ] Decide any future live job cancellation/worker termination model as a separate explicit worker lease/cancel slice; do not treat abandoned metadata recovery as process cancellation.
- [ ] Expand safe config controls after field behavior is verified, using `docs/safe-config-controls-plan.md` and the existing config editor pipeline.
- [x] Improve mod cleanup/mod mutation edge-case recovery workflows with controlled partial-change results, audit-safe diagnostics, and pending-work recovery markers.
- [x] Add narrow `/mods` stale profile settings cleanup for allowlisted disabled-mod module blocks only, with backup, counts-only audit, pending restart tracking, and no addon deletion, sidecar removal, active `game.mods` change, or generic file editing.
- [x] Build slice 2 read-only players page / improved players view from existing sources.
- [x] Add the initial web-only current-player enrichment/guard slice that established nullable fields and placeholder-safe behavior before the play-session contract, without DB migration/materialized counters/jobs, K/D, Discord/public enrichment, Role truth, GET writes, public IDs, or raw log/path/IP/secret exposure.
- [x] Player stats truth Slice E session-scoped current stats aggregation: read existing `players.db` query-only, gate on normalized reliable ID plus Slice D open play-session/server-run/lifecycle proof and Slice C fresh checkpoint coverage, count only stable exact/derived-time Kills/Deaths/TK inside the inclusive session window, keep teamkills out of Kills and deaths victim-only, preserve reconnect windows within grace, reset after grace/lifecycle, expose last-known Faction and safe freshness/window details, render `—` when unavailable and `0` only when proven, keep Role `—`, and add no K/D, GET mutation, daemon/timer, Discord/public enrichment, raw correlations, paths, lines, IPs, or secrets.
- [ ] Continue live session tracking with automatic poller/scheduler decisions, full session UI/detail/API beyond the read-only list and manual controls, remaining retention scheduling, richer truth labels, and broader conflict-policy hardening from the Phase 4a design, with no IP storage by default.
- [ ] Add slice 4 player search/filter over reliable IDs, names, and session metadata.
- [ ] Add slice 5 audited banlist manager after identity, storage, and rollback rules are documented.
- [ ] Add richer read-only Discord player columns only after reliable player history/session data is stable and each K/D, playtime, role, faction, or current-session claim has a verified source and truth label.
- [x] Improve schedule timezone UX with browser-local input/display and UTC backend normalization.
- [x] Add config-focused editor phase 2 with validation, backups, audit, recovery, and reset/error UX.
- [x] Add narrow safe replacement foundation for allowlisted /files/config config/profile files, including AdminServerSettings and CMPlayerStatsHUD profile JSON, with no broad editing, delete, rename, or arbitrary file-manager scope.
- [x] Document safe file editor Slice 1 read-only design and allowlist audit, including editable targets, must-not-edit boundaries, save/UI contract, and missing Slice 2 tests before runtime editor work.
- [x] Run a project-wide reuse/SOLID duplication audit before runtime file editor work and broad new feature expansion, mapping existing config/file mutation, filesystem containment, audit/recovery, jobs, and player/session service contracts so new slices reuse shared behavior instead of creating parallel sources of truth.
- [x] Add a narrow file editor reuse-helper slice before runtime editor UI/save work, exposing editor read/save DTOs through file_replacements, stale baseline checks, and focused tests without duplicating config/replacement/recovery contracts.
- [x] Add the runtime safe file editor UI/save slice using the reuse helper, without broad file-manager, delete, rename, move, copy, bulk, or arbitrary path scope.
- [x] Add shared mutation recovery foundation for config/raw-config and allowlisted file replacement, with restart-pending fallback markers, controlled post-mutation bookkeeping failures, and tests for audit/pending fallback without broad file editor/moderation expansion.
- [x] Close hardening-audit P1 cleanup by removing obsolete admin/mod pending fallback dead code, routing admin restart-pending recovery through the shared mutation recovery helper, and preserving controlled post-mutation failure behavior with regression tests.
- [x] Close hardening-audit P2 compatibility cleanup by explicitly testing retained legacy web facade, filesystem facade, pending-restart adapter, and `/players/refresh` alias, while leaving lower-noise dead-code tooling as future cleanup rather than adding a noisy dependency.
- [x] Document local/same-host and gateway-managed VM web deployment profiles, including the internal `8765` VM bind port, external gateway ports, and HTTPS-required cookie responsibility.
- [ ] Close remaining TUI/Web parity gaps that should be web-primary.
- [x] Run production SSH read-only ops smoke for current `feat/web-interface`, including normal wrapper/bootstrap checks, web service status, public health/status, recent web journals, gateway health, and nginx error logs, without game restarts.
- [x] Run post-hardening VM web smoke for the current `feat/web-interface` baseline after web-restart diagnostics, async theme preference recovery, and stale-job abandoned recovery; web status, `/healthz`, public status, and recent web journals were healthy, and no game restart was required.
- [x] Run authenticated browser UI smoke and architecture/security/dead-code review for the current `feat/web-interface` deployment baseline; current pass found no P0/P1 code blockers, but this does not close the public `main` merge gate until extraction/docs-boundary decisions are complete.

## Public Documentation

- [x] README covers install, usage, runtime layout, CLI commands, Telegram, and local web dashboard.
- [x] Architecture doc explains source/runtime/service boundaries.
- [x] Troubleshooting covers install, service, ports, telemetry, Telegram, and web dashboard basics.
- [x] Web deployment doc covers local/same-host, gateway-managed VM, reverse-proxy, and HTTPS-required cookie guidance.
- [ ] Sanitize public web deployment/hardening docs so they contain no private hostnames, IPs, routes, or operator-only topology.
- [ ] Split or move internal dashboard extraction/commercial planning before public `main` merge.
- [ ] Keep screenshots current after visible UI changes.
- [ ] Keep release notes concise and operator-focused.
