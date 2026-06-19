# Repeat System Modularity Audit - 2026-06-19

## Branch / Commit Reviewed

- Branch: `feat/web-interface`
- Commit: `7777e2d21c804bd1d2980aa3b974c6cf99ad3ddc`
- Checkout: WSL checkout only, `/home/deus/projects/armactl`
- Audit type: repeat post-refactor system modularity audit after the web/service/player/jobs reliability slices.
- Initial worktree state before rewriting this report: clean, `## feat/web-interface...origin/feat/web-interface`.
- No code changes, commits, or pushes were made. The only intended audit artifact is this report.
- Commands were run from the WSL checkout through `bash.exe -lc`; `wsl.exe` was not used.

## Scope

Reviewed the current implementation, not only documentation, across:

- Entry points: CLI, TUI, web, Telegram bot, web service launcher/installer.
- Web layers: routes, auth/permissions/CSRF, page models, service workflows, filesystem services, jobs, runtime DB, templates.
- Backend/system layers: `service_manager`, installer, repair, logs, ports, discovery, metrics/report paths, platform adapter.
- Persistence/storage: `config.json`, sidecar state, `web.db`, `players.db`, audit log, pending-work fallback, job metadata.
- Jobs/background actions: queueing, active job dedupe, worker claim, duplicate-active maintenance, operator diagnostics.
- Player registry: source collection, identity normalization, SQLite storage, page models, refresh/audit workflow, privacy defaults.
- Tests: route/service/page-model seams, route-internal monkeypatch patterns, job regressions, player registry tests.
- Strategic readiness: banlist, paid gates, roles/tiers, settings registry, Windows backend, file edit/delete, SAT/mod settings.

## Refactor Status Summary

- Facade/page-model split: verified. Routes import focused page models; `src/armactl/web/facade.py` is a compatibility re-export only.
- Filesystem split: verified. Roots, path jail, listing, preview, transfer, upload workflow, and errors are separated; upload uses stage -> audit -> publish -> cleanup.
- Oversized web app tests split: verified. `tests/test_web_app.py` is no longer the route mega-test hub.
- FastAPI route internals patching: verified as fixed. No `__globals__`, `endpoint.__globals__`, `app.router`, or route-module monkeypatch hits were found in tests; remaining monkeypatches target service/page-model/backend seams.
- Service adapter boundary: verified for web service/schedule actions. `ServiceAdapter` exists at `src/armactl/platform/service_adapter.py:17`, Linux/systemd default implementation at `src/armactl/platform/service_adapter.py:74`, and web workflows enter through `src/armactl/web/services/service_actions.py:308` and `src/armactl/web/services/schedule_actions.py:521`.
- Player registry/moderation boundaries: verified. Source collection, identity normalization, storage, page DTOs, and refresh workflow are separate modules.
- Pending restart/work logic: verified as moved into service workflows for config/mod/admin/service/schedule result handling, with fallback-aware clearing at `src/armactl/web/services/pending_work.py:718`.
- Audit-before-mutation reliability: verified for config, mods, admins, service, schedule, player refresh, file upload, and server job enqueue. See the should-fix note for player refresh exception outcomes.
- Duplicate server job worker issue: verified as fixed. `enqueue_server_job_and_start()` starts a worker only when `created` is true at `src/armactl/web/services/server_job_actions.py:76`, active dedupe is in `src/armactl/web/jobs/store.py:393`, and worker claim is atomic at `src/armactl/web/jobs/store.py:541`.

Feature readiness snapshot:

- Banlist: feasible without a web rewrite, but should wait for player DB migrations and reliable-ID ban workflow design.
- Paid feature gates / roles / tiers: not ready as-is; needs a separate policy/entitlement layer and role migrations.
- Settings registry: not present; should be added before SAT/mod settings or runtime feature flags sprawl.
- Windows backend: web service/schedule actions have the first adapter seam; broader service/log/process/install/discovery adapters are still needed.
- File edit/delete: filesystem foundation is ready, but destructive workflows need explicit policy, backups, audit, and confirmations.
- SAT/mod settings: should be added as typed service workflows over a settings/config registry, not as route/template logic.

## Blockers

No current blockers were found in the reviewed snapshot.

The old blockers from the earlier 2026-06-19 report are no longer accurate for this commit:

- Duplicate server job worker start is fixed by created-only worker start plus atomic queued-to-running claim.
- Pending work is no longer route-owned for the reviewed mutating web workflows.
- Player registry storage no longer imports a web moderation DTO.
- Route-global/FastAPI endpoint-internal patching was not found in tests.
- Audit-before-mutation ordering is implemented for the main mutating web workflows.

## Should Fix

### S1. Add explicit migrations for `web.db` and `players.db`

Refs:

- `src/armactl/web/runtime/db.py:11`
- `src/armactl/web/runtime/db.py:32`
- `src/armactl/web/runtime/db.py:252`
- `src/armactl/web/services/player_registry.py:88`

Why it matters:

- `web.db` records `schema_version = 7`, but schema management is still mostly `CREATE TABLE IF NOT EXISTS` plus embedded compatibility moves.
- `players.db` has no schema metadata or migration runner.
- Roles/tiers, feature gates, settings, bans, activity history, SAT settings, and audit exports will be risky on long-lived installs without ordered, testable migrations.

Small next slice:

- Add an idempotent migration runner for `web.db` and `players.db`.
- Add fixture tests from older table shapes to current schema.
- Run maintenance such as duplicate-active job repair only after schema compatibility is established.

### S2. Closed: player refresh audits failed source/storage outcomes

Status update: closed after the repeat audit on `feat/web-interface`.

Refs:

- `src/armactl/web/routes/players.py`
- `src/armactl/web/services/player_actions.py`
- `src/armactl/web/services/player_sources.py`
- `src/armactl/web/services/player_registry.py`
- `tests/test_web_player_registry.py`

Outcome:

- `/players/refresh` remains thin: permission/CSRF in the route, then service workflow and render.
- `refresh_registry_and_audit()` writes intent audit before roster load/persist and aborts without reading or writing `players.db` if intent audit fails.
- Source and registry/persist failures return controlled `PlayerRefreshResult` failures and write `players.refresh` outcome audit entries with safe source/count/reason details.
- Successful backend refresh with failed outcome audit preserves persisted player data and reports the audit problem instead of pretending the backend refresh failed.
- Focused tests cover intent audit failure, success outcome audit, source failure, registry failure, outcome-audit failure after persist, safe audit details, permission/CSRF guards, and no route-global monkeypatch pattern.

### S3. Broaden platform adapters before Windows backend work

Refs:

- `src/armactl/platform/service_adapter.py:17`
- `src/armactl/platform/service_adapter.py:74`
- `src/armactl/service_manager.py:60`
- `src/armactl/service_manager.py:188`
- `src/armactl/service_manager.py:707`
- `src/armactl/logs.py:23`
- `src/armactl/installer.py:139`
- `src/armactl/installer.py:184`
- `src/armactl/ports.py:165`
- `src/armactl/discovery.py:147`
- `src/armactl/cli.py:102`
- `src/armactl/tui/screens.py:84`
- `src/armactl/tui/screens.py:433`
- `src/armactl/telegram_bot.py:22`
- `src/armactl/web/service.py:16`

Why it matters:

- Web service and schedule actions no longer call `service_manager` directly; they use the adapter boundary.
- CLI/TUI/bot and backend modules still directly encode Linux/systemd, `journalctl`, `sudo`, `ufw`, `/proc`/process assumptions, SteamCMD/install behavior, and systemd unit generation.
- A Windows backend can be added as an adapter only if these contracts are widened beyond service/timer control.

Small next slice:

- Define contracts for service control/status, timer/schedule, logs, process metrics, ports/firewall, install/repair/update, and discovery.
- Move current Linux/systemd behavior behind `linux_systemd` implementations without changing operator behavior.
- Keep web routes/templates unaware of platform implementation.

### S4. Add roles/tiers through policy, not routes/templates

Refs:

- `src/armactl/web/auth/permissions.py:51`
- `src/armactl/web/auth/permissions.py:52`
- `src/armactl/web/auth/permissions.py:71`
- `src/armactl/web/runtime/db.py:44`
- `src/armactl/web/auth/users.py:18`
- `src/armactl/web/auth/users.py:88`

Why it matters:

- Current auth is intentionally owner-only: DB CHECK allows only `owner`, and `ROLE_PERMISSIONS` grants all declared permissions to owner.
- That is acceptable for MVP but insufficient for free/basic/premium/mega/deus tiers, delegated operators, or dangerous feature gates.
- If tiers are added directly to route conditionals or templates, business policy will become duplicated and easy to bypass.

Small next slice:

- Add role migrations and a central policy/feature-gate service.
- Keep permissions as capabilities and tiers as entitlements; routes ask policy helpers, templates receive booleans/page-model affordances.
- Add tests proving forbidden actions fail before CSRF/mutation/audit side effects where appropriate.

### S5. Extract neutral workflows before adding features across CLI/TUI/web/bot

Refs:

- `src/armactl/cli.py:102`
- `src/armactl/cli.py:218`
- `src/armactl/cli.py:1155`
- `src/armactl/tui/app.py:22`
- `src/armactl/tui/screens.py:84`
- `src/armactl/telegram_bot.py:22`

Why it matters:

- Core/backend modules did not show problematic imports of UI adapters.
- The remaining risk is feature workflow duplication in UI adapters.
- Banlist, SAT settings, file delete/edit, and Windows behavior should not be implemented separately in four UIs.

Small next slice:

- For the next cross-UI feature, add a neutral workflow service first.
- UI layers should collect input, check adapter-specific auth/permission, call the workflow, and render/report results.

## Nice-to-have

### N1. Keep compatibility shims on a removal clock

Refs:

- `src/armactl/web/facade.py:1`
- `src/armactl/web/services/filesystem.py:4`
- `src/armactl/web/services/pending_restart.py:18`

These wrappers are currently intentional and low-risk. They should get removal criteria once downstream imports and legacy pending-restart compatibility are no longer needed.

### N2. Decide the public filesystem service API before destructive file actions

Refs:

- `src/armactl/web/routes/files.py:181`
- `src/armactl/web/routes/files.py:195`
- `src/armactl/web/services/file_uploads.py:83`

The split is healthy, but future delete/edit/overwrite should have one documented workflow entry point with permission, CSRF, audit, backup/rollback decision, and path-jail guarantees.

### N3. Dashboard remains a broad read-only aggregator

`src/armactl/web/page_models/dashboard.py` is still large, but it is read-only aggregation, not route mutation logic. Split it only when the next dashboard feature makes the boundary uncomfortable.

### N4. Minor cleanup: duplicate DB delete in pending-work clearing

Refs:

- `src/armactl/web/services/pending_work.py:718`

`clear_restart_pending_work_safely()` clears primary pending work and then performs an additional scoped delete while clearing legacy rows. Behavior is safe and fallback clearing is independent, but the double primary delete can be simplified later.

## Strategic Architecture Findings

- The current system is modular enough to resume feature work, provided the next feature slices do not bypass the new service boundaries.
- Web routes are mostly thin: permission/CSRF/form extraction/rendering, then service workflow calls. Raw SQL and platform command leakage were not found in web routes/templates.
- Jobs now have active dedupe, created-only worker start, atomic worker claim, and operator-visible maintenance diagnostics.
- Player registry is now correctly split: source collection in `player_sources`, reliable identity normalization in `player_identity`, storage in `player_registry`, page DTOs in `page_models/players`, and refresh workflow in `player_actions`.
- No IP storage by default was found; tests explicitly assert the player schema does not include `ip_address`.
- The service adapter is a good first seam, but it is not yet a full platform backend abstraction.
- The current `owner`-only permissions model is clean for MVP, but it is not a tier system.
- Broad `except Exception` in reviewed web code is mostly documented fail-closed/degradation/best-effort behavior. Player refresh failure paths now return controlled service results and write failure outcome audit where possible.

## Settings Registry Recommendation

Add an internal typed settings registry before SAT settings, paid feature flags, roles/tiers, ban policies, or runtime UI toggles expand.

Recommended shape:

- Definition layer: `src/armactl/settings/registry.py` with typed keys, scopes, defaults, validation, descriptions for operators, and sensitivity classification.
- Storage layer: `src/armactl/settings/store.py` with SQLite-backed typed values and explicit migrations.
- Scope model: at least `global`, `web`, `instance`, and optionally `user`. Keep user UI preferences separate from server/runtime policy where possible.
- Schema: key, scope_type, scope_id, value type, JSON value, schema/default version, updated_at, updated_by, and maybe source (`default`, `operator`, `migration`).
- Audit: all mutating writes go through service workflows and append intent/outcome audit events.
- Defaults: registry definitions are the source of truth; storage contains overrides only.
- Migrations: registry/store migrations must run before reads that depend on new settings.

Do not store these in the registry in plaintext:

- Session secrets, RCON passwords, Steam credentials, Telegram tokens, API keys, license private keys, recovery tokens, or raw payment/customer secrets.
- Store secret references instead, or keep secrets in private `.env`/secret files with `0600` permissions and redacted display paths.

Also do not duplicate canonical game config in the registry. `config.json` remains the source of truth for game-server config unless a setting is explicitly an armactl runtime policy or UI preference.

## Paid Features / License / Repo Visibility Recommendation

Engineering recommendation:

- Separate entitlements from permissions. `free/basic/premium/mega/deus` should answer “is this installation/customer entitled?”, while permissions answer “may this user perform this capability?”.
- Add a central feature policy service, for example `armactl.policy.features`, with feature keys, minimum entitlement, required permissions, dangerous-action requirements, and audit labels.
- Routes should call policy helpers; templates should receive booleans from page models. Do not branch on tier names in templates or route bodies.
- Dangerous features should require owner plus a high entitlement such as mega/deus, explicit confirmation, CSRF, audit, and possibly local/IP allowlist or deployment-mode checks.

High-risk features that should be owner/mega-or-higher only:

- File delete/edit/overwrite/rename/archive extraction.
- Raw JSON config editing or bulk config import.
- SAT/mod settings that can break server startup or expose private state.
- Install/repair/update jobs, service install/enable/disable, firewall/port changes.
- Backup restore/delete, command/terminal/RCON command send, web exposure/public bind controls.
- User/role management and feature entitlement override controls.

Repo/licensing note, not legal advice:

- If the current code has already been public under MIT, published copies can be forked and reused under those terms. Making the repo private later does not erase that history from anyone who already obtained it.
- Do not commit proprietary premium implementation to a public MIT repo unless you are comfortable with users receiving it under that license.
- For an open-core model, keep the reliable core open and move premium implementations to a private package/repo or server-side SaaS module. Public code can contain stable extension points and entitlement checks, but public premium implementation gates are easy to patch out.
- If premium code must be proprietary, make the repo/private package decision before writing that code, and keep license boundaries explicit in packaging and docs.
- A lawyer should review the licensing plan before commercial launch; the engineering risk is mostly irreversible public distribution and mixed-license confusion.

## Documentation Drift Found

No critical checklist/plan/handoff drift blocker was found in this repeat audit.

Current docs that now match implementation:

- `docs/checklist.md:243-246` marks route-global cleanup and broad web exception audit done; current tests and code match that status.
- `docs/checklist.md:288` documents the web service adapter with CLI/TUI compatibility imports; current code matches.
- `docs/checklist.md:343` documents the player registry foundation; current code matches.
- `docs/web-interface-plan.md:1311-1316` describes facade/filesystem/test/broad-exception cleanup; current code matches.
- `docs/web-interface-handoff.md:95-96` and `docs/web-interface-handoff.md:384-391` warn against route internals and document the adapter compatibility path; current code matches.

Recommended doc edits, but only after follow-up slices:

- Player refresh failure outcome auditing is now reflected in the handoff notes.
- After settings/tiers design, add a small architecture section for `settings` and `policy/features` modules.
- After broader platform adapters, update architecture/handoff to say CLI/TUI/bot no longer use `service_manager` as the compatibility path.

## Suggested Next Slices

1. DB migration runner slice: versioned migrations for `web.db` and `players.db`, with old-schema fixtures.
2. Settings registry design slice: typed registry/store, scopes, defaults, audit, migrations, and secret policy.
3. Feature policy/roles slice: role migration plus entitlement-vs-permission gate helpers.
4. Platform adapter expansion slice: service/logs/process/ports/install/discovery contracts over current Linux implementations.
5. Banlist foundation slice: reliable-ID ban domain/storage/workflow on top of player registry, no nickname/IP inference.
6. Destructive file workflow slice: delete/edit/overwrite with backups, audit, confirmations, owner/mega policy, and tests.
7. SAT/mod settings slice: typed settings/config workflows, not route-owned mutation logic.

## Validation Run

All validation below was run from `/home/deus/projects/armactl` through `bash.exe -lc`; `wsl.exe` was not used.

```bash
git status -sb
```

Result before writing this report:

```text
## feat/web-interface...origin/feat/web-interface
```

```bash
rg -n -e "__globals__" -e "route global" -e "endpoint\.__globals__" -e "service_manager" -e "except Exception" -e "TODO" -e "compatibility" -e "pending_work" -e "players\.db" src/armactl tests docs
```

Result: exit code 0, 636 matching lines. Reviewed categories included expected service-manager/Linux coupling, documented compatibility wrappers, pending-work service usage/tests, players.db references, and broad exception handling. No `__globals__`, `endpoint.__globals__`, or `route global` runtime/test hits requiring action were found.

Additional targeted checks:

```bash
rg -n -e "__globals__" -e "endpoint\.__globals__" -e "app\.router" tests
```

Result: exit code 1, no hits.

```bash
rg -n -e "sqlite3|SELECT |INSERT |UPDATE |DELETE |PRAGMA|CREATE TABLE|DROP TABLE" src/armactl/web/routes src/armactl/web/templates
```

Result: exit code 1, no raw SQL in routes/templates.

```bash
rg -n -e "systemctl|journalctl|sudo|subprocess|service_manager" src/armactl/web/routes src/armactl/web/templates
```

Result: exit code 1, no direct platform/systemd leakage in web routes/templates.

```bash
.venv/bin/ruff check src/armactl/web tests
```

Result:

```text
All checks passed!
```

```bash
ulimit -n 4096 && .venv/bin/python -m pytest -q
```

Result:

```text
716 passed in 149.07s (0:02:29)
```

```bash
git diff --check
```

Result: exit code 0, no output.

```bash
git status -sb
```

Result after writing this report:

```text
## feat/web-interface...origin/feat/web-interface
 M docs/system-modularity-audit-results-20260619.md
```