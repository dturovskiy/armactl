# armactl

Installer, manager, and TUI for **Arma Reforger Dedicated Server** on Ubuntu.

`armactl` is built for operators who want one tool that can install a server
from scratch, detect an existing installation, repair broken state, manage
`systemd`, edit `config.json`, work with mods, and optionally expose a Telegram
admin bot.

## Who this is for

- Ubuntu 24.04 hosts
- single dedicated server instance
- single Linux user
- operators who prefer a repo-local launcher over global package setup

## Quick start

```bash
git clone https://github.com/dturovskiy/armactl.git
cd armactl
./armactl
```

On first run, `./armactl` bootstraps the repo-local environment automatically
and then opens the TUI. After that, keep using the same launcher: no PATH
changes, no manual venv activation.

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

For older scattered installs, see [docs/migration.md](docs/migration.md).

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

## Runtime layout

`armactl` separates three layers:

| Layer | Location | Purpose |
|-------|----------|---------|
| Source code | this repository | CLI + TUI + backend modules |
| Runtime data | `~/armactl-data/default/` | server files, config, backups, state |
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

See [docs/telegram-bot.md](docs/telegram-bot.md) for the full flow.

## Documentation

- [Architecture](docs/architecture.md)
- [Migration](docs/migration.md)
- [Localization](docs/localization.md)
- [Telegram Bot](docs/telegram-bot.md)
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
