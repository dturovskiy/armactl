# Web system audit protocol

This checklist is for Arkady or another reviewer to run as a focused system audit around large web-interface changes.

Run it twice for risky slices:

1. Pre-audit before implementation, to confirm current boundaries and known debt.
2. Post-audit after implementation, before merge or production smoke, to confirm no new architectural shortcuts were added.

The audit is not a replacement for unit tests, CI, or VM smoke. It is a module-by-module review of source of truth, security boundaries, operator UX, and test quality.

## Audit scope

Review these areas in order:

- Core backend: paths, discovery, config_manager, service_manager, installer, repair.
- Gameplay management: mods_manager, mods_state, admins_manager, player registry, moderation, future banlist.
- Runtime telemetry: metrics, logs, report, ports, service status, schedule status.
- Web runtime: runtime database, sessions, CSRF, permissions, rate limit, exposure warnings.
- Web routes and services: dashboard, service actions, config, mods, admins, files, logs, jobs, schedule, players.
- Persistence: web.db migrations, players.db, audit.log, pending operator work, background job metadata.
- Deployment: armactl wrapper, armactl-web.service, reverse proxy guidance, HTTPS and external bind warnings.
- Tests and CI: isolation, Python version parity, no route-global monkeypatch dependency, no order-dependent failures.
- Documentation: checklist, handoff, architecture, deployment, and web plan remain consistent.

## Per-module questions

For every module touched by a large slice, answer these questions:

- What is the source of truth for this state?
- Is the route only HTTP glue, with workflow logic in a service module?
- Does the service reuse existing backend modules instead of shelling out to CLI/TUI?
- Does every mutating action have auth, permission, CSRF, audit, and controlled error handling?
- Does risky state change create pending operator work when a server restart is required?
- Is there a backup, rollback, staged write, or explicit no-rollback note where practical?
- Are secrets, tokens, passwords, session IDs, CSRF tokens, and hashes kept out of UI, logs, and JSON DTOs?
- Are errors localized or intentionally operator-facing diagnostics?
- Are broad exception handlers documented as fail-closed or best-effort cleanup?
- Do tests patch stable service/facade seams instead of imported route globals or FastAPI endpoint internals?
- Does the behavior work in the Linux/systemd MVP without mixing in future Windows adapter assumptions?

## Red flags

Treat these as blockers unless there is an explicit documented exception:

- Route code owns workflow, audit, pending-work, rollback, or filesystem publish logic.
- Tests pass only by patching route globals after app creation.
- A mutating POST route has no audit event or no CSRF requirement.
- Browser UI exposes raw shell, arbitrary filesystem access, or dangerous host controls without the future danger-zone gates.
- Public deployment guidance normalizes direct non-TLS port forwarding as production-safe.
- File upload/delete/edit bypasses path jail, symlink checks, size limits, confirmation, audit, or backup rules.
- Player or ban features store IP addresses by default without a separate privacy decision.
- Product tier names grant dangerous capabilities directly instead of mapping to explicit permissions.

## Required output

The reviewer should produce a short audit note with:

- branch and commit hash reviewed;
- whether this was pre-audit or post-audit;
- changed modules reviewed;
- findings grouped as blocker, should-fix, follow-up, or deferred non-blocking item; reliability, security, data-loss, privacy, and operator-trust risks are blockers unless already fixed in the same slice;
- tests and smoke checks run;
- exact docs/checklist updates needed.

If no blockers are found, say that explicitly and list only deferred non-blocking items with an owner/next action. Do not mark reliability, security, data-loss, privacy, or operator-trust issues as accepted risks.
