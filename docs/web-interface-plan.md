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
- Do not make `/files` the primary way to edit `config.json`. Normal config
  changes belong in structured `/config` controls.
- Do not add Windows service/log/process support to the Linux/systemd-first web
  MVP.
- Do not run game-server installation, repair, or update flows as long blocking
  HTTP requests.
- Do not update the game server automatically by default.
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

## Game server schedule and boot policy

The web panel autostart policy and the game server autostart policy are
separate.

armactl-web.service should be enabled after install so the browser panel comes
back after VM boot and waits for operator connections. This does not guarantee
that armareforger.service also starts after VM boot.

The existing Arma restart timer controls scheduled restarts. A timer that only
uses calendar entries such as 06:00 and 18:00 plus Persistent=true catches up
missed scheduled events after downtime, but it does not mean start the game
server immediately after every VM boot. If armareforger.service is disabled and
the VM reboots at an arbitrary time, the server may stay stopped until the next
timer event or manual start.

The web panel should make this explicit:

- show service enabled/disabled and timer enabled/disabled separately;
- show the next scheduled restart time;
- warn when the game service is disabled and no boot-start policy is active;
- provide schedule controls through the same backend used by CLI/TUI;
- add a separate setting for start game server after VM boot if operators need
  immediate boot recovery.

Implemented web slice: `/schedule` shows restart timer installed/active/enabled
state, OnCalendar values, next/last run when available, and
`armareforger.service` autostart policy. Mutating controls use authenticated
POST + CSRF + schedule permissions and audit timer set/enable/disable,
restart-now, and game-service autostart enable/disable through
`platform/service_adapter.py` without shelling out to the CLI. The default
adapter is the existing Linux/systemd `service_manager` backend, so current
unit names and behavior remain unchanged.

Future `/schedule` UI must be timezone-explicit. The user sees and enters restart
schedule times in the browser's local timezone, and the frontend sends an IANA
timezone name such as `Europe/Kyiv`, `Europe/Paris`, or `America/New_York` with
the submitted times. The backend validates the timezone name, rejects bare times
without timezone context, and normalizes the schedule to UTC before storing it
or rendering systemd/backend `OnCalendar` values. Offset-only values are not
enough because daylight saving rules require an IANA timezone.

The backend/systemd source of truth remains UTC. Example: user input `18:00
Europe/Kyiv` normalizes to backend/systemd `15:00 UTC`, and the UI renders
`18:00 Europe/Kyiv / 15:00 UTC`. If browser timezone detection is unavailable,
fall back to UTC with a visible warning. Next-run and last-run displays must label their timezone
explicitly. Schedule audit records should store the user-local input, submitted
timezone, and normalized UTC schedule. Existing systemd `OnCalendar` values are
treated as UTC unless a later migration can prove a different timezone source.

Service/timer backend boundary:

- web service start/stop/restart actions and schedule mutations depend on the
  `ServiceAdapter` protocol;
- the default adapter is `LinuxSystemdServiceAdapter`, a thin wrapper over the
  existing `service_manager`;
- CLI/TUI direct `service_manager` calls remain compatibility paths for now;
- a Windows backend is a future adapter implementation, not part of the MVP.

Saved config, game-admin, and mod changes should create web-runtime
pending operator work when they actually change server state. Pending work is a
separate concept from background jobs: it is stored outside `web_jobs`, stacks by
category (`config`, `admins`, `mods`, and future `schedule` work), links back to
the source page, keeps only UI-safe redacted details, and records actor,
timestamp, instance, and the resolution action (`restart game server`). Repeated
saves in the same category may update that category, but they must not erase
other categories. The dashboard shows one compact "Pending operator work"
table with source links and a single "View all work" link, and it does not
render a large empty background-jobs panel next to pending work. `/jobs` is
the detailed operations page: it shows pending operator work as dense rows
separate from background jobs, with precise empty states for each section.
Successful web-triggered game-server restart clears only restart-related
pending work, including legacy pending-restart rows migrated from the older
single-marker store. The restart result page uses operator-friendly text
such as "Server restart completed." and "Pending restart work cleared.",
with safe diagnostics secondary to the main message.

UI follow-up: `/schedule` is accepted as functional, but the page should be
revisited after the core management flows are complete. The timer schedule,
timer enable/disable, game-service autostart, and restart-now controls should be
visually separated into calmer operator sections, with destructive actions kept
away from routine schedule editing.

Future host controls should be separate from game-server controls. VM reboot or
shutdown can be useful for remote operators, but they should be owner/admin
only, require strong confirmation, write audit records, and remain outside the
normal server start/stop/restart controls.

## Diagnostics command palette and terminal boundary

The MVP should not expose an arbitrary shell in the browser. A browser terminal
is effectively SSH with a larger attack surface: command injection, accidental
destructive commands, sudo/root escalation, transcript leakage, and confused
operator context are all easy failure modes.

The safer first step is a diagnostics command palette with allowlisted actions:

- `armactl status` equivalent through backend APIs;
- game service status and web service status;
- restart timer status and next-run checks;
- port checks;
- config validation;
- bounded recent game/web/bot logs;
- bounded redacted diagnostic report collection;
- refresh discovery/state;
- restart the web panel only through an explicit audited service action.

These actions should run through explicit handlers, preferably the web job
model when output may be slow. They must use permissions, CSRF, audit logging,
bounded output, and redaction. They should not accept arbitrary command text,
shell fragments, pipes, redirects, sudo, or root-level input.

A full web terminal can be considered later only as a disabled-by-default
break-glass feature for a very small operator set, for example the technical
architect and the hardware owner. It should require all of the following:
HTTPS, trusted proxy handling, IP allowlist for the operators' public IPs,
explicit `terminal:use` permission, extra re-auth before opening a session,
short-lived sessions, transcript audit/redaction, and clear UI separation from
normal server management. Any sudo/root use through the browser must be an
explicit operator-provisioned decision, never a default armactl installation
behavior.

## Settings information architecture

The dashboard should stay a compact operational overview, not a dumping ground
for every editable setting. Split settings by operator intent and risk:

- Dashboard: short server state, important warnings, pending work summary, and
  links to detailed pages only.
- Basic server config (`/config`): safe common fields such as name, scenario,
  player count, visibility, BattlEye, and distances.
- Mods: active/disabled mod list, add/update/disable/delete controls, ordering,
  bulk paste/import/export, and future Workshop/catalog helpers.
- Mod settings: separate pages or subpages for per-mod runtime configuration,
  such as ServerAdminTools. Dashboard should show only a short neutral/alert
  summary and link to details.
- ServerAdminTools specifically belongs to future mod runtime settings and
  diagnostics pages. The dashboard must not show a persistent warning/notice
  just because the SAT config is absent; absence is normal when the mod is not
  installed or not configured. Dashboard should surface SAT only when a real
  guard/health problem exists, such as invalid JSON, default/example-only
  admins, or a configured SAT mod whose runtime config is missing.
- Network and advanced server settings: game port, A2S, RCON, passwords, and
  other sensitive settings that need stronger validation, explanations, and
  restart warnings than the basic config form.
- Diagnostics: SAT/config/ports/paths/telemetry/log health checks and controlled
  troubleshooting actions.
- Advanced / danger zone: owner/admin-only raw JSON editor exposed as a
  deliberate mode inside `/config`, backup restore, destructive file actions,
  and other break-glass workflows that need explicit confirmation, audit
  logging, and clear rollback/recovery notes.

Config editor expansion should start with a schema inventory, not guessed form
fields. Before adding new controls, list the supported `config.json` fields from
the current config/backend schema and official Arma Reforger documentation, then
map each field to a UI group: Basic, Gameplay, Visibility/Crossplay,
Network/A2S/RCON, Security, Advanced, or Danger Zone. For each field, decide
whether it is safe, dangerous, secret, runtime/mod-specific, or break-glass raw
JSON work.

After that inventory, explicitly evaluate safe UI controls for third-person view
and crossplay/platform settings. Do not add these fields until the exact Arma
Reforger config keys, value shapes, defaults, restart behavior, and backend
validation are verified against the real schema/backend or official docs. If
they are safe, non-secret server config fields, expose them through structured
`/config` controls, not through `/files`. Raw JSON editing remains a separate
owner/mega-eligible break-glass flow inside `/config`.

Form controls should match the data shape: booleans become toggles or
checkboxes, supported platform/crossplay lists become checkbox groups or
segmented controls, and numeric fields stay numeric inputs with bounds and
validation. Secrets, RCON/admin passwords, tokens, and other sensitive fields
must not be exposed casually on normal config pages.

Emergency raw config editing is a separate break-glass flow, not the normal
config workflow. If added, it must be an owner/admin-only button or mode inside
`/config`, never in `/files`, and require an explicit permission, CSRF, double
confirmation, JSON validation, backup before save, audit logging, redacted
errors, and clear restart-required or pending-work behavior.

ServerAdminTools runtime config belongs to future `Mod Settings` or
`Diagnostics`, not dashboard noise. The dashboard should show SAT only for real
health or guard problems; neutral absence is normal. SAT admin guard remains a
backend/startup protection. Any future SAT admins, gameMasters, bans, or other
runtime config edits must be narrow field edits with backup and audit, not a
full overwrite of unrelated SAT settings.

Do not add every new setting to `/config` or `/dashboard`. Prefer focused pages
with thin routes and service-layer validation so dangerous settings do not make
routine operation harder.

## Notifications and pending operator work

The web UI should avoid pushing important operator messages to the top of long
pages when the operator is working lower on the page. Current inline result
panels are functional, but they should be upgraded in a focused UI slice:

- show save/action results as short-lived floating toast notifications near the
  side of the viewport, with accessible text and no secret payloads;
- keep destructive or restart-required warnings persistent until resolved;
- add a notification icon/indicator in the top bar for pending operator work
  such as saved config/admin/mod changes that still require restart;
- add a persistent informational notification when a scheduled game-server
  restart actually happened, backed by safe proof from an allowlisted systemd
  timer/service journal line or parsed audit/source metadata;
- keep the web-runtime pending-work store as the source of truth for
  restart-required operator work, not duplicated fake jobs;
- make notification entries link back to the relevant page/section, for
  example config, admins, mods, schedule, files, logs, or jobs;
- ensure all notifications come from server-side state or explicit safe client
  events, not from trusting query parameters alone.

This should be implemented after the core management actions are in place so
the UI can cover config, admins, mods, schedule, files, jobs, and logs
consistently instead of adding one-off messages per page.

## File manager write operations

The first file-manager slices intentionally added safe browsing, preview,
single-file download, and upload of one new file. Delete, overwrite, rename,
directory operations, archive extraction, and restore flows remain future work
because they can destroy server state or become filesystem escape primitives.

`/files` remains for safe browsing, download, upload, and future bounded delete
flows. It must not become a general raw config editor. Text preview is for
inspection, not the primary `config.json` workflow. If future text-file editing
is added, it must be separate from `/config`, limited by root/path/extension and
size, and include backup plus audit logging.

The next practical file slice should be single-file delete for cleanup of
operator-owned artifacts such as old backups and logs. Requirements:

- allow only fixed web file roots and relative paths already enforced by the
  filesystem adapter;
- reject absolute paths, traversal, `.git`, `.venv`, symlink escapes, source
  tree paths, system paths, unavailable roots, and directories;
- require authenticated POST, `files:write`, CSRF, and explicit confirmation;
- do not delete directories or recursively remove anything in the first delete
  slice;
- write a JSONL audit event with user, root, relative path, size if known,
  success/failure, and no secret file contents;
- show the result through the future notification system or a local anchored
  result panel without jumping the operator to the top of the page;
- keep a clear recovery story: backups that are deleted through web are gone,
  so the UI must make that explicit before confirmation.

Upload threat model:

- uploaded files may be malicious by content, so armactl must never execute,
  source, import, or unpack uploaded files automatically;
- uploads should remain limited to allowlisted roots and conservative file
  sizes;
- filenames must stay sanitized and must not overwrite existing files unless a
  later overwrite flow adds explicit confirmation and audit;
- preview must remain bounded and redacted;
- archive extraction must be treated as a separate high-risk feature with zip
  slip protection, file count/size limits, symlink rejection, and audit;
- if future scanning is added, it should be best-effort defense-in-depth, not a
  replacement for path, size, permission, and no-execute rules.

## Logs UI polish

The current web logs/report views are read-only, bounded, allowlisted, and good
enough for operator use. Future polish should stay read-only unless explicitly
scoped otherwise:

- render audit-log JSONL as readable rows or pretty JSON instead of one dense
  raw line per event;
- add download/export for the selected bounded log/report view;
- add optional live follow or auto-refresh with clear pause/refresh controls;
- add client/server-side filters for level, source, and free-text search;
- highlight important levels such as `ERROR`, `WARNING`, and failed web audit
  actions;
- keep arbitrary filesystem paths out of the logs UI and use only fixed
  allowlisted sources;
- reuse the planned floating notification system for refresh/export results so
  the page does not jump.

## Security review gate for premium and break-glass features

Premium, mega, diagnostics command palette, terminal, IP allowlist management,
host controls, raw config editor, banlist management, file delete, SAT runtime
edits, and any paid entitlement checks must pass a separate security review
before implementation is considered production-ready. Treat this as a release
gate, not a nice-to-have.

The review must cover:

- **Trusted proxy and client IP handling:** never trust `X-Forwarded-For` or
  similar headers unless the immediate proxy is configured as trusted. IP
  allowlists must evaluate the real client IP from a validated proxy chain.
- **IP allowlist bypass risks:** allowlist checks must happen server-side on
  every protected route/action, not only in navigation or templates. Denied
  allowlist decisions should be audited without logging unnecessary personal
  data.
- **Role, permission, and tier confusion:** product tiers such as `premium` or
  `mega` must not silently imply dangerous low-level permissions. Routes should
  check explicit permissions such as `terminal:use`, `host:manage`,
  `allowlist:manage`, or `diagnostics:run`.
- **Mega/platform-owner account takeover:** platform-owner flows should require
  strong passwords, rate limiting, extra re-auth for sensitive actions, and a
  path to add 2FA before broad external distribution.
- **Backend enforcement:** hiding a button in the UI is never enough. Every
  route, JSON endpoint, job enqueue path, and action handler must enforce auth,
  permission, CSRF, tier/policy requirements, and audit behavior.
- **Dangerous feature gates:** raw config editor, host controls, diagnostics
  command palette, terminal, banlist management, file delete, and SAT runtime
  edits need explicit permissions and audit records. Product tiers can map to
  those permissions, but a tier name alone must not authorize the action. Add
  the permission to `auth/permissions.py`, enforce it in the route/service, and
  only then expose the UI control.

  Baseline permission split for future work:

  | Feature | Minimum permission | Extra gates |
  |---------|--------------------|-------------|
  | Structured config edit | `settings:manage` | allowlisted fields only, backup, audit, pending work |
  | Advanced config fields, including ports, RCON/A2S, crossplay, platform policy, third-person, and security toggles | `settings:advanced` | schema inventory, safe widgets, validation, redacted errors, explicit restart warning |
  | Emergency raw JSON config editor | `config:raw_edit` | owner/mega eligibility, double confirmation, JSON validation, backup before save, audit, pending work, secrets redacted |
  | File text edit/upload | `files:write` plus `files:edit` when editing existing files | allowlisted roots, extension/size/path checks, backup/audit, no primary config editing through `/files` |
  | File delete/overwrite/rename/archive extraction | `files:delete` or operation-specific permission | files only unless separately designed, CSRF, confirmation, traversal/symlink checks, audit, backup/restore path when practical |
  | Ban/unban | `bans:manage` | reliable identity only, reason/actor/timestamp, optional expiry, confirmation, audit, backup/rollback for file-backed state |
  | SAT and other mod runtime settings | `mods:settings` or `sat:manage` | narrow field updates, preserve unrelated config, backup, audit, diagnostics link |
  | Diagnostics command palette | `diagnostics:run` | registered handlers only, bounded/redacted output, no arbitrary shell strings |
  | Server update | `server:update` or `jobs:update` | explicit feature/policy gate if productized, background job only, operator confirmation, audit/progress logs, dedupe, no automatic update by default |
  | Break-glass terminal | `terminal:use` | disabled by default, owner/mega eligibility, IP allowlist/trusted proxy, extra re-auth, transcript/audit, no broad sudo |
  | Host reboot/shutdown | `host:manage` | owner/mega eligibility, IP allowlist/trusted proxy, double confirmation, audit, never scheduled by default |
  | Web user/role/tier/allowlist administration | `users:manage` and/or `allowlist:manage` | extra re-auth, audit, recovery path via SSH/local CLI |
- **Command execution:** diagnostics must use registered allowlisted handlers.
  Do not accept arbitrary shell strings, pipes, redirects, environment
  injection, sudo prompts, or root shell input.
- **Terminal transcripts and redaction:** if a break-glass terminal is ever
  enabled, transcripts and job output must redact tokens, cookies, passwords,
  session IDs, CSRF values, `.env` values, and common secret-looking strings.
  Operators must see that a transcript/audit trail exists.
- **Privilege escalation:** no broad passwordless sudo. Reuse narrow privileged
  helper patterns, explicit service actions, or operator-provisioned host
  policies. Root-level browser access must never be installed by default.
- **Audit integrity:** audit logs are operational evidence, not cryptographic
  proof. Mutating and denied sensitive actions should be append-only where
  practical, include user/action/target/result, and avoid secrets. Future
  exports can add signing or remote shipping if needed.
- **Dangerous file operations:** overwrite, delete, rename, archive extraction,
  raw JSON editing, and restore flows need explicit confirmation, backups when
  applicable, traversal/symlink checks, bounded file sizes, and audit records.
- **Host/VM controls:** VM reboot/shutdown must be separate from game server
  restart, owner/platform-only, double-confirmed, audited, and never scheduled
  by default for normal player-facing operation.
- **External exposure:** direct `0.0.0.0` HTTP binding is acceptable for short
  tests only. Production should use HTTPS, reverse proxy or VPN/firewall
  controls, secure cookies, and visible exposure warnings.
- **Fail-safe entitlement behavior:** license/tier failures must not block
  security basics such as password changes, disabling the web service, audit
  export, or local CLI/TUI recovery.
- **Recovery path:** if web auth, tiers, allowlists, or terminal policy are
  misconfigured, an operator with SSH/local shell access must still be able to
  recover through documented CLI/TUI/runtime-file steps.

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
| armactl web panel | `8765` | TCP | current default |
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

Add a dev/test-only pseudolocalization mode as a UI QA tool, not as a production
language. It should transform existing localized strings into longer,
visibly-marked text so layout and missing-key problems are obvious during
manual and automated checks. Requirements:

- do not store `pseudo` as a normal production user language unless an explicit
  development/test flag enables it;
- derive pseudo strings from the existing locale keys instead of adding a third
  hand-maintained locale file;
- expand text length enough to catch button/card/table overflow;
- keep placeholders and `tr()` interpolation safe and intact;
- use it to find raw hardcoded template text, missing keys, and layout overflow
  before polishing web pages.

Preference UX should not make cheap choices feel expensive:

- theme switching updates `document.documentElement.dataset.theme` immediately
  in the browser, then persists the preference through the existing server
  route using a lightweight JSON response instead of redirecting;
- theme persistence remains a web-owned cookie because it is not sensitive
  state;
- language switching still uses a server-rendered page refresh because
  templates and text are rendered through Jinja, but the preference write can
  complete through the same lightweight JSON response before one reload;
- this avoids an extra dashboard status rebuild and does not introduce a
  cache or second source of truth for server state;
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
- Browser terminal access is future break-glass functionality, not MVP. Prefer
  an allowlisted diagnostics command palette first. If a full terminal is ever
  enabled, it must be disabled by default and gated by HTTPS, trusted proxy/IP
  allowlist, extra auth, explicit permission, short sessions, and audit.
- Secrets must be redacted in UI, logs, and API responses.
- Premium/mega, diagnostics, terminal, IP allowlist management, host controls,
  raw JSON editing, and dangerous file operations must pass the dedicated
  security review gate before production use. Route-level checks and tests are
  required; UI-only hiding is not sufficient.
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
- roles such as platform owner, server admin, operator, and read-only viewer;
- optional product tiers such as basic, plus, premium, and mega;
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

## Web users, roles, tiers, and recovery foundation

This foundation must be designed before paid features, browser terminal,
host reboot/shutdown, raw config editing, destructive file actions,
ServerAdminTools/mod runtime settings, premium/mega tools, or user/allowlist
administration become production features. These concerns belong in central
services and policy modules, not in route handlers or templates.

### Identity separation

Keep three identity domains separate:

- **Web users** log into armactl-web and operate the dashboard/system. They own
  sessions, passwords, roles, permissions, preferences, audit attribution, and
  future 2FA/passkeys/device trust.
- **Arma/game admins** are entries in the game server configuration or supported
  game/mod admin backends. They control in-game permissions and must keep using
  `admins_manager` or future game-admin adapters.
- **Player registry records** describe players observed on the managed Arma
  server. They live under the game instance, track reliable player IDs and
  nicknames, and must not become web login accounts.

Do not merge these into one table or model. A person can appear in more than
one domain, but links between domains must be explicit references, not implicit
nickname, e-mail, SteamID, or role-name guesses.

### System admin area

Future multi-user administration needs a dedicated system area such as
`/system/users` or `/users`. It should support:

- creating web users;
- assigning and changing roles;
- changing or resetting passwords;
- disabling and re-enabling users;
- resetting sessions for one user or all users;
- viewing audit history for user, role, password reset, disable/enable,
  session reset, tier, entitlement, and allowlist changes;
- preventing deletion, disablement, or demotion of the last owner-level user;
- first-owner bootstrap only from local CLI/SSH, never from an unauthenticated
  public web route;
- an official recovery flow for broken web auth, policy, or allowlist state.

All user-management mutations require auth, a named permission such as
`users:manage`, POST + CSRF, extra confirmation for high-impact actions, and
audit records that include intent and outcome without logging secrets.

### Roles, permissions, and product tiers

Treat these as separate layers:

- **Roles** are operational bundles for web users, for example `owner`/`deus`,
  `mega`, `operator`, and `viewer`. If `mega` is also used as a product tier,
  use unambiguous internal names such as `mega_operator` for the role and
  `mega` for the tier.
- **Permissions** are concrete capabilities, for example `users:manage`,
  `server:restart`, `server:update`, `mods:manage`, `settings:manage`,
  `files:delete`, `terminal:run` or `terminal:use`, and `host:reboot` or
  `host:manage`. Pick canonical permission names before implementation and
  enforce them consistently.
- **Product tiers/entitlements** are commercial or support bundles, for example
  `basic`, `plus`, `premium`, and `mega`. They can make a feature eligible, but
  they are not the same thing as authorization.

Rules:

- a tier is not a permission;
- a permission is not a billing plan;
- `premium` must never silently mean admin;
- `mega` should be privileged eligibility, not a hidden bypass;
- routes and templates must not decide tier logic themselves;
- a central policy/feature-gate service should resolve user, role,
  permission, entitlement, IP/device policy, and feature availability for each
  protected action;
- denied policy decisions for sensitive or paid actions should be audited with
  minimal personal data.

### Dangerous feature policy

These future features need the strict policy path before implementation:

- diagnostics command palette and any browser terminal;
- host reboot/shutdown;
- emergency raw JSON config editor;
- file delete, edit, overwrite, rename, restore, or archive extraction;
- ServerAdminTools and other mod runtime settings;
- user, role, tier, entitlement, and session management;
- IP allowlist and trusted-proxy management;
- paid/premium/mega tools.

Baseline requirements:

- owner/mega-level role eligibility or an explicit permission grant resolved by
  the central policy service;
- named permission check in the route/service path;
- POST + CSRF for every mutation;
- audit intent and outcome, including denied attempts where useful;
- clear operator confirmation for destructive or disruptive actions;
- optional re-authentication, passkey, or 2FA step for high-impact actions;
- optional IP/VPN/trusted-device gate for the highest-risk actions;
- no silent bypass in templates, route-local conditionals, job enqueue paths,
  or background workers.

If the existing module structure makes this difficult, add the missing module
boundary first. Do not patch around it with route/template-specific checks.

### IP allowlist model

IP allowlists are defense in depth, not the primary authorization system. A
valid IP does not replace login, session, permission, CSRF, confirmation, or
audit checks.

Design constraints:

- mobile IPs change frequently;
- providers, VPNs, carrier NAT, roaming, and office networks can change the
  apparent client IP;
- a legitimate operator may need access away from a home network;
- `X-Forwarded-For` and similar headers are trustworthy only when the immediate
  reverse proxy is explicitly configured as trusted and the proxy chain is
  validated.

Future access options can include VPN/Tailscale/WireGuard/Cloudflare Access,
trusted reverse proxies, and per-user or per-feature allowlist records for
dangerous actions. Allowlist records should support CIDR, label, enabled state,
optional expiry, scope, audit of add/edit/disable/delete, and audit of denied
decisions without unnecessary personal data.

Emergency recovery for a bad allowlist must be local CLI/SSH on the server, not
an undocumented public web bypass.

### Break-glass recovery

Recovery must be an official local flow, not a hidden backdoor. It should work
only for an operator with local shell access or SSH access to the server.

Future recovery CLI requirements:

- create a one-time recovery token or link with a short TTL;
- write an audit/log record when the token is created and consumed;
- allow resetting an owner password or creating a new owner-level web user;
- allow disabling a broken IP allowlist or policy gate only through explicit
  local recovery intent;
- never expose recovery through an unauthenticated public web route;
- never bypass audit silently.

This is the fallback for broken web auth, lost owner credentials, bad tier or
policy state, and allowlist lockout. CLI and TUI must remain usable management
paths even when the web panel is broken.

### Future mobile and device trust

Mobile/device trust is future work, not an MVP requirement. A mobile app must
not rely only on an application signature, package name, or obscured client
secret.

Preferred future direction:

- each registered device gets its own keypair;
- the server stores the device public key and user/device metadata;
- sensitive requests use server-provided challenges and device signatures;
- devices can be revoked individually;
- device trust can combine with 2FA/passkeys and IP/VPN policy for dangerous
  actions;
- device trust never replaces user authentication, roles, permissions, CSRF
  protection for browser flows, or audit.

### Required module architecture

Future implementation should add or confirm these module boundaries before
building the UI:

- `web/auth/users` or `web/users` for web-user storage, password changes,
  disable/enable, role assignments, and session resets;
- `web/auth/roles` and `web/auth/permissions` or equivalent for role and
  permission definitions;
- `web/policy/features` or similar for central policy/feature-gate evaluation
  across roles, explicit permissions, entitlements, IP allowlists, device trust,
  and feature flags;
- a typed settings registry for runtime/product/security settings instead of
  ad hoc keys scattered through templates or routes;
- explicit `web.db` migrations for users, roles, permission grants, role grants,
  entitlements, security settings, allowlists, trusted proxies, recovery token
  metadata, and device registrations.

Services own workflow, audit, recovery, and policy evaluation. Routes remain
HTTP glue. Templates render already-authorized state and must not contain the
source of truth for tier, policy, or security decisions.

It is acceptable for MVP data models to assume one local managed server per web
installation. Do not hard-code that there will only ever be one web user.

## Commercial feature posture

The current project has no licensing, billing, subscription, entitlement, or
paid-feature code. If armactl later ships to different customers with paid
tiers, keep the product model separate from the core local server-management
logic.

Treat roles, permissions, and paid tiers as separate layers:

- permissions are the exact operations the app can authorize, for example
  `dashboard:view`, `logs:view`, `actions:run`, `settings:manage`,
  `files:read`, `files:write`, `schedule:manage`, `server:update`,
  `diagnostics:run`, `users:manage`, `allowlist:manage`, `terminal:use`, and
  `host:manage`;
- roles are operational bundles, for example `viewer`, `operator`,
  `server_admin`, and `platform_owner`;
- tiers are product bundles, for example `basic`, `plus`, `premium`, and
  `mega`; they should grant or suggest permissions through explicit mappings,
  not appear as hidden ad-hoc checks throughout route handlers.

Initial tier direction:

- `basic`: dashboard/status and limited read-only visibility;
- `plus`: basic operator actions such as start/restart and selected logs or
  diagnostics;
- `premium`: broader server-management workflows such as schedule, safe config,
  files, mods/admins/bot flows, and the diagnostics command palette;
- `mega`: internal/platform-owner level for Deus/Yaroslav-style operators,
  including user management, tier/role assignment, IP allowlist management,
  break-glass terminal eligibility, and future host controls. `mega` grants
  eligibility only; routes still need explicit permissions such as
  `config:raw_edit`, `terminal:use`, `host:manage`, or `allowlist:manage`.

`mega` should be treated as privileged operator access, not simply a public
paid tier. It should be small, auditable, and separated from ordinary customer
plans.

IP allowlists should be first-class policy records, not hard-coded constants.
A future management UI/API should support:

- CIDR entries such as `203.0.113.42/32` or an office subnet;
- labels such as "Deus home" or "Yaroslav office";
- enabled/disabled state;
- optional expiry for temporary access;
- audit records for add, edit, disable, delete, and denied access decisions;
- trusted-proxy rules so the app only trusts forwarded client IP headers from
  configured proxies.

Sensitive features such as diagnostics command palette, break-glass terminal,
raw config editor, allowlist management, host controls, banlist management,
file delete, and SAT runtime edits should require both permission checks and
the relevant policy checks, not only a tier name.

Recommended direction:

- keep the self-hosted core usable without a mandatory external billing
  service;
- store local feature entitlements in `~/armactl-data/web/web.db`;
- make entitlements explicit in route guards and templates instead of hiding
  checks deep inside backend modules;
- audit denied premium actions the same way successful mutating actions are
  audited;
- keep emergency local access to CLI/TUI available even if the web panel or
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
- The file manager is not the config editor. Do not expose `config.json` raw
  editing through `/files`; normal config work belongs in `/config` and any raw
  editor belongs to a `/config` break-glass mode.
- Delete/rename/edit/overwrite should be added only after
  upload/download/list are proven safe, with CSRF, confirmation for destructive
  actions, audit, path jail checks, no symlink escape, and backup/quarantine or
  rollback where practical.
- Keep the repository root out of the allowed roots.
- Do not allow upload into `.git`, `.venv`, system unit directories, or the
  armactl source tree.
- Treat archive extraction as out of scope for the first version. Uploading an
  archive as a file is fine; server-side unpacking needs a separate threat
  model.

The current web slices implement the safe browser foundation: fixed root
selection, relative-path directory listing, metadata, bounded redacted text
preview, single-file attachment download, and single-file upload for new
targets under the `server` root without overwrite. Upload-new-file is staged in
a temporary file first; no final uploaded file is published unless `file.upload`
audit was written first. Other roots remain browse/download only for now. They
do not implement overwrite, delete, rename, remote mount support, or archive
extraction.

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

## Player registry and moderation model

The branch now has a first-class player registry foundation and an initial
moderation convenience surface. This is separate from web users and game admins:
web users are people who log into the control panel, while the player registry
tracks Arma players seen on the managed game server.

Implemented foundation: `/admins` includes a lightweight Players / Moderation
section backed by the existing `player_view`/RCON roster path. It shows current
player names, filters server-side by nickname or reliable ID, and offers
add-to-game-admin only when a stable admin reference is available. It does not
store IP addresses or expose ban/unban actions.

Implemented registry foundation: `/players` is a read-only player registry page
backed by an instance-scoped `players.db` under the game instance root, not the
web runtime database. The page shows reliable ID, current nickname, first/last
seen, seen count, and source, with server-rendered search by nickname or ID.
Recording current players is an explicit CSRF-protected POST refresh, not a
write-on-GET. Only reliable `player_view`/RCON roster identities are stored;
slot-only rows are ignored and no IP addresses are stored.

Current code boundary:

- `web/services/player_sources.py` owns current-player collection from
  `player_view`/RCON and reliable identity normalization for live rows.
- `web/services/player_registry.py` owns instance-scoped SQLite storage,
  `players.db` creation/mode, schema-version migrations, registry queries, and
  snapshot persistence.
- `web/page_models/players.py` owns `/players` registry page DTOs and the
  `/admins` Players / Moderation panel DTO/filtering.
- `web/services/player_actions.py` owns the explicit refresh workflow and audit
  intent/outcome records.

Future banlist, session tracking, activity timeline, and player detail views
should plug into these boundaries rather than mixing source collection,
storage, page DTOs, and audit workflow again. Reliable identity/admin reference
remains required for persistence and moderation actions, and player IP storage
remains off by default unless a later privacy/security review explicitly
approves it.

`/admins` may keep a convenience quick add-to-admin action for current players
with a reliable admin reference, but the full player database and moderation
workflow belongs on a dedicated `Players / Moderation` page or section.

Goals:

- collect the list of players who connected and played when a reliable identity
  source is available;
- store reliable identity/admin reference, current nickname, nickname history,
  first seen, last seen, seen count, source, and confidence;
- add future session history with `connected_at`, `disconnected_at`, and played
  duration once reliable join/leave events are available;
- show active, recent, and inactive players in the web panel;
- provide search and filters by nickname and player identifier;
- add a detail page per reliable player with aliases, observations, sessions,
  admin status, and moderation history;
- support a ban list with reason, created-by web user, timestamp, optional
  expiry, active/revoked state, and audit trail;
- expose ban/unban actions through authenticated, permission-protected,
  CSRF-protected web flows with explicit confirmation;
- do not store player IP addresses by default; if a later security-reviewed
  workflow truly needs IP metadata, gate it behind explicit policy, retention,
  and redaction rules;
- keep CLI/TUI usable for emergency moderation fallback later.

Storage should be instance-scoped, not tied only to the web runtime. Prefer a
small SQLite database under the managed instance, for example
~/armactl-data/INSTANCE/players.db.

The current foundation includes `players` and `player_names` tables. The schema
is managed by `player_registry_schema_meta.schema_version` and can later grow
tables such as player_sessions, player_bans, and player_observations through
explicit migrations. Do not store this only in ~/armactl-data/web/web.db,
because player history belongs to the game-server instance and should remain
usable by future CLI/TUI/bot features as well as the web panel.

Ingestion sources must be explicit and conservative:

- current online player views from RCON/player_view when stable identifiers are
  available;
- server logs or journal lines if join/leave/player identity events can be
  parsed reliably;
- optional ServerAdminTools data only through a dedicated adapter, not by raw
  unrelated config rewrites;
- never guess a stable player ID from a nickname alone;
- do not treat A2S counts as identity data. A2S is useful for counts, not for a
  player registry.

Each observed identifier should keep its source and confidence. Nicknames are
not stable identities, so aliases should attach to a durable game/player ID
when that ID is available. If only a nickname is known, the UI should mark the
record as incomplete and avoid destructive moderation actions that require a
stable identifier.

Ban management must not be a blind JSON editor. It needs a moderation service
that writes through the correct backend adapter for the selected ban mechanism,
creates a backup before changing any config-backed ban list, validates the
result, and records an audit event. If ServerAdminTools bans are used, update
only the relevant SAT ban fields and preserve unrelated SAT settings. Ban and
unban actions must target a reliable identity/admin reference, SteamID64, or
other supported backend ID; never a nickname-only or A2S-slot-only row. Store
reason, actor, timestamp, active/revoked state, and optional expiry later.
Require auth, explicit `bans:manage`-style permission, POST + CSRF,
confirmation, audit logging, and backup/rollback where the backend is
file-backed. Keep bans separate from web users, roles, and product tiers.

Suggested web views:

- /players: reliable player registry with search is implemented; active/recent
  filters remain future;
- /players/PLAYER_ID: future identity details, aliases, observations, sessions,
  total playtime, and audit history;
- /bans or a moderation tab: active/revoked bans, reasons, expiry, and
  ban/unban actions;
- dashboard summary: online count remains lightweight; detailed player history
  belongs on dedicated pages.

Security and product notes:

- add explicit permissions such as players:view, players:manage, and
  bans:manage;
- keep bans and player moderation separate from web users, roles, and product
  tiers;
- redact or avoid sensitive tokens/secrets in player diagnostics;
- audit all ban/unban decisions;
- treat player history as operator data that may contain personally identifying
  nicknames/IDs; document retention/export/delete behavior before productizing
  hosted or paid features;
- keep analytics local to the instance for the MVP. Fleet-wide player analytics
  belong to a future central portal/control-plane layer.

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
- Server actions: `platform/service_adapter.py` for web workflows, backed by Linux/systemd `service_manager`
- Logs: `logs`, `report`
- Config: `config_manager`
- Mods: `mods_manager`
- Admins: `admins_manager`
- Schedule/timer: `platform/service_adapter.py` for web workflows, backed by Linux/systemd `service_manager`
- Telegram bot settings: `bot_config`, `bot_manager`
- Files: new safe filesystem adapter
- Players/moderation: instance-scoped player registry foundation is implemented;
  ban-list service, details/history, session/activity views, and extra ingestion
  adapters remain future work

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

## Future Windows backend adapter

The current backend and web MVP are Linux/systemd-first. Windows support is a
future platform architecture task, not something to mix into the current Linux
web implementation route by route.

Windows support needs explicit backend abstractions for:

- service manager: systemd versus Windows Service, NSSM, or Task Scheduler;
- logs: journalctl versus files and Windows Event Log;
- path discovery and runtime data locations;
- firewall, process, and metrics collection;
- install, repair, update, and service-template flows.

Design and test those adapters before wiring them into the web panel. Until
then, keep web implementation and docs scoped to the Linux VM deployment model
described in this plan.

## Web architecture guardrails

Treat these rules as the default for every future web slice. They are meant to
keep the web panel from turning into a second TUI, a raw shell wrapper, or a
parallel source of truth.

### Layer boundaries

- `routes/` are HTTP adapters only: authenticate, authorize, validate request
  data, call one facade/service, and render a template or return JSON.
- `page_models/` and `views/` build read-only DTOs for templates and polling JSON; `facade.py` is a legacy re-export shim only.
  They may combine backend data, but they must not mutate server state.
- `services/` contains web-owned workflows such as config edits, admin actions,
  mod actions, filesystem containment, audit, pending work, and job dispatch.
  Services call existing backend modules; they do not call CLI commands.
- Existing backend modules under `src/armactl/` remain the source of truth for
  game server behavior, config, mods, admins, services, logs, and schedules.
- TUI/Textual code is not an API boundary. Web code must not import screens or
  widgets from the TUI package.

### Mutating action contract

Every web action that changes server, host, filesystem, user, or runtime state
must have all of these before it is exposed:

- authenticated session;
- explicit permission check, not just a role/tier name;
- POST-only route with CSRF validation;
- bounded and validated input;
- audit record with redacted details;
- backup or rollback path when editing operator-owned files;
- controlled success/failure result that does not expose secrets;
- pending operator work entry when the saved change needs a later restart or
  manual step.

Keep responsibilities separated: routes are HTTP glue, service modules own
workflow/audit/pending-work/rollback or correction behavior, and low-level
adapters expose pure filesystem/system/database operations.

Delete, overwrite, raw JSON edit, host controls, terminal/command palette, ban
management, and premium/mega-only tools must go through this contract before UI
work starts.

### Web runtime data

`~/armactl-data/web/` may store web-only runtime data: users, sessions, CSRF
state, rate limits, background jobs, pending operator work, and web audit
metadata. It must not become a replacement source of truth for game config,
mods, admins, schedules, service state, or logs.

Instance-scoped data that belongs to a game instance, such as `players.db`,
belongs under that instance root. Keep IP storage off by default unless a later
privacy/security review explicitly approves it.

SQLite schema changes must go through versioned migration runners, not ad hoc
service-local compatibility writes. `web.db` uses
`web_schema_meta.schema_version`; instance player registries use
`player_registry_schema_meta.schema_version`. Migrations must be idempotent,
preserve existing long-lived installs, keep private DB files at mode `0600`,
and run schema compatibility before maintenance such as duplicate active job
repair.

### Pending work and jobs

Pending operator work and background jobs are different concepts:

- pending operator work means "a saved operator change still needs a manual or
  scheduled follow-up", usually a game server restart;
- background jobs are queued/running/completed long-running operations.

Pending work stacks by category, such as config, mods, and admins, and clears
only after a successful relevant action such as a game-server restart or the
restart helper. Dashboard may show a compact pending-work summary. The
operations/jobs page must keep pending operator work and background jobs as two
separate sections with precise empty states. Do not hide pending work just
because there are no background jobs, and do not store pending restart state as
a fake job.

### Testing rules

Prefer tests that exercise the public app factory and web routes through
TestClient, with backend services, page-model loaders, facades, or platform
adapters monkeypatched at stable module boundaries. Do not patch FastAPI
endpoint `__globals__`, route function globals, app router endpoint internals,
or already-imported route-module aliases.

For import-safety tests, use subprocess checks instead of mutating
`sys.modules` inside the main pytest process. Do not make tests depend on the
operator's saved UI language, global TUI language state, real systemd units, or
network availability.

When a test needs alternate backend behavior, patch the service, page-model,
facade, or adapter seam the route calls, create the app after the patch when
possible, and assert that the route did not fall through to real backend/systemd
code.

### UI primitives

Use shared web UI primitives before adding page-specific layout hacks:

- status pills for state;
- key-value lists and value blocks for details;
- compact tables for repeated operational rows;
- floating notifications for transient save/action results;
- topbar notification center for pending work and important warnings.

Avoid inserting large page-top notices that shift forms under the user's mouse.
Dashboard stays an operator overview; deeper settings, diagnostics, mod
runtime config, raw JSON, and danger-zone actions belong on dedicated pages.

### System audit gate

Large or risky web slices should run the module-by-module audit described in docs/web-system-audit.md. Use it twice when the slice touches security, persistence, routing boundaries, file operations, jobs, permissions, or deployment behavior: once before implementation to confirm the current boundary, and once after implementation to catch new shortcuts before merge or VM smoke. The audit output should list blockers, should-fix items, follow-ups, deferred non-blocking items, tests, and docs updates. Reliability, security, data-loss, privacy, and operator-trust issues must be treated as blockers until fixed.

Whole-project modularity work, platform backend work, player/banlist domain work, and broad architecture cleanup should use `docs/system-modularity-audit.md` instead of treating the issue as web-only. That audit checks CLI/TUI/web/bot adapters, backend modules, platform adapters, persistence, tests, CI, and docs before any large rewrite is attempted.

### Known architecture debt

Pay this down before adding another large web feature slice. These are refactors only; they should preserve current behavior and tests.

Before starting a project-wide modularity refactor, run the baseline audit in `docs/system-modularity-audit.md` and turn the findings into small ordered slices. Do not mix that baseline audit with feature work.

- Done: split the broad routes/management.py surface into domain routers for config, mods, admins, and bot flows without changing URLs, permissions, templates, CSRF checks, audit calls, or pending-work behavior.
- Completed: split `facade.py` by page/domain DTO. Dashboard/status aggregation, config, mods, admins, bot, and schedule page loaders now live under `src/armactl/web/page_models/`; `facade.py` is only a compatibility re-export layer.
- Done: split `services/filesystem.py` into roots, path-safety, listing, preview, transfer, and thin compatibility facade modules before future delete, text edit, overwrite, rename, or archive extraction flows.
- Done: split the oversized `tests/test_web_app.py` into focused app wiring, auth route, preference, dashboard, management layout, jobs route, and service route test modules. Touched tests patch stable service/page-model seams instead of imported route globals or FastAPI endpoint internals.
- Done: split player registry / moderation boundaries before banlist work. Current roster collection lives in `services/player_sources.py`, SQLite registry storage in `services/player_registry.py`, `/players` and `/admins` web DTO/loaders in `page_models/players.py`, and explicit refresh+audit workflow in `services/player_actions.py`.
- Done: closed player refresh failure outcome audit should-fix. `refresh_registry_and_audit()` writes intent audit before roster load/persist, aborts if intent audit fails, records service-layer success/failure outcome audit, and reports outcome-audit failure after successful persist without rolling back `players.db`.
- Done: repeated the architecture/modularity audit after the completed refactor slices so follow-up feature work starts from the updated module boundaries.
- Done: audited broad except Exception usage in web routes/services for the mutating-route cleanup slice. Removed generic catches from mods/admins/service/schedule routes, removed generic manager catches from mods/admins action services, and let unexpected service-manager exceptions propagate instead of rendering fake action results. Remaining broad catches are documented fail-closed/degradation or best-effort cleanup cases only:
  - routes/dashboard.py: dashboard HTML and status JSON degrade to controlled unavailable responses.
  - services/log_views.py: fixed diagnostics/report preview fail closed without tracebacks.
  - page_models/players.py: read-only current-player panel degrades to unavailable UI data.
  - services/mod_actions.py, admin_actions.py, service_actions.py, schedule_actions.py: discovery preflight failures become controlled unavailable/domain results before mutation.
  - services/pending_work.py: web.db failures fall back to private sidecar storage/listing.
  - services/server_job_actions.py: audit-failure cleanup cancels just-created jobs best-effort while preserving the original audit error.
- Treat best-effort cleanup, such as cancelling a just-created job after audit failure, as explicit behavior with a comment/test instead of an invisible workaround.
- The `web_jobs` duplicate-active persistence blocker is closed for install/repair: schema maintenance cancels old duplicate `queued`/`running` rows deterministically, active lookup uses an indexed `(kind, instance, status, created_at, id)` path, and `/jobs` surfaces job-store integrity warnings instead of hiding them as deferred debt.
- Follow-up owner: next jobs/update slice. Run `EXPLAIN QUERY PLAN` plus latency checks on production-scale job history before adding update jobs or a standalone worker daemon.

Internal API readiness:

| Area | Readiness for web | Needed adapter work |
|------|-------------------|---------------------|
| Read-only status/dashboard | High | Implemented through one dashboard DTO from discovery, service/timer status, metrics, players, config summary, mods, web runtime, and safe bot summary |
| Start/stop/restart | High | Default-instance web controls are implemented with auth, confirmation, CSRF, audit log, and route-level permission checks |
| Players/moderation | Medium | Current-player moderation foundation is implemented on `/admins` using `player_view`/RCON roster data and add-to-game-admin only for reliable IDs; `/players` and instance-scoped `players.db` are implemented for reliable IDs, nickname history, first/last seen, and seen count; source collection, registry storage, page DTOs, and refresh+audit workflow are split into separate modules; future session/activity history with duration, detail views, extra ingestion adapters, and ban-list management remain; do not infer IDs from nicknames or A2S counts, and do not store IPs by default |
| Config/mods/admins/bot settings | Medium-high | Basic allowlisted config editing is implemented through `config_manager`; future config expansion must start with a verified config schema inventory and safe controls in `/config`, not `/files`; raw JSON remains an owner/admin-only `/config` break-glass flow; basic mod add/update/enable/disable/remove is implemented through `mods_manager`; game admin add/update/remove is implemented through `admins_manager`; bot mutations, advanced modpack/bulk mod flows, advanced admin bulk/raw flows, and broader config fields remain future work with form validation, CSRF, and redacted error rendering |
| Logs/report | Medium-high | Bounded read-only audit, fixed journal, and redacted report preview views are implemented; add streaming/download later without `os.execvp` |
| Install/repair/update | Medium | Install and repair enqueue explicit web background jobs; update remains future work behind a version-check adapter/read model and an explicit update job; never block a request thread or shell out from a route |
| File manager | Medium | Safe adapter, browser foundation, single-file download, and server-root upload-new-file are implemented; overwrite/delete/rename and remote mount support remain future work; `/files` must not become the raw `config.json` editor |
| Web users/roles/entitlements | Low | Implement new `web.db` models; do not reuse game admins as web users |

## Existing feature inventory

The current repo already implements most of the management behavior that the
web panel should expose. The web work should reuse these modules and not copy
logic from TUI screens.

| Product area | Current status | Existing source | Web implication |
|--------------|----------------|-----------------|-----------------|
| Dashboard/status | Implemented in TUI, CLI, and the web read-only dashboard | discovery, state, status_summary, metrics, player_view, ports, bot_config | Keep future routes thin and continue extending the facade instead of route-local aggregation |
| Server controls | Implemented | `service_manager`, CLI `start/stop/restart`, TUI `ManageScreen` | Web start/stop/restart now wraps existing calls for the default instance; schedule controls are implemented separately and job-backed operations remain future work |
| Install/repair/update jobs | Install/repair job-backed, update future | `installer`, `repair`, `web_jobs`, future server-version/update adapter | Update must use a read-only version check, service-layer enqueue workflow, background job progress, and adapter-backed SteamCMD/app manifest/log/version metadata; no Steam credentials in `web.db` |
| Logs/report | Implemented for journal/report, TUI live view, and bounded read-only web views | `logs`, `report`, `TailLogScreen` | Web exposes fixed sources and redacted report preview; add browser streaming/download later |
| Config editor | Implemented in structured and raw TUI flows | `config_manager`, `ConfigEditorScreen`, `RawConfigScreen` | Web supports allowlisted basic field edits through `config_manager` behind `settings:manage`; future web expansion should inventory verified fields into Basic, Gameplay, Visibility/Crossplay, Network/A2S/RCON, Security, Advanced, and Danger Zone groups; advanced fields need `settings:advanced`; generic/raw JSON and secrets remain out of scope except for a future owner/mega-eligible `/config` break-glass mode with `config:raw_edit` |
| Mods manager | Implemented beyond basic parity | `mods_manager`, `mods_state`, `addon_cleanup`, `ModManagerScreen` | Web can view active/disabled mods and add/update/enable/disable/remove one mod at a time through `mods_manager` with auth, CSRF, `mods:manage`, confirmation for remove, and audit; bulk paste/import/export/clear-all/modpack workflows remain future |
| Server admins | Implemented for Arma `game.admins` | `admins_manager`, `AdminManagerScreen` | Web can view game admins and add/update/remove one admin at a time through `admins_manager` with auth, CSRF, `admins:manage`, confirmation for remove, and audit; keep game admins separate from web users/roles |
| Player registry and bans | Registry foundation implemented, bans future | `player_sources` wraps current `player_view`/RCON data; `player_registry` owns instance SQLite storage; logs/RCON/SAT may become ingestion sources after adapter review | `/admins` exposes current players with server-rendered search and add-to-game-admin only when a reliable identity ID is available; `/players` stores reliable IDs and nickname history in instance-scoped SQLite through explicit refresh; page DTOs live in `page_models/players.py` and refresh/audit lives in `player_actions`; next steps are recent/history/detail views, session duration with connected/disconnected timestamps, extra ingestion adapters, and ban-list management with reliable IDs only, auth, permissions, CSRF, confirmation, backups, audit logging, and no IP storage by default |
| Backups/cleanup | Partially implemented | `config_manager` backups, `cleaner`, `CleanupScreen` | Config backups exist; full server backup/restore is future work |
| Schedule | Implemented for restart timer | `service_manager`, `ScheduleScreen`, CLI `schedule` | Web can show/set/enable/disable restart schedule, trigger restart-now, show next/last run, and warn on disabled game-service autostart; task chains are future |
| Telegram bot | Implemented | `bot_config`, `bot_manager`, `telegram_bot`, `BotConfigScreen` | Read-only bot status page exists; future config/service flows can reuse the same `.env` and service-manager path |
| File manager | Browsing, single-file download, and server-root upload-new-file implemented | `paths`, new web filesystem adapter | Browser lists fixed local roots, bounded redacted text previews, validated single-file downloads, and no-overwrite uploads to the `server` root; overwrite/delete/rename remain future work; do not use `/files` as the normal config editor |
| Web users/roles | Partially implemented | `web.db` owner user, password hashes, sessions, CSRF primitives, login/logout cookie wiring, and code-level permission categories exist | Add editable users/roles/tier assignment only after the permission matrix and owner-only admin UI are designed |
| Paid features | Not implemented | none | Add explicit entitlement/tier model only if productized; keep basic/plus/premium/mega separate from low-level permissions |

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
- Config: safe structured summary plus allowlisted basic field editing with validation and adjacent backup
- Mods: view active and disabled mods, add/update one mod, enable/disable, and
  confirmed remove; bulk paste/import/export/clear-all/modpack workflows remain
  future
- Admins: view game admin IDs/local labels and add, update, or remove one admin
  at a time; advanced bulk/raw flows remain future
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

### Dashboard freshness model

The initial dashboard can remain server-rendered HTML so login, auth, no-JS, and
manual refresh behavior stay simple and reliable. For normal operation, the
panel should add a lightweight authenticated JSON status endpoint and
package-local JavaScript polling so live values such as lifecycle, service
state, players, FPS, telemetry age, and host metrics update without a full page
reload.

Use polling first, not WebSocket. A 5-10 second interval is enough for server
operator status and keeps the implementation smaller, easier to test, and less
fragile behind reverse proxies. The endpoint must reuse the existing dashboard
facade/view-model data, expose only a small safe DTO, require the same
dashboard:view permission, and never become a second source of truth for
server state.

The no-JS fallback remains the current full page refresh. If polling fails, the
UI should leave the last known values visible and show a subtle stale indicator
instead of spamming errors.

Compact visual meters for Server FPS and host CPU/RAM/disk ride on the same
status endpoint. They use only safe numeric DTO fields and do not persist
backend metric history. A dedicated FPS history chart should be designed as a
separate UI step instead of being squeezed into the compact live-server card.

Future server-version state belongs in the same read-model discipline. The
dashboard should be able to show the installed server build/version, the latest
available game/server build/version when it can be determined safely, and a
compact status: `up to date`, `update available`, `unknown`, or `check failed`.
Failure to determine the latest version must degrade to an unknown/check-failed
badge and must not break the dashboard HTML or status JSON. The version source
should be a backend adapter/API over safe metadata such as SteamCMD/app
manifest/log/version metadata, not hardcoded parsing in routes or templates.

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
- Fast preferences UX is implemented: theme changes are instant in the browser
  and persist through a CSRF-protected lightweight POST; language preference
  writes can use the same lightweight path before one server-rendered reload.
- The always-on `armactl-web.service` template and `armactl web service ...`
  commands are implemented for production service installation and lifecycle
  management.
- Update packaging so web templates/static files are included in editable,
  wheel, and sdist installs.
- Keep the marketing `website/` untouched and separate from the management UI.

### Phase 2 - Safe read-only dashboard

- Status endpoint and dashboard.
- Metrics and player view.
- Read-only config, mods, game admins, and Telegram bot detail pages are implemented through domain page-model loaders.
- Read-only logs/report views are implemented with fixed sources, bounded output, and redaction.
- No mutating actions in this phase except login/logout.

### Phase 3 - Controlled server actions

- Start/stop/restart for the current default instance is implemented through
  the `ServiceAdapter` boundary.
- Schedule show/set/enable/disable/restart-now with next-run and boot-policy
  visibility is implemented through the same boundary.
- The default adapter remains Linux/systemd through `service_manager`; CLI/TUI
  direct calls are compatibility paths until later migration slices.
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
- Pending operator work is shown on `/jobs` in a separate dense table from
  background jobs. It is stored in `web_pending_work`, not `web_jobs`, and can be
  present even when there are no background jobs. Empty states say either
  "No pending operator work." or "No background jobs." precisely.
- Job-store reliability hardening is in place: schema v8 migrations preserve
  current web runtime tables, migrate older/minimal shapes, and run duplicate
  active `queued`/`running` repair by `(kind, instance)` after schema
  compatibility while keeping the oldest active job. Active lookup is backed by
  the `idx_web_jobs_active_lookup` index, and `/jobs` shows job-store integrity
  warnings separately from pending operator work.
- Install and repair now use explicit background job handlers and enqueue routes.
- Use the job model for future SteamCMD/update and large file actions. Update
  availability checks may be read-only operations or lightweight/background
  jobs, but update execution itself must be a background job.
- Until explicit handlers and routes exist for future operations, keep them out
  of web rather than blocking request threads.

### Phase 4 - Config, mods, admins, and bot editing

- Basic structured config editor is implemented through `config_manager` for
  allowlisted non-secret fields: server name, scenario, max players,
  visibility, BattlEye, and server distance values. Saves create an adjacent
  `config.json.before-web-config-save-YYYYMMDD-HHMMSS.bak` before atomic
  write and do not auto-restart the server.
- Extended config fields, admin/RCON passwords, and generic secret edits
  remain out of scope for web. A raw JSON editor is future emergency/admin-only
  work, not part of the normal operator config flow.
- Basic mod add/update/enable/disable/remove is implemented through
  `mods_manager` with active/disabled sections, confirmation before remove,
  and audit logging. Bulk paste/import/export/clear-all/modpack workflows
  remain future work.
- Game admin add/update/remove is implemented through `admins_manager`, kept
  separate from web users and roles. Advanced bulk/raw admin flows remain
  future work.
- Telegram bot configuration through `bot_config` without exposing token values.
- Validation errors rendered in UI.

### Phase 4b - Install and repair jobs

- `server:install` and `server:repair` are explicit web job kinds.
- Dashboard actions enqueue jobs and redirect to `/jobs`; HTTP requests do
  not run installer or repair generators directly.
- The enqueue route starts a web-process background worker thread for the
  queued job. The registered handlers stream bounded redacted output into job
  metadata.
- A durable standalone worker daemon remains future hardening for process
  restarts and multi-worker deployments.
- Update remains future work. The future update flow must follow this contract:
  - Version check is separate from update execution. It is read-only or a
    lightweight/background job and feeds a dashboard read model with installed
    build, latest available build when safely known, and `up to date` /
    `update available` / `unknown` / `check failed` state. If the installed
    server version/build equals the latest available version/build, do not
    create an update job. Show a controlled result such as `Server is already up
    to date`, treat it as a successful no-op rather than a failure, audit the
    safe read-only check result without secrets, and keep the dashboard state at
    `up to date`. If the latest version is unknown or the check failed, do not
    start update automatically; show controlled `unknown` or `check failed`
    state and allow any operator override only through a separate future policy.
  - Version discovery lives behind an adapter/API that may use SteamCMD, app
    manifests, logs, or server metadata. Do not hardcode Steam output parsing in
    routes/templates, and do not store Steam credentials or secrets in `web.db`.
  - The "Update server" action is a separate explicit background job, not a
    direct route shell-out. The route does only auth, explicit
    `server:update` or `jobs:update` permission, CSRF, confirmation, service/job
    enqueue, and response rendering.
  - The service workflow is permission -> CSRF -> intent audit ->
    enqueue/mutation -> outcome audit -> job/progress state. Output tails and
    failure metadata are bounded and redacted, with no secrets in logs.
  - Update jobs are idempotent and deduplicated by kind/instance like
    install/repair jobs, with controlled already-running/already-current
    outcomes instead of duplicate active jobs.
  - Update is never automatic by default. Operators must see impact before
    confirming: the server may stop/restart, players may disconnect, and
    config/state should be preserved. The job result should include
    rollback/recovery notes where the backend can provide them.
  - If the game server is running, the future policy must require explicit
    confirmation, may optionally stop/drain before updating, and should restart
    only when the operator confirms or the update workflow explicitly owns the
    restart step.
  - `/jobs` shows update jobs with progress/status/log tails. The dashboard may
    show compact `update available` or `update running` signals, but update
    availability must not be mixed with pending operator work unless a manual
    operator action is required.
  - Backend implementation belongs in service/adapter layers prepared for the
    current Linux/systemd backend and a future Windows backend. SteamCMD,
    systemctl, process, path, and manifest logic must not spread into routes or
    templates.
  - If paid tiers are added, update availability/action checks pass through an
    explicit feature/policy gate, but tiers remain separate from low-level
    permissions; a tier name alone must not authorize `server:update`.

### Phase 5 - Filesystem manager

- Safe allowed-root browser foundation is implemented.
- The filesystem adapter has been split into roots, path-safety, listing,
  preview, and transfer modules. `services/filesystem.py` is a thin
  compatibility facade only.
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
- Patch stable service/page-model/adapter seams in web route tests; do not patch
  FastAPI route globals, endpoint internals, or systemd internals.
- Run browser smoke tests locally against `127.0.0.1` once the first UI exists.
- Keep tests independent from saved runtime language and user settings.
- Do not require a live Arma server for normal CI/local unit tests.
- Keep live VM checks as manual smoke tests.

## Open decisions

- External access default: localhost plus reverse proxy should be the default;
  direct bind should be explicit.
- Whether install/repair flows belong in web v1 or should stay CLI/TUI until a
  background job runner exists.
