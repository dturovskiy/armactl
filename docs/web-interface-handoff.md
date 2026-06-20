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
- Use one checkout per task. Do not split uncommitted changes across Windows and
  WSL copies. If another checkout needs the result, sync it only through
  `git pull --ff-only` after the reviewed commit is pushed.
- Keep changes small and reviewable.
- Do not commit from the implementation chat. Leave changes in the working tree
  for the review chat.
- The review chat creates one focused commit per approved implementation step.

## Implementation rules

- Follow the web architecture guardrails in `docs/web-interface-plan.md` before
  adding new routes, services, UI actions, tests, or storage tables.
- Keep backend logic in reusable Python modules, not in TUI screens or web route
  handlers.
- Do not import Textual/TUI code from web code.
- Do not shell out to `armactl --json-output` for normal web behavior; call the
  existing Python backend modules directly.
- Add a web-facing facade/DTO layer before routes start combining many backend
  calls.
- Keep routes thin: auth, permission, CSRF/input validation, one service/facade
  call, and response rendering. Put multi-step workflows in `services/` or
  facades. Services own workflow, audit, pending-work, rollback, and correction
  audit behavior. Low-level adapters should stay pure filesystem/system/db
  operations without HTTP/session knowledge.
- Every mutating web action needs auth, explicit permission, POST+CSRF,
  bounded input, audit logging, controlled errors, and backups/pending-work
  handling when applicable.
- Normal `config.json` editing belongs on `/config` with structured safe
  controls. Do not turn `/files` into the primary config editor.
- Any raw JSON config editor must be a future owner/admin-only break-glass
  button or mode inside `/config`, with explicit permission, CSRF, double
  confirmation, JSON validation, backup, audit, redacted errors, and pending
  restart/work behavior.
- Player registry and moderation must use reliable identity/admin references;
  never invent IDs from nicknames or A2S slot-only data, and do not store player
  IPs by default without a future privacy/security review.
- Ban/unban flows must be separate from web users/roles, require reliable
  identity/SteamID64/supported backend IDs, POST+CSRF, confirmation, audit, and
  backup/rollback where file-backed.
- Keep ServerAdminTools and other mod runtime settings in future Mod
  Settings or Diagnostics surfaces, not dashboard noise. Dashboard shows SAT
  only for real health/guard problems, and SAT admins/gameMasters/bans edits
  must be narrow field edits that preserve unrelated SAT settings, with backup
  and audit.
- Keep the current web MVP Linux/systemd-first. Windows backend support needs a
  future platform adapter for services, logs, paths, firewall/process/metrics,
  and install/update flow.
- Web game-service and restart-timer mutations must go through
  `armactl.platform.service_adapter.ServiceAdapter`. The current default is the
  Linux/systemd adapter over `service_manager`; CLI/TUI direct
  `service_manager` imports remain compatibility paths until later migration
  slices.
- Future `/schedule` web UI must not accept bare restart times without timezone
  context. The browser should submit an IANA timezone name, the backend validates
  it, normalizes the schedule to UTC for systemd/backend storage, displays both
  user-local and UTC equivalents, labels next/last run timezones explicitly, and
  audits local input, timezone, and normalized UTC values. Treat existing
  systemd `OnCalendar` values as UTC unless proven otherwise.
- Keep product tiers, roles, and low-level permissions separate. Dangerous
  features such as raw config editor, advanced config, host controls, command
  palette, terminal, banlist, file edit/delete/overwrite, and SAT runtime edits
  need explicit permission gates and audit. A tier such as `mega` is eligibility,
  not authorization by itself; every route/action still checks a named
  permission.
- Treat config editing as three levels: `settings:manage` for existing
  allowlisted safe fields, `settings:advanced` for future ports/RCON/A2S,
  crossplay/platform/third-person/security fields, and `config:raw_edit` for
  owner/mega-eligible break-glass raw JSON with double confirmation, validation,
  backup, audit, redacted errors, and pending-work behavior.
- Keep pending operator work separate from background jobs. Do not fake pending
  restart work as a queued job, and do not hide pending work just because the
  background job list is empty.
- Write web tests against stable service/page_model/facade/adapter boundaries.
  Avoid mutating `sys.modules` in the main pytest process; use subprocess checks
  for import safety. Do not monkeypatch FastAPI route globals, endpoint
  `__globals__`, or app router endpoint internals.
- Keep `dturovskiy/armactl-website` separate. It is the public marketing site,
  not the authenticated management panel.
- Do not expose arbitrary host filesystem access. Future file edit/delete,
  overwrite, rename, and archive extraction flows need fixed roots, path jail,
  traversal and symlink-escape rejection, CSRF, explicit confirmation for
  destructive actions, audit, and backup/quarantine or rollback where practical.
- Do not run install/repair/update as blocking HTTP requests.
- Future server update work must keep version check separate from update
  execution. The check is read-only or a lightweight/background job that feeds a
  dashboard read model with installed build, latest available build when safely
  known, and `up to date` / `update available` / `unknown` / `check failed`
  state. Check failures must not break dashboard rendering or status JSON. If
  installed version/build equals the latest available version/build, do not
  enqueue an update job; show `Server is already up to date` as a controlled
  successful no-op, not a failure or background job. Audit the safe check result
  without secrets. If latest is unknown or the check failed, do not run update
  automatically; any future operator override needs its own explicit policy.
- Future update routes must only perform auth, explicit `server:update` or
  `jobs:update` permission, CSRF, operator confirmation, service/job enqueue,
  and response rendering. Do not shell out to SteamCMD/systemctl from a route.
- Future update workflows live in service/adapter layers: permission -> CSRF ->
  intent audit -> enqueue/mutation -> outcome audit -> job/progress state.
  Update jobs must be idempotent/deduplicated by kind/instance like
  install/repair jobs, expose bounded redacted progress/logs in `/jobs`, and
  produce controlled failure results with no secrets in logs.
- Update must not run automatically by default. If the game server is running,
  require explicit confirmation; optionally stop/drain before update; restart
  only when the operator confirms or when the update workflow explicitly owns
  restart. Preserve config/state and show rollback/recovery notes where the
  backend can provide them.
- Version/update backend code must be adapter-backed for Linux/systemd now and
  future Windows support later. SteamCMD, app manifest, log/version metadata,
  systemctl, process, and path logic must not spread through routes or
  templates. Do not store Steam credentials or secrets in `web.db`.
- Treat web as an always-on service: normal setup is `./armactl web`; `armactl web run` is for foreground development/debugging, while production uses `armactl-web.service`.
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
- Pseudolocalization is planned as a dev/test-only QA mode to catch missing
  keys, raw hardcoded template text, and layout overflow before UI polish.
  Resolve language per web request/session/user and do not call the TUI-style
  global `toggle_lang()` / `save_lang()` helpers during HTTP request handling.

## Future users, policy, recovery, and allowlist rules

Before implementing paid features, browser terminal, host reboot/shutdown, raw
config editing, destructive file actions, SAT/mod runtime settings, premium or
mega tools, IP allowlists, or web user administration, follow the foundation in
`docs/web-interface-plan.md`.

Hard rules for those future slices:

- Keep web users, Arma/game admins, and player registry records as separate
  models. Do not merge them into one users table, and do not infer links from
  nicknames, Steam IDs, tier names, or role names.
- Keep roles, named permissions, and paid tiers/entitlements separate. A tier is
  not a permission, a permission is not a billing plan, and `premium` or `mega`
  must not silently mean admin.
- Add a central policy/feature-gate service before dangerous or paid actions.
  Routes and templates may call the service or receive its DTO results, but
  they must not own tier/IP/device/permission decisions.
- Add a typed settings registry for runtime/product/security settings before
  scattering flags through route handlers, templates, or ad hoc JSON keys.
- Add explicit migrations for new `web.db` users, roles, grants, entitlements,
  allowlists, trusted proxies, recovery tokens, security settings, and future
  device registrations.
- If the current structure does not fit the policy/user/settings boundary,
  refactor first. Do not add route/template workarounds to get one feature over
  the line.

The future system admin area should live on a dedicated surface such as
`/system/users` or `/users` and support creating web users, changing roles,
changing or resetting passwords, disabling/enabling users, resetting sessions,
and auditing every change. It must prevent deleting, disabling, or demoting the
last owner-level user. First-owner bootstrap stays local CLI/SSH only.

Dangerous features need explicit policy before UI work starts: command palette
or terminal, host reboot/shutdown, raw JSON config editor, file
delete/edit/overwrite, SAT/mod runtime settings, user/role/tier management, IP
allowlist management, and paid/premium tools. Require auth, named permission,
POST + CSRF for mutations, audit intent and outcome, operator confirmation, and
optional re-auth/passkey/2FA plus IP/VPN/trusted-device gates for high-risk
actions. No silent bypass in routes, templates, job enqueue code, workers, or
feature flags.

IP allowlists are only additional defense. They do not replace web auth,
permissions, CSRF, confirmations, or audit. Account for mobile IP churn,
VPN/provider changes, roaming, and operators away from home networks. Trust
forwarded client IP headers only from explicitly configured trusted proxies.
Bad allowlist state must be recoverable through local CLI/SSH, not a hidden
public web bypass.

Break-glass recovery must be official and local: a CLI/SSH command creates a
short-lived one-time recovery token, logs/audits creation and use, and can reset
an owner password or create a new owner-level web user. It must not work through
unauthenticated public web access and must not bypass audit silently.

Future mobile/device trust is not MVP. If added, prefer per-device keypairs,
server-registered public keys, challenge-based signed requests, revocation, and
device metadata. Device trust never replaces user authentication, named
permissions, CSRF protection for browser flows, or audit.

## Current mutating route audit inventory

This inventory covers the current FastAPI POST routes. There are no registered
PUT, PATCH, or DELETE routes in the web app at this point. Cookie-only/auth
state is documented explicitly so it does not get mistaken for missing operator
audit coverage.

| Route/action | Permission | CSRF | Audit | Pending work | Secret handling |
|--------------|------------|------|-------|--------------|-----------------|
| `POST /login` | Public login when owner exists | login CSRF cookie/form token | No web action audit; auth/session/rate-limit state only | No | Password never logged; generic errors; throttle stores digest-only state |
| `POST /logout` | Authenticated session | Yes | No web action audit; session revocation only | No | Clears session/CSRF cookies |
| `POST /preferences/language`, `POST /preferences/theme` | None; authenticated requests validate session CSRF | Yes when authenticated | No audit; local web preference cookie only | No | Values are normalized allowlisted UI preferences |
| `POST /service/{start,stop,restart}` | `actions:run` | Yes; stop/restart require confirmation | `start`, `stop`, `restart` | Successful performed `restart` clears restart-related pending work | Backend messages are redacted before audit/rendering |
| `POST /jobs/server/install`, `POST /jobs/server/repair` | `actions:run` | Yes | `job.server-install.enqueue`, `job.server-repair.enqueue` | No; these are background jobs in `web_jobs`, not pending operator work | Job metadata/output tails are bounded and redacted |
| `POST /files/{root_id}/upload` | `files:write` plus root upload allowlist | Yes | `file.upload` intent before final publish, `file.upload` success outcome after publish, `file.upload.publish-failed` if publish fails | No; upload-new-file does not imply a known restart/apply step | Audit stores root/path/size only, no file contents |
| `POST /config` | `settings:manage` | Yes | `config.save` when fields change | Stacks `config` restart work when changed | Allowlisted non-secret fields only; backup path/details redacted |
| `POST /mods/add`, `/mods/disable`, `/mods/enable`, `/mods/remove` | `mods:manage`; remove requires confirmation | Yes | `mod.add`, `mod.update`, `mod.disable`, `mod.enable`, `mod.remove` | Stacks `mods` restart work when changed | IDs/names/messages are bounded and redacted |
| `POST /admins/add`, `/admins/remove` | `admins:manage`; remove requires confirmation | Yes | `admin.add`, `admin.update`, `admin.remove` | Stacks `admins` restart work when changed | Admin references/labels/messages are bounded and redacted |
| `POST /players/refresh` | `players:view` | Yes | `players.refresh` intent/outcome | No | Intent audit gates roster/persist; source/storage failures write controlled failure outcome audit; no IP storage by default |
| `POST /schedule/set`, `/schedule/enable`, `/schedule/disable`, `/schedule/autostart/enable`, `/schedule/autostart/disable`, `/schedule/restart-now` | `schedule:manage`; restart-now and autostart-disable require confirmation | Yes | `schedule.set`, `schedule.enable`, `schedule.disable`, `service.autostart-enable`, `service.autostart-disable`, `schedule.restart-now` | Successful performed `schedule.restart-now` clears restart-related pending work | Schedule/service messages are bounded and redacted |

Future update route inventory target: when `POST /jobs/server/update` or an
equivalent route is added, it should use `server:update` or `jobs:update`, CSRF,
explicit impact confirmation, intent/outcome audit events, and background
`web_jobs` metadata only. It should not create pending operator work unless the
operator must take a separate manual action after the job.

## Broad Exception Audit Inventory

As of the mutating-route cleanup slice, broad except Exception usage in
src/armactl/web/routes and src/armactl/web/services has been audited.

Removed/replaced:

- routes/mods.py and routes/admins.py no longer catch generic backend
  exceptions around mutating action workflows. Domain/config failures are
  returned by mod_actions/admin_actions as controlled, redacted results;
  unexpected RuntimeError-style bugs propagate as generic 500 responses instead
  of fake action results.
- routes/service.py and routes/schedule.py no longer catch generic workflow
  exceptions. ServiceResult failures remain controlled; unexpected exceptions
  do not clear pending work or render completed/unavailable action panels.
- services/mod_actions.py and services/admin_actions.py no longer catch generic
  manager exceptions after validation/config discovery. ConfigError remains the
  explicit controlled domain failure path.
- services/service_actions.py no longer catches generic service-manager
  execution exceptions after preflight checks.

Intentionally remaining broad catches:

| Location | Classification | Reason |
|----------|----------------|--------|
| routes/dashboard.py | legit degradation fallback | Read-only dashboard HTML/status JSON return controlled unavailable responses instead of tracebacks. |
| services/log_views.py | legit fail-closed diagnostics | Fixed log/report sources are diagnostics; unavailable sources render bounded controlled placeholders. |
| page_models/players.py | legit degradation fallback | Current-player moderation panel is best-effort read-only UI data and must not block the admins page. |
| services/mod_actions.py, services/admin_actions.py | legit fail-closed preflight | Discovery failure happens before mutation and becomes a controlled config-path unavailable domain result. |
| services/service_actions.py, services/schedule_actions.py | legit fail-closed preflight | Discovery failure happens before service/timer mutation and returns controlled unavailable results. |
| services/pending_work.py | legit fallback/degradation | web.db write/list failures fall back to private sidecar storage or sidecar-only listing. |
| services/server_job_actions.py | best-effort cleanup | If audit fails after creating a job, cancellation is attempted but the original audit failure remains the reported error. |

## Current implementation status

Completed foundation:

1. `src/armactl/web/` package skeleton and import-safety tests.
2. Optional web dependencies, package-data/bootstrap support, and `scripts/run-web`.
3. Web-facing page-model/DTO layer for read-only dashboard data.
4. FastAPI app factory, `/healthz`, package-local templates/static assets, and
   minimal read-only dashboard routes.
5. Web runtime storage/config/db foundation under `~/armactl-data/web/`.
6. One-command `./armactl web` setup flow for normal first run; lower-level `armactl web init` remains available for manual runtime config and DB setup.
7. Auth DB/password foundation: `web_users`, Argon2 password helpers, and
   minimal active `owner` user helpers.
8. Owner-user setup flow through `./armactl web` or explicit `armactl web init --owner USERNAME`, with hidden password confirmation and safe operator summary output.
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
    sections via the dashboard page-model loader.
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
    jobs:view can inspect recent jobs at `/jobs`. Install and repair handlers
    are registered through explicit server job flows; update and large-file
    handlers remain future work.
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
    worker daemon remains future hardening; update remains future work and must
    add a read-only version check/read model before exposing an update action.
    The job-store duplicate-active persistence blocker is closed for these
    flows: schema maintenance cancels pre-existing duplicate active rows while
    keeping the oldest active job, active lookup has an indexed
    `(kind, instance, status, created_at, id)` path, and `/jobs` exposes
    job-store integrity warnings separately from pending operator work. Follow-up
    owner: next jobs/update slice. Run query-plan and latency checks on
    production-scale job history before adding update jobs or a standalone
    worker daemon.
25. Basic web config editing: `/config` now supports CSRF-protected
    `settings:manage` edits for allowlisted `game.name`, `game.scenarioId`,
    `game.maxPlayers`, `game.visible`, BattlEye, and server distance fields.
    Saves create a web-specific adjacent backup before using `config_manager`
    atomic write, never expose secrets, and never auto-restart the server. Raw
    JSON editing remains future emergency/admin-only work, not part of the
    normal operator config flow. Future raw JSON editing must be an
    owner/admin-only break-glass mode inside `/config`, not `/files`.
    Config saves also append a safe audit entry with changed field names and
    backup path.
26. Lightweight dashboard live refresh: `/dashboard/status.json` exposes a
    small authenticated `dashboard:view` JSON DTO built from the existing
    facade/view-model, while package-local `dashboard.js` polls every 7
    seconds and updates marked DOM fields without replacing the no-JS
    server-rendered dashboard fallback. WebSocket remains out of scope.
27. Dashboard snapshot layout polish: the main config card spans the full
    dashboard width and service/live/mod cards sit below as compact tiles.
    Neutral ServerAdminTools-missing state is not dashboard-worthy by itself.
    Keep SAT on the dashboard only for real guard/health problems; put normal
    SAT absence and runtime config details into future Diagnostics / Mod
    Settings pages.
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
    Players without reliable identity stay read-only. This `/admins` surface is
    only a quick-add convenience; the full registry and moderation workflow
    belongs to `/players` and future Players / Moderation views. No IP storage,
    ban/unban action, or banlist manager is implemented in this slice.
33. Player registry foundation: `/players` now provides a read-only
    instance-scoped player registry backed by `<instance>/players.db`, not
    `web.db`. The registry stores only reliable IDs, current nickname,
    nickname history, first/last seen, seen count, and source. It does not
    store IP addresses, tokens, sessions, or secrets. Recording current online
    players is an explicit CSRF-protected POST refresh using the existing
    `player_view`/RCON roster path; GET remains read-only and does not create
    the database. Ban/unban, banlist manager, session duration, activity
    history, details pages, and extra log/SAT ingestion remain future work.
34. Pending operator work: config, game-admin, and mod changes saved through
    web now create or update category-scoped pending work in `web.db` when they
    really change server state. `config`, `admins`, and `mods` stack without
    overwriting each other; repeated saves in the same category update that
    category. The dashboard shows one compact Pending operator work table
    with a single View all work link and hides empty background jobs. `/jobs`
    shows Pending operator work as dense rows in a separate section from
    Background jobs, with precise empty states. Pending work is
    web/operator metadata in
    `web_pending_work`, not a fake `web_jobs` row, and successful web restart
    clears only restart-related pending work, including legacy migrated
    pending-restart rows. The service result page now shows operator-friendly
    restart copy and keeps backend diagnostics secondary and redacted.
35. Web management route split: /config, /mods, /admins, and /bot now live in
    explicit domain routers under src/armactl/web/routes/. The split preserves
    existing URLs, permissions, templates, CSRF checks, audit calls, and
    pending-work behavior. The oversized tests/test_web_app.py split has
    been completed in focused route/page modules.
36. Web page-model split: the old facade.py monolith has been split into
    domain DTO loaders under src/armactl/web/page_models/ for dashboard,
    config, mods, admins, bot, and schedule. Route modules call loaders through
    page-model module aliases so tests can patch that stable seam without
    touching FastAPI route internals. The remaining facade.py is a thin
    compatibility re-export layer only; it contains no DTO-building logic.
37. Web filesystem service split: the former services/filesystem.py monolith now
    lives in filesystem_errors, filesystem_roots, filesystem_paths,
    filesystem_listing, filesystem_preview, and filesystem_transfer.
    services/filesystem.py remains only as a thin public re-export facade for
    compatibility. Delete, edit, overwrite, rename, and archive extraction
    remain future work. The repeat architecture/modularity audit is recorded in
    `docs/system-modularity-audit-results-20260619.md`.

38. Web app test split: the oversized tests/test_web_app.py catch-all has
    been reduced to app-level wiring/static smoke coverage. Focused modules now
    cover auth routes, preferences, dashboard/status pages, management layout,
    jobs routes, and service routes. Touched tests patch stable service,
    page-model, and adapter seams rather than imported route globals or FastAPI
    endpoint internals.
39. Platform service adapter boundary: web service start/stop/restart,
    schedule set/enable/disable, schedule restart-now, and game-service
    autostart enable/disable now use `platform/service_adapter.py`. The default
    backend is Linux/systemd through the existing `service_manager`, preserving
    `armareforger.service` and restart timer naming. CLI/TUI direct
    `service_manager` imports are intentionally left as compatibility/migration
    path for future smaller slices. Tests for the touched web action paths patch
    the adapter seam rather than systemd internals.
40. Player registry / moderation boundary split: current-player collection from
    `player_view`/RCON now lives in `services/player_sources.py`; reliable
    identity/text normalization lives in `services/player_identity.py`;
    instance-scoped SQLite storage and `players.db` mode/query/snapshot logic
    stay in `services/player_registry.py`; `/players` and `/admins` player
    DTO/loaders live in `page_models/players.py`; and explicit refresh intent,
    registry snapshot, and outcome audit remain in `services/player_actions.py`.
    Banlist, session tracking, activity timeline, detail pages, and extra
    ingestion adapters remain future work. Reliable identity/admin reference is
    still required for persistence/moderation, and player IP storage remains
    off by default.
41. Web/player DB migrations: `web.db` now uses an explicit schema-version
    runner at version 8, preserving current auth, session, CSRF, jobs,
    rate-limit, and pending-work tables while migrating older/minimal shapes
    before running duplicate-active job maintenance. Instance `players.db` now has
    `player_registry_schema_meta.schema_version`, an idempotent version 1
    runner for `players`/`player_names`, read-path migration for existing DBs,
    and preserved mode `0600`. No IP storage, banlist, activity tracking, or
    user-facing feature surface was added in this slice.

Implemented polish and future work:

- Schedule and boot policy visibility are implemented for the web panel. On
  the serhiivka VM, armareforger.service was disabled while
  armareforger-restart.timer was enabled with calendar restarts at 06:00 and
  18:00 plus Persistent=true. The web `/schedule` page now shows service
  enabled state, timer enabled state, OnCalendar values, next/last run, and an
  autostart warning, then mutates timer set/enable/disable/restart-now and
  game-service autostart enable/disable through the service adapter boundary
  with CSRF, permissions, and audit logging.
  UI follow-up: `/schedule` is functionally accepted for now, but it should get
  a later polish pass. The timer, game-service autostart, and restart-now
  controls are currently dense; regroup them into calmer operator sections once
  the remaining management flows are in place.
- Host reboot/shutdown controls are useful later, but must be separate from
  game-server controls, owner/admin-only, double-confirmed, and audited.
- Windows backend support is future architecture, not MVP. Keep current web
  work Linux/systemd-first until service/log/path/firewall/process/metrics and
  install/update adapter implementations are designed and tested.
- Config editor expansion should start with a verified `config.json` schema
  inventory, UI grouping, and safe/dangerous/secret/runtime field decisions.
  Future third-person and crossplay/platform controls belong in structured
  `/config` safe UI only after exact Arma Reforger keys, value shapes, restart
  behavior, and backend validation are verified; do not add them through
  `/files`. Booleans should be toggles/checkboxes, platform lists checkbox
  groups or segmented controls, numeric fields validated inputs, secrets kept
  out of casual views, and raw JSON kept as owner/mega-only break-glass work.
- Emergency raw JSON config editing remains future owner/admin-only break-glass
  work inside `/config`, not `/files`, with permission, CSRF, double
  confirmation, JSON validation, backup, audit, redacted errors, and clear
  restart-required/pending-work behavior.
- `/files` remains for safe browse/download/upload flows. Future delete and text
  editing, if added, must be separate from `/config`, root/path/extension/size
  limited, backed up, and audited.
- ServerAdminTools runtime config belongs to future Mod Settings or Diagnostics.
  Dashboard should show SAT only for real health/guard problems, and SAT
  admins/gameMasters/bans edits must preserve unrelated SAT config.

- Player registry foundation is implemented with instance-scoped
  `~/armactl-data/INSTANCE/players.db`, not only the web runtime database. The
  current boundary is split into source collection (`player_sources`), reliable
  identity normalization (`player_identity`), storage (`player_registry`), page
  DTOs (`page_models/players`), and refresh+audit workflow (`player_actions`).
  The registry DB is versioned through `player_registry_schema_meta` and keeps
  private file mode `0600` across migration runs. Player refresh now writes
  intent audit before roster/persist, audits source/storage failure outcomes
  as controlled service results, and preserves persisted data while reporting
  an audit problem if success outcome audit fails.
  Future details/history, extra ingestion, and ban/unban flows must use reliable
  RCON/log/SAT adapters for IDs and nicknames, never infer stable identity from
  nicknames or A2S counts, and require auth, permissions, POST+CSRF,
  confirmation, audit logging, and backups/rollback where applicable. Store no
  player IPs by default unless a later privacy/security review explicitly
  approves it.

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
6. Treat a full web terminal as a disabled-by-default break-glass future flow,
   equivalent in risk to SSH. It must require HTTPS, trusted proxy/IP allowlist,
   explicit operator permission, extra re-auth, short-lived sessions, transcript
   auditing/redaction, and clear separation from normal dashboard operations.
   Before implementing terminal, host controls, premium diagnostics, or
   allowlist management, add and pass the security review gate from
   `docs/web-interface-plan.md`.
7. Complete the config schema inventory before extending `/config`: verify exact
   Arma Reforger keys, group fields, and decide safe editor versus advanced
   editor behavior.
8. Add server version/update flow only after a safe backend adapter/API exists:
   dashboard version state first, then read-only/latest-version check, then an
   explicit deduped background update job with audit, progress/log visibility,
   running-server confirmation policy, no secrets in `web.db` or logs, and
   tests for dedupe, failure, audit, and dashboard states.
9. Add edit/save/delete flows for bot settings and extended config fields
   through existing backend modules; keep any raw JSON config editor as a
   separate owner/admin-only `/config` break-glass design. Advanced/bulk admin
   workflows remain future and should still avoid mixing game admins with web
   users/roles. Advanced modpack workflows such as bulk paste, import/export,
   and clear-all remain future and should keep remove/cleanup confirmations
   explicit.
10. Add atomic overwrite/delete/rename flows on top of the split safe
   filesystem adapter after single-file upload has been reviewed; do not use
   `/files` as the normal config editor.
11. Add player registry details/history and ban-list management after the
   identity ingestion source is validated on a real server log/RCON sample.
   Reuse the current source/storage/page/workflow split instead of mixing web
   DTOs, current roster collection, SQLite persistence, and audit workflow.
   The future step should add recent/history/detail views, session duration with
   connected/disconnected timestamps, banlist manager, explicit permissions,
   POST+CSRF, confirmation, backups/rollback when a file-backed list changes,
   audit logging, and no player IP storage by default.

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
- docs/system-modularity-audit.md when the slice touches whole-project boundaries

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

For web-only risky slices, use `docs/web-system-audit.md`. For whole-project modularity, platform backend, player/banlist domain, or broad refactor work, use `docs/system-modularity-audit.md` first and turn findings into small follow-up slices before implementation.

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
summary. If there are no findings, say so clearly, list only deferred non-blocking items with owner/next action, and
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
- Scheduled restart notice: when the restart timer actually runs, surface an
  informational notification with safe proof from an allowlisted systemd
  timer/service journal line or equivalent audit/source metadata.
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
- Settings IA: keep the dashboard as an operational summary. Basic config stays
  safe and common; mod settings, SAT diagnostics, A2S/RCON/network settings,
  diagnostics, raw JSON, backup restore, and destructive maintenance belong on
  focused pages or danger-zone flows instead of being dumped into `/dashboard`
  or the basic `/config` form.
