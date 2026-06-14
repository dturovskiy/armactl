# Web interface plan

## Goal

Add a browser-based management interface for armactl so an operator can manage a
remote Arma Reforger server without opening SSH and running CLI/TUI commands.

The web interface must run on the same Linux host/VM as armactl and the game
server. It should reuse the existing backend modules instead of duplicating TUI
logic.

The existing top-level `website/` directory is a marketing/static site. It is
not the management panel. The web panel must live under the Python package and
runtime service model described below. The marketing site can move to a
separate repository later, but that is not a blocker for the management panel.

The web panel should stay in this repository for now. It depends on armactl's
backend modules, runtime path model, systemd helper, templates, tests, and
release flow. Splitting it into a separate repository before the API boundary is
stable would add packaging and deployment friction without reducing risk.

The source repository and runtime data remain separate:

```text
~/projects/armactl/                 # source checkout
~/armactl-data/<instance>/          # game server runtime data
~/armactl-data/web/                 # web panel runtime settings and database
~/armactl-data/logs/                # centralized armactl-owned log files
/etc/systemd/system/armactl-web.service
```

## Non-goals for the first version

- Do not expose arbitrary host filesystem access.
- Do not run game-server installation or repair flows as long blocking HTTP
  requests.
- Do not make the static `website/` marketing page the management UI.
- Do not require Telegram bot setup.
- Do not require SSH for day-to-day server management after the web service is
  deployed.
- Do not make the web service the only management path. CLI and TUI must remain
  usable fallback tools.

## Runtime model

Add a separate optional service:

```text
armactl-web.service
  -> python -m armactl.web --host 127.0.0.1 --port 8765
```

Recommended production exposure:

```text
Internet/browser
  -> HTTPS reverse proxy on :443
  -> localhost armactl-web on 127.0.0.1:8765
  -> existing armactl backend modules
  -> systemd / ~/armactl-data/<instance>/
```

Direct `0.0.0.0` binding can exist for trusted LAN/VPN setups, but the safe
default should be localhost plus a reverse proxy such as Caddy or Nginx.

Local development/testing should run without system installation:

```text
python -m armactl.web --host 127.0.0.1 --port 8765 --dev
```

The local dev server should use the same backend modules but can point at a
temporary `ARMACTL_DATA_ROOT` fixture for tests.

Detailed local and VM smoke commands live in
[`docs/web-deployment.md`](web-deployment.md).

## Service lifecycle and launchers

The web panel is a long-running service, unlike the TUI. The TUI remains the
interactive default when an operator runs `./armactl` without subcommands; web
startup must be explicit through `armactl web ...`.

Use two launch layers:

- `scripts/run-web` for local/dev smoke tests. It should mirror
  `scripts/run-tui` and delegate to `./armactl web run "$@"` after the repo
  launcher has handled bootstrap and `.venv` refresh.
- `armactl-web.service` for production. `armactl web service install/start`
  should install and enable a systemd service that starts on boot, restarts on
  failure, and binds to the configured host/port.

`armactl web run` is useful for foreground debugging. It is not the normal
remote-server operating mode. After setup, operators should not need SSH just to
keep the panel available.

## Remote access and local smoke scenarios

Target remote scenario:

```text
Remote operator browser
  -> https://server-1.example.com/
  -> reverse proxy TLS termination
  -> armactl-web.service already running in the game VM
  -> login/session/role checks
  -> dashboard and permitted management actions
```

The browser request should not start the panel. `armactl-web.service` should
already be active after boot and waiting for connections in the background. The
reverse proxy forwards requests to that service. If the service is down, the
operator should see a clear proxy/service error and the maintainer can recover
through SSH, CLI, or TUI.

Remote access acceptance checklist:

- open the public HTTPS URL from a different network;
- log in as a web user and see the dashboard without SSH;
- verify unauthenticated requests redirect to login or return 401/403;
- verify the session cookie is secure for HTTPS deployments;
- verify server actions and file access respect the user's role;
- restart the VM and confirm `armactl-web.service` comes back automatically.

Local development acceptance checklist:

- run `./scripts/run-web --dev --data-root /tmp/armactl-web-dev`;
- open `http://127.0.0.1:8765/` in a local browser;
- confirm `--dev` reloads Python, template, and CSS changes without a manual
  foreground runner restart;
- run route/unit tests against a temporary data root;
- optionally test reverse-proxy-like access with a local Caddy/Nginx config or
  SSH tunnel before exposing a remote VM.

The concrete source-checkout smoke runbook is maintained in
[`docs/web-deployment.md`](web-deployment.md), including owner setup, login,
logout, `not_installed` dashboard checks, and foreground runner shutdown.

## Proxmox and multi-VM deployment model

For MVP, run armactl and `armactl-web.service` inside the same VM that runs the
Arma Reforger Dedicated Server. Do not run armactl on the Proxmox host to manage
game servers inside other VMs.

Reasons:

- armactl's current philosophy is local installation and local management of
  the server it owns;
- service control, config writes, log reading, and file uploads are all simpler
  and safer when they operate on the VM-local filesystem and systemd;
- the Proxmox host should remain infrastructure, not a privileged game-server
  control plane;
- running the panel beside the game server keeps each VM isolated if one panel
  is compromised or misconfigured.

Recommended topology:

```text
Proxmox Debian host
  -> VM: reforger-1
       -> Arma Reforger server
       -> armactl
       -> armactl-web on 127.0.0.1:8765 or VM-private IP
  -> VM: reforger-2
       -> Arma Reforger server
       -> armactl
       -> armactl-web on 127.0.0.1:8765 or VM-private IP
  -> LXC/VM: public website / reverse proxy
       -> marketing `website/`
       -> HTTPS routes to selected armactl-web instances
```

Existing operator environment pattern:

- multiple working game VMs can run different armactl versions during the web
  rollout;
- the first deployment should target a paused/maintenance VM before touching
  stable production or rented customer servers;
- the public website/container can list available servers and link to their
  separate management-panel hostnames, but it remains separate from the
  authenticated panel;
- an existing external utility project/VM may provide browser file management,
  noVNC, SFTP, or cross-machine mounts. Treat that as a separate project outside
  armactl, not as a dependency or architectural component of armactl-web. The
  MVP web file manager should still operate against the local VM-safe roots
  documented in this plan. SSHFS or other remote mounts on that external utility
  project should not be treated as automatically safe armactl-web roots.

External access options:

- one subdomain per game server panel, for example
  `server-1.example.com` and `server-2.example.com`;
- one reverse-proxy entrypoint with separate upstreams per VM;
- VPN/private network access for panels that should not be public.

Port conflict rules:

- `armactl-web` should default to `127.0.0.1:8765` inside each VM.
- If the reverse proxy runs outside the game VM, such as on the Proxmox host or
  in a separate website/proxy container, it cannot reach the game VM through
  `127.0.0.1`. In that topology, bind `armactl-web` to a VM-private/LAN address
  or to `0.0.0.0` with firewall rules that allow only the proxy source IP.
- The same web port can be reused across different VMs because each VM has its
  own network namespace and IP address.
- If multiple armactl instances run inside the same VM, each web service needs a
  different local port or a later multi-instance-aware web service.
- Reverse proxy ports `80` and `443` should be owned by the public proxy/LXC/VM,
  not by each game VM.
- Game ports remain separate from the web panel. Typical game/A2S/RCON ports
  are configured in `config.json` and should continue to be checked by
  `ports.py`.
- Direct public binding such as `0.0.0.0:8765` must be explicit and should be
  documented as LAN/VPN-oriented, not the default internet exposure model.

Example reverse proxy mapping:

```text
server-1.example.com:443 -> 10.0.0.11:8765
server-2.example.com:443 -> 10.0.0.12:8765
```

For a Proxmox host-level reverse proxy, keep the host proxy as the public entry
point and route each panel hostname/path to the selected game VM's private
address. Avoid exposing `8765` directly to the internet.

Caddy and Nginx examples, HTTPS-required cookie notes, and troubleshooting for
external proxy/container topologies are documented in
[`docs/web-deployment.md`](web-deployment.md).

Default port inventory:

| Component | Default | Protocol | Source |
|-----------|---------|----------|--------|
| Arma game bind/public port | `2001` | UDP | `templates/config.json.j2` |
| Steam A2S query | `17777` | UDP | `templates/config.json.j2` |
| RCON | `19999` | TCP/UDP handling in UFW | `templates/config.json.j2`, `ports.py` |
| armactl web panel | `8765` | TCP | planned default |
| reverse proxy public HTTP | `80` | TCP | proxy/LXC/VM |
| reverse proxy public HTTPS | `443` | TCP | proxy/LXC/VM |

New game-server installation already creates the Arma defaults through
`templates/config.json.j2`. Web installation should not modify those game
ports. `armactl web init` / `armactl web service install` should choose
`127.0.0.1:8765` by default and validate that the chosen web port:

- is not one of the configured game/A2S/RCON ports for that instance;
- is not one of the shared blocked web ports from `ports.py`, such as SSH,
  default Arma/A2S/RCON, or reverse-proxy ports;
- is not already listening in the same VM;
- is not `80` or `443` unless the operator explicitly knows they are running
  the web app directly without a reverse proxy;
- can be overridden by an explicit web config value or CLI option.

If the default web port is busy inside the same VM, fail with a clear message
and ask the operator to choose another port. Do not silently move to a random
port because the reverse proxy configuration needs a stable upstream.

Future option: a separate fleet controller can aggregate multiple armactl-web
instances later. That controller should talk to per-VM armactl agents/panels via
authenticated HTTP APIs. It should not replace the local per-VM armactl runtime
for MVP.

## Rollout strategy

Web-panel rollout should be incremental:

1. Develop and test locally against a temporary data root.
2. Deploy first to a non-critical or paused game VM.
3. Verify login, dashboard, service status, logs, and file containment.
4. Add reverse-proxy routing for that one VM only.
5. Keep CLI/TUI SSH fallback available.
6. Only after smoke tests pass, repeat for stable production and rented
   customer VMs.

The implementation must tolerate older deployed armactl versions until those VMs
are upgraded. The web panel should fail clearly if a target VM is missing a
required runtime config, service template, or web database.

## Technology stack decision

Keep armactl on Python. Do not rewrite the backend in Rust or C++ for the web
panel.

Reasons:

- the existing CLI, TUI, Telegram bot, config/mod/service managers, and tests
  are already Python;
- the web panel should be a thin UI over those modules, not a second
  implementation of the same server-management logic;
- the risky parts are permissions, path safety, service control, uploads, and
  deployment, not raw CPU performance;
- rewriting in Rust/C++ would add FFI/IPC boundaries and packaging work before
  the product surface is stable.

Rust or C++ can be considered later only for a narrow helper if a specific
performance or privilege-isolation problem proves it needs that treatment.

Recommended web backend stack:

- Python ASGI application;
- FastAPI as the route/API layer;
- Starlette components for middleware, sessions, static files, templates,
  streaming, and testing where useful;
- Uvicorn as the local ASGI server behind systemd;
- Jinja2 server-rendered templates;
- python-multipart for form/file upload handling;
- SQLite for web runtime state;
- Argon2 password hashing via a small local auth wrapper.

Recommended frontend stack for MVP:

- server-rendered HTML templates;
- package-local CSS under `src/armactl/web/static/`;
- htmx for partial page updates and form interactions;
- small vanilla JavaScript only where browser APIs are needed.

Do not start with a React/Vue/Svelte single-page application and do not require
a Node/Vite build pipeline for the MVP. A TypeScript/Vite layer can be added
later if the browser-side state becomes large enough to justify it, for example
for a richer file manager, live log viewer, or multi-instance dashboard.

## Web localization

Reuse the existing armactl localization files and helpers instead of creating a
second translation system for the web panel.

The current TUI language switcher stores one operator-wide language in
`~/armactl-data/user_settings.json` and uses the global `_()` / `tr()` helpers.
That is acceptable for the local TUI, but it is the wrong request model for an
always-on web service with login sessions.

The web panel should use `armactl.i18n.translate_for_lang()` and
`armactl.i18n.tr_for_lang()` through a small web i18n adapter:

- resolve language per request from the authenticated user preference, then
  session or cookie, then `Accept-Language`, then English fallback;
- expose Jinja helpers such as `t()` and `tr()` to templates;
- add a language selector that stores the web preference in `web.db` or a
  web-owned cookie/session value, not the global TUI settings file;
- store appearance preferences, such as light/dark theme, in the same web
  preference model rather than TUI settings or game config;
- keep translation keys in the existing `src/armactl/locales/*.json` files;
- never call `toggle_lang()` or `save_lang()` from normal web request handling;
- keep tests independent from the saved operator UI language, and add web
  template/route coverage once templates are localized.

Web routes may still use backend modules that return already localized
messages, but route and template strings should be localized with the
per-request language. Do not import TUI screens or widgets to reuse labels.

Preference UX should not make cheap choices feel expensive:

- theme switching should update `document.documentElement.dataset.theme`
  immediately in the browser, then persist the preference through the existing
  server route or a small async endpoint;
- theme persistence may remain a web-owned cookie because it is not sensitive
  state;
- language switching may still use a server-rendered page refresh because
  templates and text are rendered through Jinja, but it should avoid forcing
  a slow dashboard status rebuild when only the UI language changed;
- if a language switch returns to a heavy page, use a lightweight redirect,
  short-lived dashboard snapshot cache, or another scoped mechanism that does
  not create a second source of truth for server state;
- keep CSRF/session protection for authenticated preference writes, and keep
  tests independent from any saved operator UI language.

## Security baseline

- No default password.
- First setup creates a web admin credential and a random session secret.
- Store web settings under runtime data, not the repository:

```text
~/armactl-data/web/web.env
```

- Passwords must be stored as modern password hashes, not plaintext.
- Sessions must use HttpOnly, SameSite cookies.
- All mutating requests need CSRF protection.
- Login attempts are rate-limited through web.db using digest-only throttle
  state; raw passwords, IP addresses, usernames, session tokens, and CSRF tokens
  are not stored in the throttle table.
- Unsafe HTTP/external-bind state is visible to operators in CLI/runtime/service
  summaries and the dashboard before remote exposure guidance is considered
  complete.
- Optional IP allowlist and trusted proxy handling are future work; the current
  login throttle intentionally uses `request.client.host` and does not trust
  `X-Forwarded-For`.
- Secrets must be redacted in UI, logs, and API responses.
- Web access to systemd must use the existing narrow privileged helper pattern,
  not broad passwordless sudo.
- The service should bind to localhost by default and require an explicit flag
  or config value for direct external binding.
- The panel shows a clear warning when it is externally bound without
  HTTPS-required cookies, and a softer warning when externally bound behind an
  HTTPS/reverse-proxy/firewall model.
- Add an append-only runtime audit log for mutating web actions:

```text
~/armactl-data/logs/web/audit.log
```

The audit log should include timestamp, user, action, instance, target, and
result. It must not include secrets.

## Logging and observability model

armactl-owned log files should share one central log root while still keeping
separate files/directories by subsystem:

```text
~/armactl-data/logs/
├── host-tests/<instance>/host-tests-YYYYmmdd-HHMMSS.log
├── instances/<instance>/...
└── web/
    ├── audit.log
    └── runtime.log
```

The central root is for logs written by armactl itself. External logs still stay
at their native sources:

- Arma/server service logs stay in systemd journal and are read through
  `armactl logs`, TUI live logs, and future bounded web log views.
- Arma engine telemetry stays in the server profile logs under
  `~/armactl-data/<instance>/config/logs/*/console.log` and is parsed by
  `metrics` for Server FPS/frame-time and operational status. This requires the
  generated start script to include `-logStats 10000`.
- Host/developer checks run through `scripts/run-host-tests`; when launched from
  TUI, their output is saved under `~/armactl-data/logs/host-tests/<instance>/`.
- `armactl report` is the redacted support/debug snapshot. It can include
  discovery, service/timer status, process data, start script details, telemetry
  snippets, and bounded journal sections.
- The web app should write normal service runtime output to the
  `armactl-web.service` journal once the service exists.
- Mutating web actions need a separate append-only audit trail at
  `~/armactl-data/logs/web/audit.log`; this is not a replacement for service
  logs.

The web panel should expose logs in stages: first bounded read-only journal
snippets and diagnostic report preview/copy, later live streaming. It should
not stream unlimited logs by default and must redact secrets before showing
diagnostic output in the browser. The current web slice implements fixed
source `/logs` views and a `/report` preview without arbitrary paths,
downloads, or streaming.

## Accounts and cabinet model

The MVP can start with one local administrator account because the first target
is a self-hosted panel on the operator's own VM.

The first cabinet is scoped to one machine and one local armactl-managed server.
It should not include "add another server", fleet enrollment, remote VM
registration, or cross-machine file access. Those belong to a future fleet or
product-cabinet layer. For now, one deployed `armactl-web` controls the local
VM/server it runs beside.

The design must not block a future product-style cabinet for different users.
If armactl is distributed to multiple operators outside internal use, the web
surface should be able to grow into:

- a user profile page;
- password change and recovery/reset flow;
- multiple users per installation;
- roles such as owner, operator, and read-only viewer;
- per-user audit log attribution;
- optional 2FA;
- product/update/license or support information if the distribution model needs
  it later.

Future multi-server cabinet features should be additive and should call
authenticated per-VM armactl-web instances instead of letting one panel directly
manage other machines' files or systemd units.

Keep this separate from the public marketing `website/`. The cabinet belongs to
the authenticated management panel, not the promotional site.

For the first implementation, prefer storage that can evolve:

```text
~/armactl-data/web/web.db
~/armactl-data/logs/web/audit.log
```

Use SQLite for users, password hashes, roles, sessions, CSRF tokens, and future
job metadata. Keep `logs/web/audit.log` as a human-readable append-only
operational log.

Do not hard-code assumptions that there is only one user across the route
handlers, templates, audit log, or permission checks.

It is acceptable for MVP data models to assume one local managed server per web
installation. Do not hard-code that there will only ever be one web user.

## Commercial feature posture

The current project has no licensing, billing, subscription, entitlement, or
paid-feature code. If armactl later ships to different customers with paid
tiers, keep the product model separate from the core local server-management
logic.

Recommended direction:

- keep the self-hosted core usable without a mandatory external billing
  service;
- store local feature entitlements in `~/armactl-data/web/web.db`;
- make entitlements explicit in route guards and templates instead of hiding
  checks deep inside backend modules;
- audit denied premium actions the same way successful mutating actions are
  audited;
- keep emergency local access to CLI/TUI available even if the web cabinet or
  license state is broken;
- do not gate security-critical basics such as password changes, audit export,
  and disabling the web service.

Potential paid or higher-tier features should be additive, for example:

- multi-user cabinet with granular permission categories;
- team/user audit history and export;
- scheduled task chains such as warning -> backup -> restart;
- full backup/restore UI with retention policies;
- performance recommendations;
- fleet overview across multiple VMs;
- assisted migration/import workflows.

## Filesystem access model

The file manager must be scoped to explicit allowed roots. The first useful
root is the Arma server install directory:

```text
~/armactl-data/<instance>/server/
```

Optional read/download roots can be added later:

```text
~/armactl-data/<instance>/config/
~/armactl-data/<instance>/backups/
~/armactl-data/<instance>/config/logs/
```

Rules:

- Reject absolute paths from the browser.
- Resolve every requested path and require it to stay inside the selected
  allowed root.
- Reject symlink traversal outside the allowed root.
- Reject remote/mounted roots such as SSHFS/NFS/SMB by default unless a future
  advanced admin-only flow explicitly models that risk.
- Uploads must have a size limit.
- Overwrites should be explicit and atomic.
- Config writes should continue to go through `config_manager`, not raw upload
  replacement, unless the user is in a deliberate advanced flow.
- Delete/rename should be added after upload/download/list are proven safe.
- Keep the repository root out of the allowed roots.
- Do not allow upload into `.git`, `.venv`, system unit directories, or the
  armactl source tree.
- Treat archive extraction as out of scope for the first version. Uploading an
  archive as a file is fine; server-side unpacking needs a separate threat
  model.

The current web slices implement the safe browser foundation: fixed root
selection, relative-path directory listing, metadata, bounded redacted text
preview, single-file attachment download, and single-file upload for new
targets under the `server` root without overwrite. Other roots remain
browse/download only for now. They do not implement overwrite, delete, rename,
remote mount support, or archive extraction.

This is not SFTP in the MVP. Because the web panel runs inside the same VM as
the game server, file operations should be implemented as safe local filesystem
operations behind authenticated HTTPS routes. The browser uploads/downloads
files through armactl-web; armactl-web writes only to allowed VM-local roots.

Existing browser file managers, noVNC gateways, SFTPGo instances, or SSHFS
mounts can remain separate external projects. They are useful for emergency
access and manual operations, but they must not expand the default armactl-web
file manager scope.

SFTP/SSH can remain an operator fallback outside armactl. A future fleet
controller may use SSH/SFTP internally to reach remote machines, but that is a
different architecture and should not be part of the first per-VM web panel.

## Backend surface

The project already has an internal Python API surface: reusable backend
modules under `src/armactl/`. The TUI uses these modules directly; it does not
use an HTTP API, and it should not be treated as the API boundary.

The CLI also has `--json-output` for some commands. That is useful for scripts
and diagnostics, but the web panel should not shell out to `armactl
--json-output` for normal in-process behavior. Route handlers should call the
same backend modules that the TUI and Telegram bot already use.

Treat CLI, TUI, Telegram, and web as adapters over the same internal backend
API. None of them should own separate source-of-truth data for server state,
configuration, mods, schedules, or service control. The only web-specific source
of truth should be web runtime data such as users, sessions, CSRF tokens,
roles and jobs in `~/armactl-data/web/`, with audit records under
`~/armactl-data/logs/web/`.

The web backend should be a thin adapter over existing modules:

- Discovery/status: `discovery`, `state`, `status_summary`, `metrics`,
  `player_view`
- Server actions: `service_manager`
- Logs: `logs`, `report`
- Config: `config_manager`
- Mods: `mods_manager`
- Admins: `admins_manager`
- Schedule/timer: `service_manager`
- Telegram bot settings: `bot_config`, `bot_manager`
- Files: new safe filesystem adapter

Avoid importing or calling TUI screens from web code.

Useful existing internal contracts:

- `ServerState.to_dict()` / `ServerState.from_dict()` for persisted discovery
  state.
- `ServiceResult.to_dict()` for service-control outcomes.
- Structured status dictionaries from `get_service_status()` and
  `get_timer_status()`.
- Dataclasses from `metrics`, `player_view`, `status_summary`, `bot_config`,
  `mods_manager`, `addon_cleanup`, and `admins_manager`.
- `run_install()` and `run_repair()` generators for long-running progress
  output.

Before implementing many routes, add a small web-facing facade layer that
groups multi-step workflows into stable functions. That facade should return
plain dataclasses or dictionaries suitable for templates, JSON responses, job
records, and tests. Keep `click`, Textual widgets, and terminal formatting out
of that facade.

Internal API readiness:

| Area | Readiness for web | Needed adapter work |
|------|-------------------|---------------------|
| Read-only status/dashboard | High | Implemented through one dashboard DTO from discovery, service/timer status, metrics, players, config summary, mods, web runtime, and safe bot summary |
| Start/stop/restart | High | Default-instance web controls are implemented with auth, confirmation, CSRF, audit log, and route-level permission checks |
| Config/mods/admins/bot settings | Medium-high | Read-only detail pages are implemented; wrap future mutations with form validation, CSRF, and redacted error rendering |
| Logs/report | Medium-high | Bounded read-only audit, fixed journal, and redacted report preview views are implemented; add streaming/download later without `os.execvp` |
| Install/repair/update | Medium | Run via background jobs; never block a request thread |
| File manager | Medium | Safe adapter, browser foundation, single-file download, and server-root upload-new-file are implemented; overwrite/delete/rename and remote mount support remain future work |
| Web users/roles/entitlements | Low | Implement new `web.db` models; do not reuse game admins as web users |

## Existing feature inventory

The current repo already implements most of the management behavior that the
web panel should expose. The web work should reuse these modules and not copy
logic from TUI screens.

| Product area | Current status | Existing source | Web implication |
|--------------|----------------|-----------------|-----------------|
| Dashboard/status | Implemented in TUI, CLI, and the web read-only dashboard | discovery, state, status_summary, metrics, player_view, ports, bot_config | Keep future routes thin and continue extending the facade instead of route-local aggregation |
| Server controls | Implemented | `service_manager`, CLI `start/stop/restart`, TUI `ManageScreen` | Web start/stop/restart now wraps existing calls for the default instance; schedule and job-backed operations remain future work |
| Logs/report | Implemented for journal/report, TUI live view, and bounded read-only web views | `logs`, `report`, `TailLogScreen` | Web exposes fixed sources and redacted report preview; add browser streaming/download later |
| Config editor | Implemented in structured and raw TUI flows | `config_manager`, `ConfigEditorScreen`, `RawConfigScreen` | Read-only web config page exists; future form writes stay through `config_manager` with validation |
| Mods manager | Implemented beyond basic parity | `mods_manager`, `mods_state`, `addon_cleanup`, `ModManagerScreen` | Read-only web mods page exists; future add/remove/enable/disable/import/export routes should reuse existing modules |
| Server admins | Implemented for Arma `game.admins` | `admins_manager`, `AdminManagerScreen` | Read-only game-admin page exists; keep game admins separate from web users/roles for future management flows |
| Backups/cleanup | Partially implemented | `config_manager` backups, `cleaner`, `CleanupScreen` | Config backups exist; full server backup/restore is future work |
| Schedule | Implemented for restart timer | `service_manager`, `ScheduleScreen`, CLI `schedule` | Web can show/set/enable/disable restart schedule; task chains are future |
| Telegram bot | Implemented | `bot_config`, `bot_manager`, `telegram_bot`, `BotConfigScreen` | Read-only bot status page exists; future config/service flows can reuse the same `.env` and service-manager path |
| File manager | Browsing, single-file download, and server-root upload-new-file implemented | `paths`, new web filesystem adapter | Browser lists fixed local roots, bounded redacted text previews, validated single-file downloads, and no-overwrite uploads to the `server` root; overwrite/delete/rename remain future work |
| Web users/roles | Partially implemented | `web.db` owner user, password hashes, sessions, CSRF primitives, login/logout cookie wiring, and code-level permission categories exist | Add editable roles/permissions only when more roles are introduced |
| Paid features | Not implemented | none | Add explicit entitlement model only if productized |

## Proposed package structure

```text
src/armactl/web/
  __init__.py
  __main__.py
  app.py
  launcher.py
  facade.py
  runtime/
    config.py
    db.py
  jobs/
    models.py
    store.py
    runner.py
  auth/
    users.py
    sessions.py
    csrf.py
    permissions.py
    rate_limit.py
  security/
    exposure.py
  routes/
    auth.py
    dashboard.py
    service.py
    config.py
    mods.py
    schedule.py
    logs.py
    files.py
    users.py
    jobs.py
  services/
    dashboard.py
    service_actions.py
    config_editor.py
    mods.py
    schedule.py
    logs.py
    file_manager.py
    audit.py
    jobs.py
  schemas/
    dashboard.py
    files.py
    users.py
    jobs.py
  templates/
    auth/
    dashboard/
    service/
    config/
    mods/
    schedule/
    logs/
    files/
    users/
  static/
    css/
    js/

templates/
  armactl-web.service.j2

tests/
  test_web_auth.py
  test_web_files.py
  test_web_routes.py
  test_web_service.py
```

Keep the web package modular by responsibility. Routes should stay thin:
authenticate/authorize, validate request data, call a facade/service, then
render a template or return JSON. Application workflows belong in small
domain-focused services or facades, and existing backend modules remain the
source of truth for server behavior. Avoid catch-all modules such as a large
`routes.py`, `files.py`, or `web.py`; if a module starts coordinating unrelated
areas, split it before it grows into a long maintenance file.

Packaging must be updated when web templates/static files are added. The
current `pyproject.toml` includes Python/json files under `src/armactl` and
`templates/**/*.j2`; it does not yet include web HTML/CSS/JS assets.

Prefer a `web` optional dependency group first:

```text
[project.optional-dependencies]
web = [...]
dev = [..., web test dependencies]
```

The repo launcher/bootstrap should learn how to install web dependencies when
the operator enables the web service.

## UI scope for MVP

First screen should be the usable dashboard, not a landing page.

The web panel should provide dashboard parity with the useful information that
already exists in the TUI, then improve the ergonomics for browser use. It is
not a separate feature surface; it is a more convenient remote operator view
over the same server state, controls, logs, config, mods, schedule, Telegram
bot, and maintenance workflows.

MVP views:

- Login
- Dashboard: lifecycle, service/timer state, paths, ports, players, CPU/RAM/disk,
  server FPS telemetry, config/mod summaries, web runtime, and safe Telegram
  summary
- Server controls: start, stop, restart, refresh status
- Logs: latest journal lines, later live streaming
- Config: read-only safe structured summary now; future editable fields plus validation
- Mods: read-only active list now; future add, remove, import/export
- Admins: read-only game admin IDs and local labels now; future management forms
- Bot: read-only Telegram status now; future safe configuration forms
- Schedule: show, set, enable, disable, restart now
- Files: browse allowed server root, upload, download

The interface should be quiet and operational: dense, predictable, and focused
on repeated server management tasks.

The product feel can be inspired by self-hosted server dashboards: a personal
cabinet with quick status, clear sections, and direct management tools
available after login. This is a design direction, not a requirement to copy any
specific existing product.

The dashboard should feel related to the TUI in information architecture:
clear server state first, then actionable controls, then diagnostic details.
The browser version can use more spatial layout, richer tables, inline forms,
file upload/download controls, and better copy/paste affordances than the TUI.

The web UI should have its own templates/static assets under `src/armactl/web/`.
It should not import files from top-level `website/`, and top-level `website/`
should not import or depend on the management panel.

## Visual direction

The management panel can share the same brand language as the marketing site:
logo, product name, typography direction, and a compatible color palette.

It should not copy the marketing site's page structure. The panel is an
operator tool, so it should prioritize fast scanning, dense status information,
clear forms, tables, logs, and predictable navigation. Avoid hero sections,
landing-page copy, decorative layouts, and large promotional imagery inside the
authenticated app.

Recommended approach:

- Share brand cues with `website/`, but keep panel assets package-local.
- Use a dashboard-first layout after login.
- Keep controls compact and explicit.
- Use restrained visual polish for confidence, not decoration.
- Reserve marketing-style pages for the public `website/` only.

Long-term, the public website and per-server panels may be linked through a
single product portal or domain structure. Keep that as a distant post-MVP
integration idea; do not make it a requirement for the first web panel.

## Deployment commands

Add CLI commands:

```text
armactl web init
armactl web run
armactl web service install
armactl web service start
armactl web service stop
armactl web service restart
armactl web service status
armactl web service enable
armactl web service disable
```

`web init` creates runtime config and credentials. `service install` now
installs or refreshes `armactl-web.service`, reloads systemd, and enables the
unit on boot. It does not start the service; use `armactl web service start`
explicitly after review or a smoke check.

The first service implementation follows the current source-checkout deployment
model: the rendered unit runs the repo-local `.venv/bin/python`. If armactl gets
a wheel-only deployment path later, revisit service template/interpreter
discovery instead of assuming the source tree layout.

Add a repo-local smoke launcher plus documented flow:

```text
./scripts/run-web --dev --data-root /tmp/armactl-web-dev
armactl web run --dev --data-root /tmp/armactl-web-dev
```

This lets us test the panel locally before installing the service on a remote
VM. In `--dev` mode the foreground runner uses Uvicorn reload for local Python,
template, and CSS work. Production deployments should use `armactl-web.service`,
not the foreground debug runner.

## Implementation phases

### Phase 1 - Test and web foundation

- Keep tests independent from saved UI language.
- Add web dependencies and package skeleton.
- Add `scripts/run-web` and bootstrap support for local web smoke tests.
- Add a FastAPI app factory without starting Uvicorn inside the app module.
- Add `/healthz`, package-local templates/static, and minimal read-only
  dashboard routes wired to the web facade.
- Add web runtime config loader, runtime init, owner setup, and
  auth/session/CSRF primitives.
- Add login/logout routes and cookie wiring before exposing mutating web flows.
  Future mutating flows must reuse the established auth/session/CSRF helpers.
- Web i18n/theme preferences are implemented with request-scoped translation
  helpers, existing locale JSON files, and web-owned cookies.
- Fast preferences UX remains future work: theme changes should be instant in
  the browser and language changes should avoid needless heavy dashboard
  status rebuilds.
- The always-on `armactl-web.service` template and `armactl web service ...`
  commands are implemented for production service installation and lifecycle
  management.
- Update packaging so web templates/static files are included in editable,
  wheel, and sdist installs.
- Keep the marketing `website/` untouched and separate from the management UI.

### Phase 2 - Safe read-only dashboard

- Status endpoint and dashboard.
- Metrics and player view.
- Read-only config, mods, game admins, and Telegram bot detail pages are implemented through the web facade.
- Read-only logs/report views are implemented with fixed sources, bounded output, and redaction.
- No mutating actions in this phase except login/logout.

### Phase 3 - Controlled server actions

- Start/stop/restart via existing `service_manager` for the current default instance is implemented.
- Schedule show/set/enable/disable.
- Audit log for mutating actions.
- Add confirmation UI for stop/restart and other disruptive operations.

### Phase 3.5 - Background jobs

- A small `web_jobs` metadata model now exists in `web.db` for web-runtime job
  status, progress, timestamps, bounded redacted stdout/stderr tails, and safe
  result/error metadata.
- A pluggable runner/dispatcher foundation now runs only explicitly registered
  safe handlers by job kind; unknown kinds and handler failures become
  controlled failed jobs.
- A read-only authenticated `/jobs` page lists recent jobs and bounded output
  tails for operators with `jobs:view`.
- Use the job model for future install, repair, SteamCMD update, and large file
  actions.
- Until explicit handlers and routes exist for those operations, keep
  install/repair/update out of web.

### Phase 4 - Config, mods, admins, and bot editing

- Structured config editor through `config_manager`.
- Mods add/remove/import/export through `mods_manager`.
- Game admin management through `admins_manager`, kept separate from web users.
- Telegram bot configuration through `bot_config` without exposing token values.
- Validation errors rendered in UI.

### Phase 5 - Filesystem manager

- Safe allowed-root browser foundation is implemented.
- Download single file is implemented through the same safe relative-path adapter.
- Upload one new file to a selected safe directory under the `server` root is implemented without overwrite.
- Atomic overwrite with explicit confirmation remains future work.
- Tests for path traversal, symlinks, overwrite, and size limits.

### Phase 6 - External deployment docs

- Service commands for `armactl-web.service` are implemented; document operational deployment use.
- Document Caddy/Nginx reverse proxy.
- Document LAN/VPN direct bind option.
- Document firewall ports and service restart/update flow.
- Add VM smoke checklist.
- Document how to rotate/reset the web admin password.
- Document backup/restore of `~/armactl-data/web/`.
- Document how to disable the web service while keeping CLI/TUI available.

## Local test plan

- Unit-test auth, config loading, CSRF, and filesystem path handling.
- Route-test the dashboard and API with a temporary data root.
- Mock `service_manager` for start/stop/restart route tests.
- Run browser smoke tests locally against `127.0.0.1` once the first UI exists.
- Keep tests independent from saved runtime language and user settings.
- Do not require a live Arma server for normal CI/local unit tests.
- Keep live VM checks as manual smoke tests.

## Open decisions

- External access default: localhost plus reverse proxy should be the default;
  direct bind should be explicit.
- Whether install/repair flows belong in web v1 or should stay CLI/TUI until a
  background job runner exists.
