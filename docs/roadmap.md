# Roadmap

`armactl` is focused on local/free Arma Reforger Dedicated Server management for Ubuntu hosts. The roadmap below keeps the public scope practical for users and contributors.

## Current Scope

- Repo-local launcher and bootstrap.
- Fresh install, existing-server detection, and repair.
- CLI and TUI management flows.
- Config editing, mod management, schedules, logs, status, ports, and telemetry.
- Optional Telegram bot controls.
- Local browser dashboard.
- Public release notes and operator-focused troubleshooting.

## Short-Term Work

- Harden the local web dashboard for production use.
- Polish server update checks and update job UX.
- Expand safe config controls only after field behavior is verified.
- Improve mod add-by-link, import/export, and cleanup workflows.
- Improve player history and moderation workflows with reliable identity rules.
- Improve schedule timezone handling and display.
- Keep public docs concise and user-facing.

## Contributor Priorities

- Reuse backend modules from CLI, TUI, Telegram, and web surfaces.
- Keep route handlers and TUI screens thin.
- Add focused tests when behavior changes.
- Keep runtime data outside the repository checkout.
- Redact secrets in logs, reports, and rendered UI.
- Keep release notes short and operator-focused.
