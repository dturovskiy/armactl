# Development

## Local setup

Recommended development bootstrap:

```bash
ARMACTL_BOOTSTRAP_MODE=--dev ./armactl
```

This keeps the project self-contained and uses the same repo-local launcher
model as the main product.

## Windows + WSL workflow

For Windows development, prefer the Linux checkout under WSL for real project
work. The app targets Ubuntu hosts, systemd, SteamCMD, and Linux paths, so tests
and commits should come from the WSL checkout rather than a second Windows copy
of the repository.

Keep the Windows checkout clean unless it is intentionally being used for a
separate local experiment. Do not split related changes across both checkouts.

## Test and lint commands

```bash
./scripts/run-host-tests
./scripts/run-host-tests -- tests/test_mods.py
```

`scripts/run-host-tests` owns the test/lint workflow. It bootstraps the
repo-local dev environment by default, runs pytest, and then runs ruff.

Tests are isolated from the operator's saved UI language. The test suite forces
English as the active language so local Ukrainian UI settings do not change test
expectations.

Web route tests use FastAPI/Starlette `TestClient`. Keep the dev environment
installed with the declared dev dependencies, including `httpx2`, so Starlette
does not fall back to the deprecated `httpx` test-client path. Do not silence
that deprecation warning with a pytest filter; fix the test dependency instead.

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
- ServerAdminTools admin guard behavior before start/restart

Manual smoke check on a live server:

```bash
pgrep -af ArmaReforgerServer
grep -RiaE 'FPS:|frame time' ~/armactl-data/default/config/logs | tail -20
./armactl status
```

## Project structure

- `src/armactl/` — backend modules, CLI, TUI, Telegram bot, and planned web code
- `src/armactl/web/` — planned browser management panel package
- `templates/` — config, systemd, and helper templates
- `website/` — separate marketing/static site, not the management panel
- `tests/` — unit and integration-style coverage
- `docs/` — architecture, migration, localization, troubleshooting, release docs

## Design principles

- Keep TUI screens and web route handlers thin and delegate logic to backend
  modules.
- Prefer explicit state and file layout over hidden magic.
- Treat runtime data and repo code as separate layers.
- Redact secrets in logs and UI output by default.

## Web panel development notes

The planned web interface is documented in
[web-interface-plan.md](web-interface-plan.md). Until the implementation exists,
do not add browser-only behavior that bypasses the existing backend modules.

For multi-chat implementation/review work, use
[web-interface-handoff.md](web-interface-handoff.md) as the prompt and review
checklist source.

Web changes should include focused tests for auth/session behavior, CSRF on
mutating routes, service-control authorization, and filesystem containment.
Manual VM smoke checks should cover local binding, reverse proxy routing, and
the default web port not conflicting with Arma game/A2S/RCON ports.

See [architecture.md](architecture.md) for more detail.
