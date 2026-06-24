# Architecture

`armactl` is a local management tool for one Arma Reforger Dedicated Server installation per Linux user. The core design keeps source code, runtime state, generated services, and operator interfaces separate.

## Layout

| Layer | Location | Purpose |
| --- | --- | --- |
| Source code | repository checkout | CLI, TUI, web package, templates, tests |
| Runtime data | `~/armactl-data/<instance>/` | server files, config, backups, state |
| Web runtime | `~/armactl-data/web/` | dashboard users, sessions, jobs, pending work |
| Logs | `~/armactl-data/logs/` | armactl logs and web action records |
| System services | `/etc/systemd/system/` | game server, restart timer, bot, dashboard |

Typical instance data:

```text
~/armactl-data/default/
├── server/
├── config/config.json
├── backups/
├── state.json
└── start-armareforger.sh
```

## Repository Structure

```text
src/armactl/
├── cli.py                 # command-line entry point
├── tui/                   # Textual interface
├── web/                   # local browser dashboard
├── config_manager.py      # config load/save/validation helpers
├── server_config_schema.py# supported config field registry/defaults
├── service_manager.py     # Linux/systemd service helpers
├── mods_manager.py        # mod list workflows
├── admins_manager.py      # game admin workflows
├── repair.py              # repair orchestration
└── platform/              # adapter seams for service operations
```

Templates live in `templates/`. Tests live in `tests/`. Public documentation lives in `docs/`.

## Adapter Model

CLI, TUI, Telegram bot, and web dashboard are adapters over shared backend modules. They should not call each other as APIs.

- CLI parses commands and delegates to backend modules.
- TUI owns terminal layout and user interaction.
- Telegram bot owns chat commands and callbacks.
- Web routes own HTTP/session/form handling.
- Backend modules own validation, file operations, service operations, and state changes.

This keeps the same install, repair, config, mods, schedule, and service behavior available from multiple interfaces.

## Configuration Source Of Truth

The runtime server config is:

```text
~/armactl-data/<instance>/config/config.json
```

Generated defaults come from `src/armactl/server_config_schema.py` and `templates/config.json.j2`. The schema registry records supported paths, default values, validation metadata, restart behavior, and UI metadata for fields managed by armactl.

The public web dashboard edits only selected non-secret config fields. Broader or sensitive config changes remain CLI/TUI/operator workflows until they have dedicated validation and recovery behavior.

## Service Model

Generated services include:

- `armareforger.service`
- `armareforger-restart.timer`
- `armactl-web.service`
- `armactl-bot.service`

`service_manager.py` owns Linux/systemd operations. Web service actions go through the platform service adapter so route handlers stay thin and tests can patch a stable seam.

## Web Dashboard Boundaries

The web dashboard is a local server-management interface. Its main package structure is:

```text
src/armactl/web/
├── routes/       # HTTP glue, auth/session/form handling
├── services/     # workflow services and state-changing operations
├── page_models/  # read models for templates
├── jobs/         # background job metadata and runner
├── runtime/      # web runtime paths and database setup
├── auth/         # users, sessions, CSRF, permissions
├── templates/    # server-rendered HTML
└── static/       # CSS, JS, images
```

Route handlers should stay small. Multi-step work belongs in service modules. Templates render already-prepared state.

## Web Runtime Data

The dashboard stores runtime state under `~/armactl-data/web/`, including users, sessions, jobs, pending work, and preferences. Player registry data is instance-scoped under `~/armactl-data/<instance>/players.db`.

Mutating web actions use POST plus CSRF checks, permission checks, controlled errors, backups or pending work where applicable, and web action records that avoid storing secrets.

## Data Flow Examples

Install or repair:

```text
operator -> CLI/TUI/web job -> backend install/repair helpers -> runtime files -> systemd services
```

Config save:

```text
operator -> config UI -> config service -> config_manager -> backup + config.json -> pending restart when needed
```

Server status:

```text
operator -> CLI/TUI/web -> service/status helpers -> systemd + runtime logs + config summary
```
