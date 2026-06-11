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

## Backend surface

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

## Proposed package structure

```text
src/armactl/web/
  __init__.py
  __main__.py
  app.py
  auth.py
  config.py
  csrf.py
  files.py
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

Add a local smoke command or documented flow:

```text
armactl web run --dev --data-root /tmp/armactl-web-dev
```

This lets us test the panel locally before installing the service on a remote
VM.

## Implementation phases

### Phase 1 - Test and web foundation

- Keep tests independent from saved UI language.
- Add web dependencies and package skeleton.
- Add web runtime config loader.
- Add auth/session/CSRF primitives.
- Add service template for `armactl-web.service`.
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

- Web framework: likely a small ASGI app with server-rendered templates first,
  then richer JS only where it helps.
- Auth storage: single local admin user for MVP, multi-user later.
- External access default: localhost plus reverse proxy should be the default;
  direct bind should be explicit.
- Whether install/repair flows belong in web v1 or should stay CLI/TUI until a
  background job runner exists.
