# armactl

[![CI](https://github.com/dturovskiy/armactl/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dturovskiy/armactl/actions/workflows/ci.yml)
[![Latest Release](assets/badges/release.svg)](https://github.com/dturovskiy/armactl/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Ubuntu 24.04](https://img.shields.io/badge/ubuntu-24.04-E95420.svg)](README.md)

Installer, manager, TUI, and branch web foundation for **Arma Reforger Dedicated
Server** on Ubuntu. The browser management panel is implemented on
`feat/web-interface`; stable release and production-hardening work are still
pending, so the released management paths remain CLI, TUI, and the optional
Telegram bot.

`armactl` is built for operators who want one tool that can install a server
from scratch, detect an existing installation, repair broken state, manage
`systemd`, edit `config.json`, work with mods, and optionally expose a Telegram
admin bot.

## Screenshot

![armactl TUI main menu](assets/tui-main-menu.png)

## Who this is for

- Ubuntu 24.04 hosts
- single dedicated server instance
- single Linux user
- operators who prefer a repo-local launcher over global package setup
- remote operators who manage a VM over SSH today and want browser management
  after the web service is deployed

## Quick start

```bash
git clone https://github.com/dturovskiy/armactl.git
cd armactl
./armactl
```

On first run, `./armactl` bootstraps the repo-local environment automatically
and then opens the TUI. After that, keep using the same launcher: no PATH
changes, no manual venv activation.

To set up the browser panel on a server, use the same repo-local launcher:

```bash
./armactl web
```

The web setup flow installs the web dependencies, asks for the first owner only
when needed, writes the runtime config, installs/enables `armactl-web.service`,
starts it, and prints the URL/status summary. The safe default bind is local;
choose `lan` or pass `--access lan` only when the VM/network exposure is ready.

If you want the development toolchain from the first launch too, use:

```bash
ARMACTL_BOOTSTRAP_MODE=--dev ./armactl
```

## Core scenarios

### Fresh host

Use `./armactl` on a clean Ubuntu host and choose `Install New Server`.
`armactl` will bootstrap the environment, install SteamCMD if needed, download
the server, create config and runtime directories, generate `systemd` units,
and start the service.

### Existing server

Use `Detect Existing Server` or `Manage Existing Server`. `armactl` will look
for the runtime root, `config.json`, `systemd` service, timer, and current
ports, then switch into management mode without reinstalling the server.

### Broken install / repair

Use `Repair Installation` in the TUI or:

```bash
armactl repair
```

Repair re-checks the installation, validates or regenerates missing pieces,
rebuilds service and timer files, reinstalls the secure helper when needed, and
refreshes `state.json`.

## What armactl does

- install a server from scratch via SteamCMD
- detect and manage an existing installation
- start, stop, restart, and inspect the server
- safely edit `config.json`, with an optional raw JSON screen
- manage mods: add, remove, dedupe, import, export
- manage scheduled restarts through `systemd`
- expose optional Telegram bot controls
- run the branch web foundation for browser-based server management
- show real Arma Reforger server FPS/frame-time telemetry when `-logStats` data is available
- keep runtime data separated from repo code

## Scheduled restarts

In TUI, `Restart Schedule` accepts exact times instead of raw `systemd` syntax:

```text
08:00
08:00, 20:00
08:00 20:00
```

`armactl` converts those values into the correct `OnCalendar=` entries in the
timer unit automatically.

## Server FPS telemetry

`armactl` can show real Arma Reforger Dedicated Server FPS and frame-time
telemetry.

For generated services, `start-armareforger.sh` starts the server with:

```text
-logStats 10000
```

The server writes periodic engine telemetry into the runtime console log, and
`armactl` reads the latest valid `FPS:` line from:

```text
~/armactl-data/<instance>/config/logs/*/console.log
```

Example:

```text
Server FPS:  60.0
Frame time:  16.7 ms avg / 18.5 ms max
Telemetry:   4s old
```

This value is the server engine's own FPS telemetry. It is not estimated from
CPU usage.

For existing installations, regenerate the service/start script and restart the
server after updating `armactl` so the process starts with `-logStats 10000`.

## Runtime layout

`armactl` separates source code, game runtime data, web-runtime data, and log
storage:

| Layer | Location | Purpose |
|-------|----------|---------|
| Source code | this repository | CLI, TUI, web package, and backend modules |
| Runtime data | `~/armactl-data/default/` | server files, config, backups, state |
| Web runtime | `~/armactl-data/web/` | web panel settings, users, sessions, jobs, and pending-work DB |
| Player registry | `~/armactl-data/<instance>/players.db` | instance-scoped player registry foundation |
| armactl logs | `~/armactl-data/logs/` | centralized armactl-owned logs and audit files |
| System services | `/etc/systemd/system/` | auto-start and scheduled restarts |

Typical runtime structure:

```text
~/armactl-data/default/
├── server/
├── config/config.json
├── backups/
├── state.json
└── start-armareforger.sh
```

## Telegram bot

Telegram bot management is optional and runs as a separate `systemd` unit:

- `armactl-bot.service`
- TUI settings screen: `Manage Existing Server -> Telegram Bot`
- instance-scoped `.env` as the single source of truth for bot settings

Runtime config path:

```text
~/armactl-data/<instance>/bot/.env
```

The repository ships [.env.example](.env.example) as a template, while real
runtime `.env` files stay out of git.

Current bot capabilities include:

- status
- metrics
- details
- start / stop / restart
- scheduled restart management
- player visibility through A2S and local RCON
- real Server FPS/frame-time metrics when server telemetry is available

See [docs/telegram-bot.md](docs/telegram-bot.md) for the full flow.

## Web interface on `feat/web-interface`

The web management panel foundation is implemented on the `feat/web-interface`
branch. It runs inside the same VM as the Arma server and reuses the existing
backend modules instead of reimplementing install, service, config, mods, logs,
or filesystem logic. Stable release and production-hardening work are still
pending.

Current branch capabilities:

- authenticated dashboard with live status polling
- safe config editor for allowlisted non-secret fields
- mods management foundation
- game-admin management and current-player quick-add
- restart schedule and game-service autostart controls
- file browser with bounded preview, single-file download, and no-overwrite upload
- logs and diagnostic report views
- background jobs for install/repair and operator-visible job status
- instance-scoped player registry foundation with reliable IDs and no IP storage by default
- auth, sessions, CSRF protection, code-level permissions, and login throttling
- JSONL audit logging plus pending operator work for saved config/admin/mod changes

Future web work remains scoped to items such as the system admin panel for web
users, central policy/feature gates for roles/permissions/tiers, official local
break-glass recovery, IP allowlist/trusted-proxy handling, future mobile/device
trust, a settings registry, broader platform adapters, banlist management,
destructive file workflows, server update/version flows, and SAT/mod runtime
settings.

The public marketing website lives in the separate
[`dturovskiy/armactl-website`](https://github.com/dturovskiy/armactl-website)
repository. It is not the management panel and should stay separate from the
authenticated web UI.

Paid or premium features are not implemented. Any product tiers need a separate
policy/feature-gate layer and business/legal review: existing public MIT history
cannot be made private retroactively, and proprietary premium implementation
should not be committed to the public MIT repo without an explicit repo/license
decision.

## Documentation

- [Architecture](docs/architecture.md)
- [Localization](docs/localization.md)
- [Telegram Bot](docs/telegram-bot.md)
- [Web smoke checks and deployment](docs/web-deployment.md)
- [Web Interface Plan](docs/web-interface-plan.md)
- [Web Interface Handoff](docs/web-interface-handoff.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development](docs/development.md)
- [Release Process](docs/release-process.md)
- [Roadmap](docs/roadmap.md)
- [Checklist](docs/checklist.md)

## CLI commands

```text
armactl detect
armactl install
armactl repair

armactl start
armactl stop
armactl restart
armactl status
armactl logs
armactl ports

armactl config show
armactl config validate

armactl mods list
armactl mods add
armactl mods remove
armactl mods dedupe
armactl mods export FILE
armactl mods import FILE

armactl schedule show
armactl schedule set
armactl schedule enable
armactl schedule disable
```

## Development

```bash
./scripts/run-host-tests
.venv/bin/pytest
.venv/bin/ruff check src tests
```

If the repo was only bootstrapped in prod mode before, both `./armactl` and
`./scripts/run-host-tests` will refresh the repo-local `.venv` automatically
after `pyproject.toml` dependency changes.

## Project health

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security Policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Changelog](CHANGELOG.md)

## License

MIT
