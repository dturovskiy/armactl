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
- `/players` - player registry foundation.
- `/updates` - server version/update views.

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

Community-facing statistics must stay read-only. Public Discord/website-style output can include server status, player counts, FPS freshness, scenario, and mod counts, but must not expose secrets, raw paths, admin actions, or server-control commands. The Discord webhook publisher should create one message and update it on later runs instead of spamming the channel.

## Deployment

See [web-deployment.md](web-deployment.md) for setup, service commands, health checks, HTTPS, and reverse proxy guidance.

## Public Roadmap

Near-term dashboard work focuses on:

- production hardening;
- server update flow polish;
- safer config controls after behavior is verified;
- mod cleanup edge-case recovery improvements;
- player history/moderation improvements with reliable identity rules;
- read-only community statistics polish before any Discord/Telegram publishing automation;
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
