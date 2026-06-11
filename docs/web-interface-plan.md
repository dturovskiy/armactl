# Web interface plan

## Goal

Add a browser-based management interface for armactl so an operator can manage a
remote Arma Reforger server without opening SSH and running CLI/TUI commands.

The web interface must run on the same Linux host/VM as armactl and the game
server. It should reuse the existing backend modules instead of duplicating TUI
logic.

## Non-goals for the first version

- Do not expose arbitrary host filesystem access.
- Do not run game-server installation or repair flows as long blocking HTTP
  requests.
- Do not make the static `website/` marketing page the management UI.
- Do not require Telegram bot setup.

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

## Implementation phases

### Phase 1 - Test and web foundation

- Keep tests independent from saved UI language.
- Add web dependencies and package skeleton.
- Add web runtime config loader.
- Add auth/session/CSRF primitives.
- Add service template for `armactl-web.service`.

### Phase 2 - Safe read-only dashboard

- Status endpoint and dashboard.
- Metrics and player view.
- Read-only logs.
- No mutating actions yet except login/logout.

### Phase 3 - Controlled server actions

- Start/stop/restart via existing `service_manager`.
- Schedule show/set/enable/disable.
- Audit log for mutating actions.

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

## Open decisions

- Web framework: likely a small ASGI app with server-rendered templates first,
  then richer JS only where it helps.
- Auth storage: single local admin user for MVP, multi-user later.
- External access default: localhost plus reverse proxy should be the default;
  direct bind should be explicit.
- Whether install/repair flows belong in web v1 or should stay CLI/TUI until a
  background job runner exists.
