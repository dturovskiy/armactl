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
./scripts/run-host-tests -- tests/test_mods.py
```

`scripts/run-host-tests` owns the test/lint workflow. It bootstraps the
repo-local dev environment by default, runs pytest, and then runs ruff.

When working from an SFTP-mounted checkout or another environment where the
repo-local `.venv/bin/python` is not runnable on the local host, provide an
explicit Python runner:

```bash
ARMACTL_TEST_PYTHON=python3 PYTHONPATH=/path/to/deps:src ./scripts/run-host-tests -- tests/test_mods.py
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
