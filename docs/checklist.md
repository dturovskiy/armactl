# Public Status Checklist

This checklist tracks public, user-facing work for the free/local `armactl` core. It is intentionally concise; detailed implementation history and review notes are not kept in public docs.

## Current Core

- [x] Repo-local launcher with automatic bootstrap.
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
- [x] Explicit current-roster refresh foundation through `players:refresh-current` background jobs, with active-job dedupe, counts-only audit/job output, no GET writes, and no raw paths, raw log lines, IPs, joined/role/session K/D, ban/kick, or Discord enrichment.
- [x] Keep `/players/known` as a reliable identity directory, not a historical combat-stat board.
- [x] Phase 4a session tracking design for boundary events, reliable versus heuristic sources, roster-observation versus session truth, no-IP schema, retention/cleanup, source conflicts, and truthful UI/API scope.
- [x] Phase 4b session schema/vocabulary foundation in `players.db`, without live scanner, session writers, UI/API pages, retention cleanup, IP storage, or raw log/path storage.
- [x] Phase 4c session writer foundation in the registry service layer, with explicit reliable-ID observe/close helpers, sanitized evidence, one-open-session enforcement, and no live scanner/job, session UI/API, retention cleanup, IP storage, raw log/path storage, or current-session K/D/role/joined claims.
- [x] Phase 4d-a stored-log sessionization job foundation, with explicit `players:sessionize-log-events` background jobs over already stored `player_log_events`, active-job dedupe, counts-only audit/job output, checkpoint idempotence, and no live poller, session UI/API, disconnect pairing, retention cleanup, IP storage, raw log/path storage, or current-session K/D/role/joined claims.
- [ ] Continue production hardening for the local dashboard.
- [x] Polish server update browser flow with controlled post-action notices and active job links.
- [ ] Continue polishing server update check/update UX after VM smoke feedback.
- [ ] Expand safe config controls after field behavior is verified.
- [ ] Improve mod cleanup edge-case recovery workflows.
- [x] Build slice 2 read-only players page / improved players view from existing sources.
- [ ] Implement full session tracking, live scanner/sessionization, and retention policy work from the Phase 4a design, with no IP storage by default.
- [ ] Add slice 4 player search/filter over reliable IDs, names, and session metadata.
- [ ] Add slice 5 audited banlist manager after identity, storage, and rollback rules are documented.
- [ ] Add richer read-only Discord player columns only after reliable player history/session data exists.
- [x] Improve schedule timezone UX with browser-local input/display and UTC backend normalization.
- [x] Add config-focused editor phase 2 with validation, backups, audit, recovery, and reset/error UX.
- [ ] Decide and document safe scope for lightweight file editing beyond config.
- [ ] Close remaining TUI/Web parity gaps that should be web-primary.
- [ ] Run final VM smoke, architecture/security/dead-code/docs review before main merge.

## Public Documentation

- [x] README covers install, usage, runtime layout, CLI commands, Telegram, and local web dashboard.
- [x] Architecture doc explains source/runtime/service boundaries.
- [x] Troubleshooting covers install, service, ports, telemetry, Telegram, and web dashboard basics.
- [x] Web deployment doc covers local/LAN setup and reverse-proxy guidance.
- [ ] Keep screenshots current after visible UI changes.
- [ ] Keep release notes concise and operator-focused.
