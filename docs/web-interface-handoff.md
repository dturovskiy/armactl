# Web Interface Handoff

This document is the working handoff for implementing the armactl web panel with
one implementation chat/agent and one review chat/agent. The implementation
agent leaves changes uncommitted. The review agent validates the diff, requests
or applies fixes when needed, and creates the commit only after review passes.

## Source of truth

Before implementing any web-panel task, read:

- `docs/web-interface-plan.md`
- `docs/checklist.md`, Phase 16
- `docs/architecture.md`
- `docs/development.md`
- `CONTRIBUTING.md`

Release files are not part of web-panel implementation unless the task is
explicitly a release task.

## Working branch and workspace

- Work in the WSL checkout: `/home/deus/projects/armactl`.
- Use branch `feat/web-interface` unless the maintainer asks for a smaller
  sub-branch.
- Do not edit or commit the Windows `E:\Projects\armactl` checkout.
- Keep changes small and reviewable.
- Do not commit from the implementation chat. Leave changes in the working tree
  for the review chat.
- The review chat creates one focused commit per approved implementation step.

## Implementation rules

- Keep backend logic in reusable Python modules, not in TUI screens or web route
  handlers.
- Do not import Textual/TUI code from web code.
- Do not shell out to `armactl --json-output` for normal web behavior; call the
  existing Python backend modules directly.
- Add a web-facing facade/DTO layer before routes start combining many backend
  calls.
- Keep `website/` separate. It is the public marketing site, not the
  authenticated management panel.
- Do not expose arbitrary host filesystem access.
- Do not run install/repair/update as blocking HTTP requests.
- Treat web as an always-on service: `armactl web run` is for foreground
  development/debugging, while production uses `armactl-web.service`.
- Do not make web replace the default `./armactl` TUI startup path.
- Preserve the target remote scenario: a user can open a public HTTPS URL from
  another network, log in, and use the permitted panel features without SSH.
- Use the shared `ports.py` blocked web port helpers when implementing
  `armactl web init`, `armactl web run`, or service install port validation.
- When documenting or testing a central reverse proxy outside the game VM,
  remember that proxy upstreams must target the game VM private address, not
  `127.0.0.1`.
- Roll web out first on a paused/non-critical VM. Do not start with stable
  production or rented customer servers.
- Treat any existing noVNC/SFTP/browser file-manager VM as a separate external
  project, not as an armactl-web dependency or component.
- Do not let SSHFS/NFS/SMB or other remote mounts silently expand the web file
  manager's allowed roots.
- Scope the MVP cabinet to one local machine/server per `armactl-web`
  installation. Do not add multi-server enrollment or cross-VM management.
- Keep CLI and TUI usable as fallback management paths.
- Keep tests independent from saved operator UI language.
- Reuse existing `src/armactl/locales/*.json` files for web localization.
  Resolve language per web request/session/user and do not call the TUI-style
  global `toggle_lang()` / `save_lang()` helpers during HTTP request handling.

## Current implementation status

Completed foundation:

1. `src/armactl/web/` package skeleton and import-safety tests.
2. Optional web dependencies, package-data/bootstrap support, and `scripts/run-web`.
3. Web-facing facade/DTO layer for read-only dashboard data.
4. FastAPI app factory, `/healthz`, package-local templates/static assets, and
   minimal read-only dashboard routes.
5. Web runtime storage/config/db foundation under `~/armactl-data/web/`.
6. `armactl web init` first setup command for local runtime config and DB.
7. Auth DB/password foundation: `web_users`, Argon2 password helpers, and
   minimal active `owner` user helpers.
8. Owner-user setup flow via explicit `armactl web init --owner USERNAME`
   with hidden password confirmation and safe operator summary output.
9. Session and CSRF primitives: digest-only SQLite storage, expiry checks,
   session revoke/delete helpers, and CSRF tokens bound to active sessions.
10. Login/logout routes, login template, authenticated dashboard guard, and
    HttpOnly SameSite=Lax cookie wiring. `Secure` is enabled when
    `ARMACTL_WEB_HTTPS_REQUIRED=true`; Lax keeps localhost/dev form redirects
    usable while blocking routine cross-site POST cookie sending.
11. Foreground `armactl web run` smoke launcher: ensures runtime, honors
    transient `--host/--port`, prints a safe startup summary, and runs the
    FastAPI app with Uvicorn. `--dev` enables Uvicorn reload for local Python,
    template, and CSS work.
12. Web permission categories foundation: owner has all declared code-level
    permissions and dashboard routes require dashboard:view.
13. Expanded read-only dashboard parity: the dashboard now shows safe
    lifecycle, service/timer, paths, config, players, telemetry, host, ports,
    mods, web runtime, Telegram-bot summary, empty-state, and partial-data
    sections via the web facade.
14. Controlled default-instance start/stop/restart actions: POST-only routes
    require auth, actions:run permission, CSRF, confirmation for stop/restart,
    and append safe JSONL audit entries before rendering controlled results.
15. Web localization and appearance preferences: request-scoped language
    resolution reuses existing locale JSON files and exposes server-rendered
    language/theme controls through web-owned cookies. Theme switching is
    progressively enhanced with package-local JS so the root `data-theme`
    changes immediately, then persists through a CSRF-protected lightweight
    POST. Language switching still uses server-rendered labels, but the
    preference write can complete through the same lightweight response before
    one page reload, avoiding an extra dashboard snapshot rebuild.
16. Background job metadata foundation: `web_jobs` stores web-runtime job
    status, progress, timestamps, bounded redacted stdout/stderr tails, and
    safe result/error metadata.
17. Background dispatcher/read-only jobs UI foundation: a pluggable runner can
    run explicitly registered safe handlers, terminal jobs do not rerun, handler
    failures are redacted into job metadata, and authenticated users with
    jobs:view can inspect recent jobs at `/jobs`. No install/repair/update or
    large-file handlers are registered yet.
18. Read-only management pages for `/config`, `/mods`, `/admins`, and `/bot`:
    authenticated users with the matching view permissions can inspect safe
    server config summaries, active mods, game admins, and Telegram bot status
    without exposing secrets or mutating game/server state.
19. Production systemd service foundation: `templates/armactl-web.service.j2`
    and `armactl web service install/start/stop/restart/status` allow the
    panel to be installed as an enabled always-on service without starting it
    during install. Enable/disable wrappers are available as explicit commands.
    This foundation assumes the current source-checkout deployment model with
    the repo-local `.venv`; a future wheel-only deployment path should revisit
    template/interpreter discovery.
20. Web security hardening foundation: login attempts are throttled through
    digest-only SQLite state keyed by HMAC(session secret, client IP, normalized
    username), and external-bind/HTTPS exposure warnings appear in safe CLI,
    service, foreground-run, and dashboard summaries. Optional IP allowlist and
    trusted proxy handling are explicitly future work and are not implemented.
21. Web smoke/deployment documentation: `docs/web-deployment.md` covers local
    foreground smoke checks, source-checkout VM/systemd smoke checks, reverse
    proxy / HTTPS topology, port guidance, and troubleshooting for the current
    source checkout plus repo `.venv` deployment model. Real remote HTTPS smoke
    remains a separate validation step.
22. Bounded read-only logs/report views: authenticated users with `logs:view`
    can inspect the web audit log, fixed service journals, and a redacted
    diagnostic report preview through `/logs` and `/report`. Output is
    bounded, redacted, non-streaming, and does not accept arbitrary paths.
23. Safe file browser foundation: authenticated users with `files:read` can
    inspect fixed local roots under the default instance at `/files`,
    download one validated file as an attachment, and upload one new file into
    the `server` root without overwriting existing targets. Browser input is
    root id plus relative path only; absolute paths, traversal, source/system
    roots, `.git`/`.venv`, symlink escapes, unsafe filenames, and oversized
    uploads are rejected. Other roots remain browse/download only for now;
    overwrite, delete, rename, and archive extraction remain future work.

24. Install/repair web job flows: lifecycle-aware dashboard actions can enqueue
    `server:install` for `not_installed` and `server:repair` for `incomplete`
    without running long operations inside the HTTP request. The route starts a
    web-process background worker thread for the queued job, and the explicit
    server job dispatcher registers safe handlers that stream installer/repair
    generator output into bounded redacted job tails. A durable standalone
    worker daemon remains future hardening; update remains future work.
25. Basic web config editing: `/config` now supports CSRF-protected
    `settings:manage` edits for allowlisted `game.name`, `game.scenarioId`,
    `game.maxPlayers`, `game.visible`, BattlEye, and server distance fields.
    Saves create a web-specific adjacent backup before using `config_manager`
    atomic write, never expose secrets, and never auto-restart the server. Raw
    JSON editing remains future emergency/admin-only work, not part of the
    normal operator config flow.
    Config saves also append a safe audit entry with changed field names and
    backup path.
26. Lightweight dashboard live refresh: `/dashboard/status.json` exposes a
    small authenticated `dashboard:view` JSON DTO built from the existing
    facade/view-model, while package-local `dashboard.js` polls every 7
    seconds and updates marked DOM fields without replacing the no-JS
    server-rendered dashboard fallback. WebSocket remains out of scope.
27. Dashboard snapshot layout polish: the main config card spans the full
    dashboard width, service/live/mod cards sit below as compact tiles, and
    ServerAdminTools unavailable state moved into diagnostics instead of
    appearing as unavailable plus Warning: none.
28. Dashboard visual meters: the dashboard status DTO now includes safe numeric
    FPS/CPU/RAM/disk metrics, the server-rendered dashboard shows compact
    meters with a no-JS text fallback, and package-local `dashboard.js` updates
    meter bars through the existing polling path. A dedicated FPS history chart
    is postponed until the visual design is reviewed.
29. Shared operator UI primitives: dashboard and config pages now use reusable
    status pills, key-value/value-block layouts, and notice styles for
    restart-required, warning, success, and unavailable states. Keep future page
    work on these flat primitives and avoid nested cards.
30. Safe web game-admin management: `/admins` now keeps read-only visibility
    under `admins:view` and adds authenticated CSRF-protected add/update/remove
    forms for users with `admins:manage`. Mutations go through a thin web
    adapter over `admins_manager.add_admin()` / `remove_admin()`, require
    confirmation for remove, render controlled success/error/unchanged states,
    and append redacted JSONL audit events. Game admins remain separate from
    web users/roles; SAT guard, raw JSON editing, and bulk/advanced admin flows
    remain future work.
31. Safe web mod management: `/mods` now keeps read-only visibility under
    `mods:view`, shows active and disabled mods separately, and adds
    authenticated CSRF-protected add/update, enable, disable, and confirmed
    remove forms for users with `mods:manage`. Mutations go through a thin web
    adapter over `mods_manager` helpers, render controlled
    success/error/unchanged states, show restart-required only after real
    changes, and append redacted JSONL audit events with compact cleanup
    summary for remove. Bulk paste/import/export/clear-all/modpack workflows,
    raw JSON editing, and archive extraction remain future work.
32. Players / moderation foundation: `/admins` now includes a lightweight
    moderation section backed by the existing `player_view`/RCON roster path.
    It shows current players, filters by nickname or reliable ID through a
    server-rendered GET query, and reuses the existing admin action flow to add
    a player as a game admin only when a stable admin reference is available.
    Players without reliable identity stay read-only. No player registry,
    IP storage, ban/unban action, or banlist manager is implemented in this
    slice.
33. Player registry foundation: `/players` now provides a read-only
    instance-scoped player registry backed by `<instance>/players.db`, not
    `web.db`. The registry stores only reliable IDs, current nickname,
    nickname history, first/last seen, seen count, and source. It does not
    store IP addresses, tokens, sessions, or secrets. Recording current online
    players is an explicit CSRF-protected POST refresh using the existing
    `player_view`/RCON roster path; GET remains read-only and does not create
    the database. Ban/unban, banlist manager, session duration, activity
    history, details pages, and extra log/SAT ingestion remain future work.
34. Pending restart marker: config, game-admin, and mod changes saved through
    web now create or update a per-instance marker in `web.db` when they really
    change server state. The dashboard shows this marker in the Recent Jobs
    area as operator work still needing a game-server restart, and a successful
    web restart action clears it. The marker is web/operator metadata, not a
    fake background job.

Planned but not implemented:

- Schedule and boot policy visibility are implemented for the web panel. On
  the serhiivka VM, armareforger.service was disabled while
  armareforger-restart.timer was enabled with calendar restarts at 06:00 and
  18:00 plus Persistent=true. The web `/schedule` page now shows service
  enabled state, timer enabled state, OnCalendar values, next/last run, and an
  autostart warning, then mutates timer set/enable/disable/restart-now and
  game-service autostart enable/disable through service_manager with CSRF,
  permissions, and audit logging.
  UI follow-up: `/schedule` is functionally accepted for now, but it should get
  a later polish pass. The timer, game-service autostart, and restart-now
  controls are currently dense; regroup them into calmer operator sections once
  the remaining management flows are in place.
- Host reboot/shutdown controls are useful later, but must be separate from
  game-server controls, owner/admin-only, double-confirmed, and audited.

- Player registry and moderation are now part of the web roadmap. Store player
  identity/activity data per instance, for example in ~/armactl-data/INSTANCE/players.db,
  not only in the web runtime database. Use reliable RCON/log/SAT adapters for
  IDs and nicknames, never infer stable identity from nicknames or A2S counts,
  and require auth, permissions, CSRF, audit logging, and backups for ban/unban
  flows.

Next recommended implementation order:

1. Run a real remote HTTPS smoke test on a target VM/proxy pair and record the
   environment-specific outcome.
2. Add optional IP allowlist / trusted proxy handling if operators need direct
   external bind deployments; this is not implemented yet.
3. Smoke-test the web schedule controls and boot/autostart warning on a real
   target VM, because this affects remote recovery expectations after VM reboot.
4. Add a diagnostics command palette before any browser terminal. It should run
   only registered safe commands such as status, timer status, port checks,
   config validation, bounded logs/report collection, and web/game service
   status. Use jobs, permissions, CSRF, audit, bounded/redacted output, and no
   arbitrary shell input.
5. Treat a full web terminal as a disabled-by-default break-glass future flow,
   equivalent in risk to SSH. It must require HTTPS, trusted proxy/IP allowlist,
   explicit operator permission, extra re-auth, short-lived sessions, transcript
   auditing/redaction, and clear separation from normal dashboard operations.
   Before implementing terminal, host controls, premium diagnostics, or
   allowlist management, add and pass the security review gate from
   `docs/web-interface-plan.md`.
6. Add update flow to explicit background job handlers if a safe backend API is
   introduced.
7. Add edit/save/delete flows for bot settings and extended config fields
   through existing backend modules; keep any raw JSON config editor as a
   separate emergency/admin-only design. Advanced/bulk admin workflows remain
   future and should still avoid mixing game admins with web users/roles.
   Advanced modpack workflows such as bulk paste, import/export, and clear-all
   remain future and should keep remove/cleanup confirmations explicit.
8. Add atomic overwrite/delete/rename flows on top of the safe filesystem
   adapter after single-file upload has been reviewed.
9. Add player registry details/history and ban-list management after the
   identity ingestion source is validated on a real server log/RCON sample.
   The future step should add recent/history/detail views, session duration,
   banlist manager, explicit permissions, CSRF, confirmation, backups when a
   config-backed list changes, audit logging, and no player IP storage by
   default.

## Implementation prompt template

Use this template for a new implementation chat:

```text
You are implementing the armactl web panel in the WSL repo
`/home/deus/projects/armactl` on branch `feat/web-interface`.

Read these files first:
- docs/web-interface-plan.md
- docs/web-interface-handoff.md
- docs/checklist.md
- docs/architecture.md
- docs/development.md

Task:
<one focused Phase 16 task>

Constraints:
- Work only in the WSL checkout.
- Do not touch the Windows E: checkout.
- Keep backend logic in reusable modules, not route handlers.
- Do not import TUI code from web code.
- Do not shell out to armactl --json-output for normal web behavior.
- Add focused tests for the behavior you change.
- Keep the diff small.
- Do not commit. Leave the working tree ready for review and suggest a concise
  commit message.

Before finishing, run the relevant tests and `git diff --check`.
Report:
- files changed
- tests run
- any known gaps or follow-up tasks
- suggested commit message
```

## Review prompt template

Use this template for a review chat:

```text
Review the latest uncommitted implementation changes on
`/home/deus/projects/armactl`, branch `feat/web-interface`.

Use a code-review stance. Prioritize:
- security regressions
- auth/session/CSRF mistakes
- filesystem path traversal or unsafe writes
- route handlers containing business logic
- TUI imports inside web code
- subprocess usage where backend modules should be called directly
- missing tests
- packaging/bootstrap omissions
- docs/checklist drift

Check the diff against:
- docs/web-interface-plan.md
- docs/web-interface-handoff.md
- docs/checklist.md Phase 16
- docs/architecture.md
- docs/development.md

Run relevant tests and `git diff --check` if possible.
Return findings first with file/line references, then test coverage and a short
summary. If there are no findings, say so clearly, mention residual risk, and
commit the approved changes with a concise message.
```

## Review gates

Every web-panel step should answer these before merge/next task:

- Does it reuse existing backend modules where possible?
- Does it avoid TUI imports and terminal-only formatting?
- Are new route handlers thin?
- Are secrets redacted?
- Are mutating actions protected by auth, permissions, and CSRF once auth
  exists?
- Are path operations contained inside explicit allowed roots?
- Are long-running operations modeled as jobs instead of request blocking?
- Are package data and bootstrap changes included when templates/static/deps are
  added?
- Are docs/checklist updated when scope changes?

## Planned safety/UX slices

Keep these as focused follow-up slices, not as drive-by changes mixed into
unrelated feature work:

- Floating notifications: move save/action success and error results into
  anchored toast-style UI so forms and previews do not jump to the top of the
  page after POST/GET actions.
- Notification indicator: add a topbar indicator/center for persistent operator
  work such as saved config/admin/mod changes that still require a game-server
  restart, plus important exposure/runtime warnings.
- File delete: implement safe single-file deletion only after the notification
  slice. It must be POST-only, authenticated, permission-checked, CSRF-checked,
  confirmed by the operator, audited, and constrained to the existing file
  browser roots/path validation. Do not add recursive directory deletion in the
  first delete slice.
- Upload safety: treat uploaded files as untrusted bytes. Do not execute,
  import, source, unpack, or auto-apply uploaded content. Keep filename/path/size
  validation, bounded redacted preview, no overwrite by default, and add tests
  around these guarantees before expanding upload workflows.
- Logs polish: `/logs` and `/report` are accepted as functional read-only
  operator views. Future work should make audit JSONL readable, add bounded
  download/export, optional live follow or auto-refresh, filters/search, and
  `ERROR`/`WARNING` highlighting while keeping sources fixed and allowlisted.
