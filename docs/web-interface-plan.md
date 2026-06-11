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
~/armactl-data/web/                 # web panel runtime settings and audit log
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
- run route/unit tests against a temporary data root;
- optionally test reverse-proxy-like access with a local Caddy/Nginx config or
  SSH tunnel before exposing a remote VM.

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
- Login attempts should be rate-limited.
- Secrets must be redacted in UI, logs, and API responses.
- Web access to systemd must use the existing narrow privileged helper pattern,
  not broad passwordless sudo.
- The service should bind to localhost by default and require an explicit flag
  or config value for direct external binding.
- The panel should show a clear warning when it runs without HTTPS because the
  operator is about to expose service control and file upload over the network.
- Add an append-only runtime audit log for mutating web actions:

```text
~/armactl-data/web/audit.log
```

The audit log should include timestamp, user, action, instance, target, and
result. It must not include secrets.

## Accounts and cabinet model

The MVP can start with one local administrator account because the first target
is a self-hosted panel on the operator's own VM.

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

Keep this separate from the public marketing `website/`. The cabinet belongs to
the authenticated management panel, not the promotional site.

For the first implementation, prefer storage that can evolve:

```text
~/armactl-data/web/web.db
~/armactl-data/web/audit.log
```

Use SQLite for users, password hashes, roles, sessions, CSRF tokens, and future
job metadata. Keep `audit.log` as a human-readable append-only operational log.

Do not hard-code assumptions that there is only one user across the route
handlers, templates, audit log, or permission checks.

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

This is not SFTP in the MVP. Because the web panel runs inside the same VM as
the game server, file operations should be implemented as safe local filesystem
operations behind authenticated HTTPS routes. The browser uploads/downloads
files through armactl-web; armactl-web writes only to allowed VM-local roots.

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
| Read-only status/dashboard | High | Compose one dashboard DTO from discovery, service status, metrics, players, config summary, and mods summary |
| Start/stop/restart | High | Add auth, confirmation, CSRF, audit log, and route-level permission checks |
| Config/mods/admins/bot settings | Medium-high | Wrap existing functions with form validation and redacted error rendering |
| Logs/report | Medium | Use bounded reads first; add streaming later without `os.execvp` |
| Install/repair/update | Medium | Run via background jobs; never block a request thread |
| File manager | Low | Implement a new safe filesystem adapter first |
| Web users/roles/entitlements | Low | Implement new `web.db` models; do not reuse game admins as web users |

## Existing feature inventory

The current repo already implements most of the management behavior that the
web panel should expose. The web work should reuse these modules and not copy
logic from TUI screens.

| Product area | Current status | Existing source | Web implication |
|--------------|----------------|-----------------|-----------------|
| Dashboard/status | Implemented in TUI and CLI | `discovery`, `state`, `status_summary`, `metrics`, `player_view`, `ports` | Build read-only dashboard first from existing functions |
| Server controls | Implemented | `service_manager`, CLI `start/stop/restart`, TUI `ManageScreen` | Add web confirmations and audit entries around existing calls |
| Logs/report | Implemented for journal/report and TUI live view | `logs`, `report`, `TailLogScreen` | Start with latest log lines; add browser streaming later |
| Config editor | Implemented in structured and raw TUI flows | `config_manager`, `ConfigEditorScreen`, `RawConfigScreen` | Use form views plus validation; config writes stay through `config_manager` |
| Mods manager | Implemented beyond basic parity | `mods_manager`, `mods_state`, `addon_cleanup`, `ModManagerScreen` | Expose list/add/remove/enable/disable/import/export through routes |
| Server admins | Implemented for Arma `game.admins` | `admins_manager`, `AdminManagerScreen` | Keep separate from web users/roles; expose as server-admin management |
| Backups/cleanup | Partially implemented | `config_manager` backups, `cleaner`, `CleanupScreen` | Config backups exist; full server backup/restore is future work |
| Schedule | Implemented for restart timer | `service_manager`, `ScheduleScreen`, CLI `schedule` | Web can show/set/enable/disable restart schedule; task chains are future |
| Telegram bot | Implemented | `bot_config`, `bot_manager`, `telegram_bot`, `BotConfigScreen` | Web can reuse the same `.env` and service-manager flow |
| File manager | Not implemented | only path-safety patterns in `paths`, `cleaner`, `addon_cleanup` | Add a new safe filesystem adapter before exposing upload/download |
| Web users/roles | Not implemented | only server admins and Telegram allowlist exist | Add `web.db` users, roles, sessions, CSRF, and permissions |
| Paid features | Not implemented | none | Add explicit entitlement model only if productized |

## Proposed package structure

```text
src/armactl/web/
  __init__.py
  __main__.py
  app.py
  auth.py
  config.py
  csrf.py
  db.py
  files.py
  jobs.py
  routes.py
  schemas.py
  templates/
  static/

templates/
  armactl-web.service.j2

tests/
  test_web_auth.py
  test_web_files.py
  test_web_routes.py
  test_web_service.py
```

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
- Dashboard: running state, ports, players, CPU/RAM, server FPS telemetry
- Server controls: start, stop, restart, refresh status
- Logs: latest journal lines, later live streaming
- Config: safe structured fields plus validation
- Mods: list, add, remove, import/export
- Schedule: show, set, enable, disable, restart now
- Files: browse allowed server root, upload, download

The interface should be quiet and operational: dense, predictable, and focused
on repeated server management tasks.

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

## Deployment commands

Add CLI commands:

```text
armactl web init
armactl web run
armactl web service install
armactl web service start
armactl web service stop
armactl web service status
```

`web init` should create runtime config and credentials. `service install`
should install or refresh `armactl-web.service`.

Add a repo-local smoke launcher plus documented flow:

```text
./scripts/run-web --dev --data-root /tmp/armactl-web-dev
armactl web run --dev --data-root /tmp/armactl-web-dev
```

This lets us test the panel locally before installing the service on a remote
VM. Production deployments should use `armactl-web.service`, not the foreground
debug runner.

## Implementation phases

### Phase 1 - Test and web foundation

- Keep tests independent from saved UI language.
- Add web dependencies and package skeleton.
- Add `scripts/run-web` and bootstrap support for local web smoke tests.
- Add web runtime config loader.
- Add auth/session/CSRF primitives.
- Add service template for always-on `armactl-web.service`.
- Update packaging so web templates/static files are included in editable,
  wheel, and sdist installs.
- Keep the marketing `website/` untouched and separate from the management UI.

### Phase 2 - Safe read-only dashboard

- Status endpoint and dashboard.
- Metrics and player view.
- Read-only logs.
- No mutating actions yet except login/logout.

### Phase 3 - Controlled server actions

- Start/stop/restart via existing `service_manager`.
- Schedule show/set/enable/disable.
- Audit log for mutating actions.
- Add confirmation UI for stop/restart and other disruptive operations.

### Phase 3.5 - Background jobs

- Add a small job model before long-running operations are exposed in web.
- Jobs should track progress, status, stdout/stderr tail, and final result.
- Use it for future install, repair, SteamCMD update, and large file actions.
- Until this exists, keep install/repair/update out of web.

### Phase 4 - Config and mods

- Structured config editor through `config_manager`.
- Mods list/add/remove/import/export through `mods_manager`.
- Validation errors rendered in UI.

### Phase 5 - Filesystem manager

- Safe allowed-root browser.
- Download single file.
- Upload file to selected directory.
- Atomic overwrite with explicit confirmation.
- Tests for path traversal, symlinks, overwrite, and size limits.

### Phase 6 - External deployment docs

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
