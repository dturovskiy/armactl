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

