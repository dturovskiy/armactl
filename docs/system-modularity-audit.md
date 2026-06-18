# System modularity audit protocol

This checklist is for Arkady or another reviewer to run as a whole-project architecture audit before we add another large feature family or platform backend.

This is broader than `docs/web-system-audit.md`. The web audit checks whether a web slice respected the existing boundaries. This system audit checks whether the whole `armactl` codebase still has healthy boundaries for future features such as player moderation, banlists, Windows support, premium/danger-zone controls, file editing/deletion, SAT/mod settings, and update jobs.

Run it in two modes:

1. Baseline audit before a major refactor or feature family, to identify real blockers and avoid guessing.
2. Post-refactor audit after architecture cleanup, to confirm the cleanup did not create new hidden coupling or test-only shortcuts.

The audit is not a request to rewrite everything. It should separate blockers from acceptable debt, and it should produce small, ordered refactor slices.

## Audit Scope

Review these layers in order:

- Entry points: CLI, TUI, web, Telegram bot, service templates, scripts, and generated commands.
- Domain/backend modules: paths, discovery, config_manager, service_manager, installer, repair, mods_manager, mods_state, admins_manager, metrics, logs, report, cleaner, integrity, player registry, and future bans.
- Workflow services: install/repair/update, start/stop/restart, config save, mod/admin changes, file upload/download, pending operator work, background jobs, audit logging, and schedule actions.
- Platform adapters: Linux/systemd helpers, filesystem adapters, SteamCMD/Workshop, RCON/A2S/SAT, Telegram, reverse proxy/deployment assumptions, and future Windows backend seams.
- Persistence: `config.json`, sidecar state files, `web.db`, `players.db`, audit logs, backups, pending-work fallback, job metadata, and migration helpers.
- UI adapters: TUI screens, web routes/templates/static JS, CLI commands, and bot handlers.
- Tests and CI: unit vs integration boundaries, route/service monkeypatching, Python-version parity, order independence, packaging/static data coverage, and VM smoke coverage.
- Documentation: architecture, checklist, web plan, handoff, deployment, release process, and audit docs.

## Boundary Rules

Use these rules to judge whether a module is healthy:

- Core/domain modules must not import web, TUI, bot, or CLI adapters.
- UI adapters must not own business rules, persistence transactions, rollback logic, or shell command orchestration.
- CLI/TUI/web/bot should call shared backend modules or workflow services directly; they should not call each other as APIs.
- Workflow services own multi-step behavior: validation, audit, pending-work, backup, rollback/correction, and adapter calls.
- Low-level adapters stay pure: filesystem adapters do path/root operations, platform adapters do systemd/process work, storage adapters do database operations.
- Platform-specific assumptions must sit behind explicit adapters, not leak into feature logic that future Windows support will need to reuse.
- State source of truth must be explicit. Do not let UI cache, template data, or test monkeypatches become hidden truth.
- Dangerous features require explicit permissions and policy gates; product tiers such as `mega` are eligibility labels, not direct capabilities.
- Broad exception handling must be documented as fail-closed, degradation, fallback, or best-effort cleanup with tests.
- Tests patch stable seams and public service APIs, not imported route globals or FastAPI endpoint internals.

## Suggested Searches

Use `rg` and source reading, not only grep counts. These searches are starting points:

```bash
rg -n "TODO|FIXME|HACK|workaround|temporary|legacy|compatibility|best-effort|except Exception" src tests docs
rg -n "subprocess|os\.system|systemctl|sudo|sshpass|shell=True" src scripts tests
rg -n "from armactl\.web|from armactl\.tui|from armactl\.cli|import armactl\.web|import armactl\.tui|import armactl\.cli" src/armactl
rg -n "DEFAULT_INSTANCE_NAME|default\)|instance=\"default\"|web\.db|players\.db|sidecar|fallback" src/armactl tests docs
rg -n "monkeypatch\.setattr|route global|endpoint|create_app|app\.router|dependency_overrides" tests
rg -n "config\.json|admins-state|mods-state|pending-work|backup|audit" src/armactl/web src/armactl tests
```

When a search finds something, classify it. Some compatibility wrappers and best-effort cleanup paths are legitimate if documented and tested. Hidden coupling, fake success states, or test-only behavior are not legitimate.

## Red Flags

Treat these as blockers unless fixed in the same slice:

- Core module imports UI adapter code.
- Web/TUI/CLI route or screen owns workflow logic that should live in a service/backend module.
- A feature shells out to `armactl` CLI or TUI when a backend module exists.
- A platform-specific command is embedded in feature logic instead of a platform adapter.
- Mutating behavior lacks an explicit source of truth, audit policy, backup/rollback decision, or pending-work behavior when restart/apply is required.
- Tests only pass by patching imported route globals after app creation or by relying on test order.
- New files or sidecars are created without permissions, ownership, migration, cleanup, or documentation.
- Player/moderation features infer identities from nicknames or store IPs by default.
- Dangerous features bypass explicit permissions, confirmation, CSRF, audit, and owner/mega/IP-allowlist policy.
- A broad `except Exception` hides failed mutations or presents a fake success state.

## Output Format

The reviewer should produce `docs/system-modularity-audit-results-YYYYMMDD.md` or a handoff note with:

- branch and commit hash reviewed;
- whether this is baseline or post-refactor audit;
- modules and docs reviewed;
- findings grouped as `Blocker`, `Should fix before next feature`, `Follow-up`, and `Deferred/non-blocking`;
- exact file/line references;
- why each issue matters for reliability, security, data loss, privacy, operator trust, or future feature velocity;
- suggested smallest safe refactor slice;
- tests, commands, and smoke checks run;
- docs/checklist updates needed;
- explicit statement if no blockers were found.

Reliability, security, data-loss, privacy, and operator-trust issues are blockers until fixed. Do not normalize them as accepted risks.

## Expected Backlog Shape

The audit should produce an ordered backlog, not one giant rewrite:

1. Fix blockers that can corrupt state, hide failed mutations, weaken auth/audit, or break production operators.
2. Split oversized boundary modules that block immediate planned work, such as web DTO facades, filesystem operations, or test fixtures.
3. Extract platform adapters before Windows backend work.
4. Extract player/moderation/banlist domain services before ban actions.
5. Extract file edit/delete/overwrite workflows before exposing destructive file manager actions.
6. Move cosmetic UI polish after the relevant state and safety boundaries are stable.

## Do Not Do During The Audit

- Do not rewrite large modules just because they are large.
- Do not mix feature work with the audit unless fixing a blocker is explicitly requested.
- Do not create new framework abstractions without showing which current coupling they remove.
- Do not touch release files unless this is a release slice.
- Do not use the Windows checkout for WSL-only audit work.
