# Post-cleanup System Modularity Audit - 2026-06-19

## Reviewed Snapshot

- Branch: `feat/web-interface`
- Commit: `f57782a2f0e66736ed9137ad65a6359927d952a1`
- Checkout: WSL only, `/home/deus/projects/armactl`
- Audit type: post-refactor architecture/modularity audit after web hardening and split slices.
- Initial worktree state before writing this report: clean, branch ahead of `origin/feat/web-interface` by 2 commits.
- This audit intentionally did not modify code and did not commit. The only audit artifact is this report.
- `docs/checklist.md` was not edited in this audit. Checklist/docs drift is reported below instead.

## Scope Checked

Required docs read and checked against implementation:

- `docs/system-modularity-audit.md`
- `docs/architecture.md`
- `docs/web-interface-plan.md`
- `docs/web-interface-handoff.md`
- `docs/checklist.md`
- `docs/web-system-audit.md`
- `docs/web-deployment.md`
- Previous audit/fix docs: `docs/system-modularity-audit-results-20260618.md`, `docs/audit-fix-plan.md`

Code/test areas checked:

- Web route/service/page-model/facade boundaries.
- Config, mods, admins, service, schedule, files upload, players refresh, and server job POST contracts.
- Pending operator work creation, fallback storage, restart clearing, and legacy pending-restart cleanup.
- File browser roots, path jail, preview/download, upload staging/publish safety.
- Job store active-job dedupe, duplicate repair, diagnostics, worker dispatch, and server job enqueue flow.
- Web runtime DB schema, audit log usage, `players.db` storage shape.
- Test split after `tests/test_web_app.py`, shared route helpers, route-global monkeypatch patterns.
- CLI/TUI/web/bot dependency direction and platform coupling searches.
- Dangerous future feature gates in docs: raw JSON config, delete/edit/overwrite, terminal/command palette, host controls, banlist, IP allowlist, SAT/mod settings.

## Executive Status

- Blockers found: yes, 1.
- Ready to return fully to the main feature plan: not yet. Fix B1 first because it can run duplicate install/repair workers for one queued server job.
- After B1 is fixed, the web cleanup slices look mostly successful for facade/page-model split, filesystem split, broad mutating-route exception cleanup, audit-before-mutation, pending restart clearing, and test split.
- Several should-fix items remain before adding persistent/player/ban/SAT/destructive-file/Windows-platform features.

## Blockers

### B1. Server job enqueue can start duplicate workers for the same queued active job

Refs:

- `src/armactl/web/services/server_job_actions.py:86-114`
- `src/armactl/web/jobs/server.py:23-38`, `src/armactl/web/jobs/server.py:58-73`, `src/armactl/web/jobs/server.py:152-160`
- `src/armactl/web/jobs/runner.py:134-152`
- `src/armactl/web/jobs/store.py:393-472`, `src/armactl/web/jobs/store.py:541-584`

Why this matters:

- `get_or_create_active_job()` now dedupes queued/running jobs under `BEGIN IMMEDIATE`, and legacy duplicate repair is deterministic. That part is good.
- `enqueue_server_job_and_start()` still calls `start_server_job_worker(db_path, job.id)` for both newly-created jobs and already-existing active jobs.
- `dispatch_job()` checks that the job is queued before calling `mark_job_running()`, but `mark_job_running()` does fetch-then-update and its `UPDATE` only filters `WHERE id = ?`. It does not compare-and-set `WHERE id = ? AND status = 'queued'`, and it does not hold an immediate transaction across the status read and write.
- Two near-simultaneous POSTs to `/jobs/server/install` or `/jobs/server/repair` can both get the same queued job, both start workers, both pass the queued check, and both run the install/repair handler for one job id.
- Install/repair handlers mutate server/runtime/system state. This is a reliability and operator-trust bug, not just test architecture debt.

Smallest safe refactor slice:

- Start a server-job worker only when `created is True`, or make the service explicitly return existing active jobs without starting another worker.
- Also harden `mark_job_running()` as an atomic claim: `UPDATE web_jobs SET status='running' ... WHERE id=? AND status='queued'`, then require `rowcount == 1` before running a handler. Use `BEGIN IMMEDIATE` if needed.
- Add a regression test that dispatches or starts two workers for the same queued job and asserts exactly one handler run and one terminal result.

## Should Fix Before Next Feature

### S1. Pending-work ownership still leaks into routes

Refs:

- `src/armactl/web/routes/_common.py:74-89`
- `src/armactl/web/routes/config.py:126-177`
- `src/armactl/web/routes/mods.py:95-120`
- `src/armactl/web/routes/admins.py:98-123`
- `src/armactl/web/routes/service.py:167-175`
- `src/armactl/web/routes/schedule.py:117-127`

Why this matters:

- The cleanup moved audit-before-mutation into services for config/mod/admin/service/schedule/player flows. That fixed the old audit-order blocker.
- However, pending-work create/clear still happens in route glue. This conflicts with the stated rule that services own workflow, audit, pending-work, rollback, and correction behavior.
- Current routes handle pending failures in a controlled way, so this is not a current product bug by itself. It becomes risky when adding bans, SAT settings, destructive file actions, or more schedule/config flows because each route can re-invent slightly different pending/error semantics.

Smallest safe refactor slice:

- Add workflow service entry points that accept `db_path`, `audit_log_path`, actor, instance, and action input, then return mutation result plus pending-work outcome.
- Move restart pending writes for config/mod/admin and restart pending clearing for service/schedule into those workflow services.
- Keep routes responsible for auth, permission, CSRF, basic form extraction, and rendering only.

### S2. `web.db` and `players.db` still lack explicit versioned migrations

Refs:

- `src/armactl/web/runtime/db.py:11`, `src/armactl/web/runtime/db.py:23-255`
- `src/armactl/web/services/player_registry.py:94-132`

Why this matters:

- `ensure_web_db()` creates tables if missing and writes `schema_version = 7`, but it does not run ordered migrations from old persisted table shapes.
- `players.db` creates current foundation tables but has no schema metadata or migration runner.
- Future users/roles, player activity, bans, SAT settings, audit export, and app settings will be risky on long-lived deployments if schema evolution remains implicit.

Smallest safe refactor slice:

- Introduce an idempotent migration runner for `web.db`, with fixture tests from older schema shapes to current schema.
- Add `players.db` schema metadata and migrations before player sessions/activity/ban tables.
- Run maintenance such as duplicate-active-job repair after schema compatibility is guaranteed.

### S3. Player registry storage still depends on a web moderation DTO

Refs:

- `src/armactl/web/services/player_registry.py:14`
- `src/armactl/web/services/player_registry.py:166-181`
- `src/armactl/web/services/player_actions.py:10`, `src/armactl/web/services/player_actions.py:91-92`

Why this matters:

- `/players/refresh` is now a service-layer workflow with intent/outcome audit, which fixes the old route-owned refresh workflow.
- The storage layer still imports `ModerationPlayer` from `web.services.player_moderation` and expects that web DTO shape.
- Player activity, bans, extra ingestion from logs/RCON/SAT, or CLI/TUI/bot reuse will be harder if `players.db` remains tied to a web page DTO.

Smallest safe refactor slice:

- Move the observed-player input shape into a neutral domain/storage module, or make `record_current_players_snapshot()` accept primitive reliable id, display name, and source records.
- Keep `player_actions.py` as the web workflow adapter over the neutral storage API.

### S4. Route-global monkeypatches remain in tests, while docs say this was removed

Refs:

- `tests/test_web_mod_actions.py:105-108`, `tests/test_web_mod_actions.py:291-293`
- `tests/test_web_admin_actions.py:129-132`, `tests/test_web_admin_actions.py:278-280`
- `tests/test_web_schedule.py:184`, `tests/test_web_schedule.py:549`, `tests/test_web_schedule.py:654`
- `docs/checklist.md:243`
- `docs/web-interface-plan.md:1282`

Why this matters:

- This is test architecture debt, not a product bug. Runtime code is not monkeypatched, and the full suite passes.
- Some tests patch imported route globals after `create_app()`. That is the exact pattern the audit docs call fragile because it relies on FastAPI endpoint functions looking up module globals at request time.
- The docs currently mark the route-global cleanup as done, which overstates the implemented test boundary.

Smallest safe refactor slice:

- Patch stable service/page-model modules before app creation, or add a narrow test app factory seam for page loaders.
- Keep route tests focused on HTTP/auth/CSRF/permission behavior and service result rendering.
- Update checklist/plan wording after the last route-global patches are removed, or explicitly document the few intentional guard-test exceptions.

### S5. Platform adapter work remains before Windows or cross-platform features

Refs:

- `src/armactl/service_manager.py:188-210`, `src/armactl/service_manager.py:707-879`, `src/armactl/service_manager.py:1021-1039`
- `src/armactl/logs.py:35-63`
- `src/armactl/installer.py:135-194`, `src/armactl/installer.py:229-234`
- `src/armactl/ports.py:92-98`, `src/armactl/ports.py:165-179`
- `src/armactl/discovery.py:145-158`, `src/armactl/discovery.py:325-370`
- `src/armactl/web/service.py:5-112`, `src/armactl/web/service.py:171`
- `src/armactl/web/page_models/dashboard.py:8-15`, `src/armactl/web/page_models/dashboard.py:361-380`
- `src/armactl/web/page_models/schedule.py:7`, `src/armactl/web/page_models/schedule.py:18-22`, `src/armactl/web/page_models/schedule.py:45-51`

Why this matters:

- Current Linux/systemd-first scope is documented and acceptable for the web MVP.
- Windows backend work still needs explicit service/log/process/firewall/install/path adapters. Otherwise feature logic will keep growing around `systemctl`, `journalctl`, `/proc`, `sudo`, `apt-get`, SteamCMD, and unit-file parsing.
- No direct `systemctl`/`sudo`/`subprocess` leakage was found in web routes, page templates, or templates. The remaining page-model coupling is read-only status aggregation through backend modules.

Smallest safe refactor slice:

- Before Windows backend work, define service status/control, timer, logs, process metrics, firewall/ports, install/update, and discovery contracts.
- Move current Linux/systemd behavior behind a `linux_systemd` style adapter without changing behavior.
- Then add Windows implementations behind the same contracts.

## Nice-to-have / Follow-up

### N1. Remove `facade.py` compatibility shim after imports are migrated

Refs:

- `src/armactl/web/facade.py:1-29`
- `tests/test_web_facade.py:255`

Notes:

- `facade.py` is now only compatibility re-exports. Routes import `page_models` directly.
- Required facade reference search found only `tests/test_web_facade.py:255`.
- Keep it until downstream/tests no longer need compatibility, then delete with a focused test update.

### N2. Clarify filesystem public API after the split

Refs:

- `src/armactl/web/services/filesystem.py:1-5`
- `src/armactl/web/routes/files.py:25-35`

Notes:

- `filesystem.py` declares itself the public compatibility facade/re-export.
- `routes/files.py` imports focused `filesystem_*` modules directly. Current behavior is safe, but the public API boundary is ambiguous.
- Before file delete/edit/overwrite/archive flows, either route through `services/filesystem.py` or document the focused modules as intentional public service APIs.

### N3. Dashboard page model remains large but no longer a facade hub

Refs:

- `src/armactl/web/page_models/dashboard.py:1-456`

Notes:

- Dashboard DTO building is still broad because the dashboard aggregates many read-only sections.
- It does not import routes and the mutation search found no page-model state mutation.
- Further split can wait until a concrete dashboard feature needs it.

### N4. CLI/TUI/Telegram still contain historical workflow orchestration

Refs:

- `src/armactl/tui/screens.py:232`, `src/armactl/tui/screens.py:266`, `src/armactl/tui/screens.py:3077`
- `src/armactl/cli.py:53-72`, `src/armactl/cli.py:1149-1233`
- `src/armactl/telegram_bot.py:995`, `src/armactl/telegram_bot.py:1029`, `src/armactl/telegram_bot.py:1436`

Notes:

- Core/backend modules did not import UI adapters in the precise import-direction search.
- Web/TUI/CLI/bot do not appear to use each other as API surfaces, except expected CLI launch/import paths for TUI and web commands.
- Before adding one feature across multiple UIs, extract that feature's workflow into a neutral backend/service module first.

## Accepted Architecture Decisions

- Linux/systemd-first web MVP is accepted. Windows support remains future platform-adapter work and is documented that way.
- `src/armactl/web/facade.py` as a temporary compatibility re-export shim is accepted with a removal horizon.
- Broad `except Exception` in web read-only/degradation paths is accepted where documented as fail-closed, fallback, or best-effort cleanup. Mutating route fake-success catches were not found in the reviewed web routes/services.
- File browser scope is accepted as fixed roots, safe browse/preview/download, and upload one new file to the `server` root without overwrite. Delete/edit/overwrite/rename/archive extraction remain gated future flows.
- Pending operator work and background jobs are separate concepts in UI/storage. `/jobs` lists both but does not fake pending restart work as a queued job.
- Login/logout/preferences do not write operator action audit entries by design; they are auth/session/preference state, with CSRF and cookie/session handling documented.

## Remaining Platform Adapter Work

- Service control/status/timer: extract from `service_manager.py` into a narrow platform contract.
- Logs/report diagnostics: separate Linux `journalctl` and fixed command diagnostics from future Windows Event Log/file-log providers.
- Process/metrics/ports/firewall: separate `/proc`, `ss`, `ufw`, and subprocess assumptions before Windows support.
- Install/update/repair: separate `apt-get`, SteamCMD, sudo helper, and systemd unit generation from cross-platform workflow decisions.
- Web production service: `src/armactl/web/service.py` is intentionally systemd-specific now; keep it out of non-Linux feature assumptions.

## Docs Consistency

- `docs/architecture.md`: broadly consistent. It documents source/runtime/log/system-service separation and Linux/systemd unit ownership. No contradiction found for current web MVP.
- `docs/checklist.md`: partially inconsistent. `docs/checklist.md:243` marks route-global/FastAPI endpoint patch dependency as removed, but route-global monkeypatches remain in focused route tests. The repeat audit checklist item remains unchecked; this report is the artifact, but the checklist was not edited in this audit.
- `docs/web-interface-plan.md`: mostly consistent. It correctly marks facade/filesystem/test split as done and dangerous feature gates as future work. It overstates the test split at `docs/web-interface-plan.md:1282` because some route-global patches remain. It also says the filesystem split has a thin compatibility facade; actual file routes import focused internals directly, which is a minor API-boundary ambiguity.
- `docs/web-interface-handoff.md`: mostly consistent with implementation and current guardrails. It documents current POST inventory, broad exception inventory, dangerous feature gates, and future work. It does not mention the duplicate worker race found in B1.
- `docs/web-deployment.md`: consistent. It keeps the safe deployment baseline as localhost plus HTTPS reverse proxy, warns against direct public HTTP, and documents app-level IP allowlist/trusted proxy handling as future work.
- Existing audit docs: the 2026-06-18 blockers for audit-before-mutation and restart pending clearing are fixed in current code. The old large-module cleanup findings are mostly fixed. The old migration/platform/player-domain/test-boundary follow-ups remain partly open.

## Validation Commands and Results

All commands were run from the WSL checkout via `bash.exe -lc "cd /home/deus/projects/armactl && ..."`, without `wsl.exe`.

```bash
git status -sb
```

Result:

```text
## feat/web-interface...origin/feat/web-interface [ahead 2]
```

```bash
rg -n "armactl.web.facade|from \.facade|import facade" src tests
```

Result:

```text
tests/test_web_facade.py:255:        "armactl.web.facade",
```

```bash
rg -n "except Exception|BaseException|pass  #|TODO|FIXME|workaround|compatibility|legacy|shim|route global|monkeypatch\.setattr" src/armactl tests docs
```

Result:

- Exit code: 0.
- Output size: 522 matching lines.
- Relevant categories reviewed: documented broad catches, compatibility shims, legacy migration references, and many test `monkeypatch.setattr` calls.
- Key audit-relevant hits included route-global patches in `tests/test_web_mod_actions.py`, `tests/test_web_admin_actions.py`, and `tests/test_web_schedule.py`; broad web catches documented as degradation/fail-closed/best-effort; `src/armactl/web/facade.py` and `src/armactl/web/services/filesystem.py` compatibility re-exports.

```bash
rg -n "systemctl|systemd|Windows|platform|adapter|sudo|subprocess" src/armactl docs
```

Result:

- Exit code: 0.
- Output size: 476 matching lines.
- Relevant categories reviewed: expected Linux/systemd MVP coupling in `service_manager.py`, `logs.py`, `installer.py`, `ports.py`, `discovery.py`, `web/service.py`, CLI/TUI historical flows, and docs that explicitly gate Windows/platform work.
- Additional precise route/page/template platform search found no direct `systemctl`, `sudo`, `subprocess`, or `journalctl` in `src/armactl/web/routes`, `src/armactl/web/page_models`, or `src/armactl/web/templates`.

```bash
.venv/bin/ruff check src/armactl/web tests
```

Result:

```text
All checks passed!
```

```bash
git diff --check
```

Result: exit code 0, no output.

```bash
ulimit -n 4096 && .venv/bin/python -m pytest -q
```

Result:

```text
703 passed in 110.17s (0:01:50)
```

Additional read-only checks run for audit context:

```bash
rg -n "from armactl\.(web|tui|cli)|import armactl\.(web|tui|cli)" src/armactl --glob '!src/armactl/web/**' --glob '!src/armactl/tui/**' --glob '!src/armactl/cli.py' --glob '!src/armactl/__main__.py'
```

Result: exit code 1, no core/backend imports of web/tui/cli adapters found.

```bash
rg -n "@router\.(post|put|patch|delete)" src/armactl/web/routes
```

Result: POST routes only; no registered PUT/PATCH/DELETE routes found.

```bash
rg -n "systemctl|sudo|subprocess|journalctl" src/armactl/web/routes src/armactl/web/page_models src/armactl/web/templates
```

Result: exit code 1, no direct platform command leakage in web routes/page models/templates.

## Draft Prompt For Arnold

Do not execute this prompt as part of the audit.

```text
Арнольде, працюй тільки у WSL checkout `/home/deus/projects/armactl` на `feat/web-interface`. Windows checkout не чіпай, `wsl.exe` не використовуй, commit не роби.

Задача: виправити blocker з `docs/system-modularity-audit-results-20260619.md` B1.

Scope:
- Harden web server job enqueue/dispatch so duplicate POSTs cannot run two install/repair workers for the same queued active job.
- In `server_job_actions.enqueue_server_job_and_start()`, do not start a new worker for an already-existing active job unless there is an explicit safe reason.
- In `jobs/store.mark_job_running()`, make the queued-to-running claim atomic with compare-and-set semantics (`WHERE id=? AND status='queued'` plus rowcount check, and transaction locking if needed).
- Preserve existing audit-before-worker-start behavior: newly-created jobs must be audited before the worker starts, and if audit fails, the new job is cancelled best-effort.
- Add focused regression tests proving two concurrent dispatch/start attempts for one queued job run the handler exactly once.

Validation:
- `git status -sb`
- `.venv/bin/ruff check src/armactl/web tests`
- targeted jobs tests, including the new regression
- `ulimit -n 4096 && .venv/bin/python -m pytest -q`
- `git diff --check`

Do not broaden into DB migration, player registry, filesystem facade, route pending-work ownership, or test architecture cleanup in this slice.
```
