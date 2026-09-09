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

## Completed On The Current Web Branch

- Production dashboard hardening, bounded readiness checks, stale-job metadata recovery, and current VM smoke.
- Server update checks and update-job UX with controlled retry/failure guidance.
- The current safe config field set, guarded raw config editing, and narrow safe config/profile file editing.
- Mod add/bulk/import/export/dedupe flows, bounded cleanup manifests, and shared mutation recovery for current mod/profile cleanup paths.
- Player history, supervised log/session pipelines, current-session truth guards, session search/detail UI, and read-only native ban-list viewing.
- Browser-local schedule timezone input/display with UTC backend normalization.
- Operator-critical TUI/Web parity classification; full feature-for-feature parity is intentionally not a goal.

## Remaining Planned Work

The ordered and decision-gated public backlog is maintained only in
[checklist.md](checklist.md). It includes stabilization of the current
update/profile/incident/admin baseline, a renewed operator-critical CLI/TUI/Web
parity audit, native moderation completion, remaining diagnostics, the complete
public-main preparation gate, and post-merge release work.

Detailed contracts define behavior but do not maintain competing progress
lists. Private/commercial dashboard planning remains outside this public
roadmap.

## Contributor Priorities

- Reuse backend modules from CLI, TUI, Telegram, and web surfaces.
- Keep route handlers and TUI screens thin.
- Add focused tests when behavior changes.
- Keep runtime data outside the repository checkout.
- Redact secrets in logs, reports, and rendered UI.
- Keep release notes short and operator-focused.
