# armactl

Open-source installer, manager and TUI for **Arma Reforger Dedicated Server** on Linux.

## What it does

- **Install** a server from scratch via SteamCMD
- **Detect** an already existing server installation
- **Manage** server: start, stop, restart, status, logs, ports
- **Edit** `config.json` safely — no manual JSON editing
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
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh
armactl --help
```

The bootstrap script installs system dependencies (Python 3.10+, venv, pip),
creates a `.venv`, installs armactl in editable mode, and places a launcher
in `~/.local/bin/armactl`.

## Architecture

armactl separates three layers:

| Layer | Location | Purpose |
|-------|----------|---------|
| Source code | This repo | CLI + TUI + modules |
| Runtime data | `~/armactl-data/default/` | Server files, config, backups, state |
| System services | `/etc/systemd/system/` | Auto-start and scheduled restarts |

See [docs/architecture.md](docs/architecture.md) for full details.

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

armactl schedule show       # Show restart schedule
armactl schedule set        # Set restart schedule
armactl schedule enable     # Enable scheduled restarts
armactl schedule disable    # Disable scheduled restarts
```

## Development

```bash
./scripts/bootstrap.sh --dev
pytest
ruff check src/
```

## License

MIT
