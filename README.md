# armactl

Open-source installer, manager and TUI for **Arma Reforger Dedicated Server** on Linux.

## What it does

- **Install** a server from scratch via SteamCMD
- **Detect** an already existing server installation
- **Manage** server: start, stop, restart, status, logs, ports
- **Edit** `config.json` safely, with an optional raw JSON screen for advanced cases
- **Manage mods** — add, remove, dedupe, import/export
- **Automate** via systemd service and scheduled restart timer
- **Repair** broken or incomplete installations
- All through a beautiful **TUI** or directly from CLI

## Target platform

- Ubuntu 24.04
- Single dedicated server instance
- Single Linux user

## Quick start

```bash
git clone https://github.com/dturovskiy/armactl.git
cd armactl
./armactl
```

On first run, `./armactl` bootstraps the repo-local environment automatically
(system packages, `.venv`, Python dependencies) and then opens the TUI. After
that, keep using the same repo-local launcher — no PATH changes, no venv
activation needed.

If you want the dev toolchain from the first launch too, use:

```bash
ARMACTL_BOOTSTRAP_MODE=--dev ./armactl
```

## Existing server

If the server already exists on the host, launch `./armactl` and choose
`Detect Existing Server` or `Manage Existing Server`. armactl will look for the
runtime root, `config.json`, systemd service, timer and current ports, then
switch into management mode without reinstalling the server.

For older scattered installs, see [docs/migration.md](docs/migration.md).

## Repair mode

If the installation is partial or broken, run `./armactl` and choose
`Repair Installation`, or use:

```bash
armactl repair
```

Repair re-checks the installation, validates or regenerates missing pieces,
rebuilds service/timer files and refreshes `state.json`.

## Scheduled restarts

In TUI, `Restart Schedule` accepts exact times instead of raw systemd syntax.
Examples:

```text
08:00
08:00, 20:00
08:00 20:00
```

armactl converts those values into the correct `OnCalendar=` entries in the
timer unit automatically.

## Architecture

armactl separates three layers:

| Layer | Location | Purpose |
|-------|----------|---------|
| Source code | This repo | CLI + TUI + modules |
| Runtime data | `~/armactl-data/default/` | Server files, config, backups, state |
| System services | `/etc/systemd/system/` | Auto-start and scheduled restarts |

See [docs/architecture.md](docs/architecture.md) for full details.
See [docs/localization.md](docs/localization.md) for adding and maintaining UI languages.
See [docs/telegram-bot.md](docs/telegram-bot.md) for the planned Telegram bot design.

## File layout on server

```text
~/armactl-data/default/
├── server/                 # Arma Reforger Dedicated Server (SteamCMD)
├── config/config.json      # Server configuration
├── backups/                # Automatic backups before changes
├── state.json              # Instance state for discovery
└── start-armareforger.sh   # Launch script for systemd
```

## CLI commands

```text
armactl detect              # Find existing server
armactl install             # Install server from scratch
armactl repair              # Fix broken installation

armactl start               # Start server
armactl stop                # Stop server
armactl restart             # Restart server
armactl status              # Show server status
armactl logs                # Tail server logs
armactl ports               # Show listening ports

armactl config show         # Show current config
armactl config set-name     # Set server name
armactl config set-scenario # Set scenario ID
armactl config validate     # Validate config

armactl mods list           # List installed mods
armactl mods add            # Add a mod
armactl mods remove         # Remove a mod
armactl mods dedupe         # Remove duplicate mods
armactl mods export FILE    # Export mods to standalone JSON mod pack
armactl mods import FILE    # Import from mod pack JSON or full config.json

armactl schedule show       # Show restart schedule
armactl schedule set        # Set restart schedule
armactl schedule enable     # Enable scheduled restarts
armactl schedule disable    # Disable scheduled restarts
```

## Development

```bash
./scripts/run-host-tests
.venv/bin/pytest
.venv/bin/ruff check src/
```

## Telegram bot design

Telegram bot management is an optional component, not part of the base server
process. The current/runtime model is:

- separate systemd unit: `armactl-bot.service`
- TUI settings screen: `Manage Existing Server -> Telegram Bot`
- instance-scoped `.env` as the single source of truth for bot settings

Runtime config path:

```text
~/armactl-data/<instance>/bot/.env
```

The repository ships `.env.example` as a template, while real `.env` files are
ignored by git.

`./scripts/run-host-tests` will auto-install the dev dependencies if the repo
was only bootstrapped in prod mode before.

For a repo-local smoke run on the Linux host, use:

```bash
./scripts/run-host-tests
```

When launched from the TUI, host test runs are also saved to:

```text
~/armactl-data/<instance>/logs/host-tests-YYYYMMDD-HHMMSS.log
```

## License

MIT
