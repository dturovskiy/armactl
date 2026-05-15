# Development

## Local setup

Recommended development bootstrap:

```bash
ARMACTL_BOOTSTRAP_MODE=--dev ./armactl
```

This keeps the project self-contained and uses the same repo-local launcher
model as the main product.

## Test and lint commands

```bash
./scripts/run-host-tests
.venv/bin/pytest
.venv/bin/ruff check src tests
```

## Server FPS telemetry development notes

Server FPS metrics are parsed from Arma Reforger's `-logStats` console output.

When changing this area, cover at least:

- latest log directory selection
- valid `FPS:` line parsing
- malformed telemetry lines
- missing logs
- stale telemetry
- CLI/TUI/Telegram rendering
- generated `start-armareforger.sh` arguments

Manual smoke check on a live server:

```bash
pgrep -af ArmaReforgerServer
grep -RiaE 'FPS:|frame time' ~/armactl-data/default/config/logs | tail -20
./armactl status
```

## Project structure

- `src/armactl/` — backend modules, CLI, TUI, Telegram bot
- `templates/` — config, systemd, and helper templates
- `tests/` — unit and integration-style coverage
- `docs/` — architecture, migration, localization, troubleshooting, release docs

## Design principles

- Keep TUI screens thin and delegate logic to backend modules.
- Prefer explicit state and file layout over hidden magic.
- Treat runtime data and repo code as separate layers.
- Redact secrets in logs and UI output by default.

See [architecture.md](architecture.md) for more detail.

