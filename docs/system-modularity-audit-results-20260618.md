# Baseline System Modularity Audit - 2026-06-18

## Reviewed Snapshot

- Branch: `feat/web-interface`
- Commit: `c3e49d1d20cf6a34f210f7e737daf8c849472928`
- Checkout: WSL only, `/home/deus/projects/armactl`
- Initial worktree state before this report already had docs-only local changes:
  - modified: `docs/architecture.md`, `docs/checklist.md`, `docs/web-interface-handoff.md`, `docs/web-interface-plan.md`
  - untracked: `docs/system-modularity-audit.md`
- This audit intentionally did not modify code and did not commit.
- Checklist update: not changed. Existing `docs/checklist.md:241-246`, `287`, and `331` already track the broad web/system modularity follow-ups; this report is the concrete audit artifact.

## Scope Checked

Required docs read:

- `docs/system-modularity-audit.md`
- `docs/architecture.md`
- `docs/web-interface-plan.md`
- `docs/web-interface-handoff.md`
- `docs/checklist.md`
- `docs/web-system-audit.md`

Code and test areas checked:

- CLI / TUI / web / Telegram bot dependency direction and adapter boundaries.
- Core/backend modules importing UI adapters.
- CLI/TUI/web/bot using each other as API.
- Web route/service/facade split after recent hardening.
- Mutating POST contract: auth, permission, CSRF, audit, pending-work behavior.
- File browser upload/download safety.
- Web jobs enqueue/start/cancel safety.
- Service restart and schedule restart-now pending-work clearing.
- Config/mod/admin/player flows.
- Web runtime DB, schema metadata, audit log, `players.db`.
- Test/CI isolation and route/global monkeypatch risks.
- Docs consistency against `architecture`, `web-interface-plan`, `handoff`, and `checklist`.

## Blockers

### B1. Mutating action audit is still post-action for several workflows

Refs:

- `src/armactl/web/services/config_edit.py:299-330`
- `src/armactl/web/services/mod_actions.py:369-388`, `src/armactl/web/services/mod_actions.py:403-410`
- `src/armactl/web/services/admin_actions.py:275-294`, `src/armactl/web/services/admin_actions.py:308-315`
- `src/armactl/web/services/service_actions.py:213-231`, `src/armactl/web/services/service_actions.py:243-244`
- `src/armactl/web/services/schedule_actions.py:425-448`, `src/armactl/web/services/schedule_actions.py:461-462`
- `src/armactl/web/routes/players.py:100-120`

Risk:

- Config, mod, admin, service, schedule, and player-refresh actions can mutate config files, service state, timers, or `players.db` before the audit event is durably appended.
- If audit append fails, the UI reports an error, but the operator action may already be applied with no durable audit entry. That is an operator-trust and accountability bug, not an accepted risk.
- This is inconsistent with safer current patterns in file upload and server job enqueue: `src/armactl/web/services/file_uploads.py:89-120` audits before publish and cleans staged files; `src/armactl/web/services/server_job_actions.py:86-114` audits before worker start and cancels a newly queued job on audit failure.

Smallest safe refactor slice:

- Add a shared audited-mutation helper/result shape with separate fields for `intent_audited`, `backend_success`, `performed`, `audit_written`, and `operator_success`.
- For mutating workflows that cannot be staged, append an audit intent before mutation and append outcome after mutation. Abort before mutation if intent audit fails.
- Keep route rendering behavior separate from backend outcome so audit failure can be surfaced without losing the real action result.
- Move `/players/refresh` into a service-layer `player_actions.refresh_registry_and_audit()` so the route does not own persistence plus audit.

### B2. Successful restart can leave stale pending work when audit append fails

Refs:

- `src/armactl/web/services/service_actions.py:224-231`
- `src/armactl/web/routes/service.py:155-163`
- `src/armactl/web/services/schedule_actions.py:441-448`
- `src/armactl/web/routes/schedule.py:111-115`

Risk:

- `audit_service_action_result()` and `audit_schedule_action_result()` rewrite `success=False` when audit logging fails, while preserving `performed=True`.
- The service and schedule routes clear restart pending work only when the post-audit `result.success` is true.
- A real backend restart or restart helper start can therefore happen, but pending restart work remains visible as stale unresolved work. That can cause repeated restarts and operator mistrust.

Smallest safe refactor slice:

- Preserve backend success separately from audit success.
- Clear restart pending work when action is restart/restart-now and the backend action succeeded and was performed, even if audit outcome forces the HTTP response to show an audit failure.
- Add regression tests for service restart and schedule restart-now with backend success plus audit failure.

## Should Fix Before Next Feature

### S1. Player registry storage depends on a web DTO and the route owns refresh workflow

Refs:

- `src/armactl/web/services/player_registry.py:14`
- `src/armactl/web/services/player_registry.py:166-181`
- `src/armactl/web/routes/players.py:100-120`
- `docs/web-interface-plan.md:1034-1040`

Risk:

- `player_registry.py` imports `ModerationPlayer` from `web.services.player_moderation`, so the storage layer depends on a web moderation DTO.
- The plan says instance-scoped `players.db` should be usable by future CLI/TUI/bot features, but this dependency makes web the owner of the player domain shape.
- `/players/refresh` performs load-current-players, persistence, and audit directly in the route, which will make player activity, detail pages, and bans harder to add cleanly.

Smallest safe refactor slice:

- Move the observed-player DTO or protocol into a neutral domain/storage module, or make `record_current_players_snapshot()` accept primitive fields.
- Add `web/services/player_actions.py` for refresh plus audit, leaving the route as auth/permission/CSRF/render glue only.
- Add migration/version metadata for `players.db` before adding activity/session/ban tables.

### S2. Runtime DBs do not have a versioned migration runner

Refs:

- `src/armactl/web/runtime/db.py:11`
- `src/armactl/web/runtime/db.py:23-255`
- `tests/test_web_runtime.py:134-161`
- `src/armactl/web/services/player_registry.py:94-132`

Risk:

- `ensure_web_db()` creates missing tables and writes schema version `7`, but the audit found no `ALTER TABLE`, `PRAGMA user_version`, or versioned migration runner.
- Tests cover fresh schema creation and a pending-restart data move, but not upgrade from older persisted `web.db` table shapes.
- `players.db` similarly creates the current foundation tables but has no schema metadata or upgrade path.
- Future roles, player activity, bans, jobs, or settings tables will be risky on long-lived deployments if schema evolution stays implicit.

Smallest safe refactor slice:

- Introduce explicit migration steps for `web.db` and `players.db`, with stored schema version and idempotent upgrade tests from old fixture schemas.
- Keep data migrations separate from table creation and run maintenance, such as duplicate-active-job repair, after schema compatibility is guaranteed.

### S3. Linux/systemd platform coupling is not behind an explicit backend adapter

Refs:

- `src/armactl/service_manager.py:60-74`, `188-210`, `565-580`, `707-879`, `1021-1039`
- `src/armactl/logs.py:23-56`
- `src/armactl/installer.py:147-194`, `276-310`
- `src/armactl/metrics.py:487-578`
- `src/armactl/ports.py:161-179`
- `src/armactl/discovery.py:147-154`
- `docs/web-interface-plan.md:1149-1164`
- `docs/architecture.md:284-286`

Risk:

- Current MVP is intentionally Linux/systemd-first, but service, logs, firewall, metrics, install/update, and discovery assumptions are spread across backend modules.
- A future Windows backend would need to untangle `systemctl`, `journalctl`, `/proc`, `apt-get`, `steamcmd`, `ufw`, sudo helper, and systemd unit parsing from feature logic.

Smallest safe refactor slice:

- Define narrow platform contracts for service control/status, logs, process/metrics, firewall, install/update, and path/service discovery.
- Move the current behavior into a `linux_systemd` adapter without changing user behavior.
- Only after that, add Windows implementations behind the same contracts.
### S4. UI adapters still own too much workflow orchestration

Refs:

- `src/armactl/tui/screens.py:224`, `258`, `434`, `1551`, `1714`, `1759`, `2419`, `2549`, `3076`, `3125`, `3148`, `3165`
- `src/armactl/cli.py:218-311`, `911-936`, `1279-1320`, `1388-1479`
- `src/armactl/telegram_bot.py:1129-1140`, `1261-1341`
- `docs/architecture.md:264-268`

Risk:

- Core/backend modules do not appear to import UI adapters, and web/TUI/bot do not import each other as APIs. That part is healthy.
- But TUI screens, CLI commands, and Telegram handlers still coordinate install/repair/service/schedule/config/mod workflows directly.
- New features such as bans, player activity, SAT edits, and Windows backend support could be implemented four times with slightly different validation, audit, pending-work, and platform behavior.

Smallest safe refactor slice:

- Before adding a feature to more than one UI, extract a neutral workflow service for that feature.
- Move one domain at a time: service lifecycle, schedule, mod/admin changes, then player moderation/bans.
- Keep UI adapters responsible for input collection, display, and permission/auth appropriate to that adapter.

### S5. Oversized web modules and web tests remain known architecture debt

Refs:

- `src/armactl/web/facade.py:9-27`, `538`, `575`, `646`, `710`, `794`, `811` (`915` lines)
- `src/armactl/web/services/filesystem.py:253-258`, `398-426`, `521-555`, `648-795`, `804-835` (`835` lines)
- `tests/test_web_app.py:442-459`, `2111-2141` (`2979` lines)
- `docs/checklist.md:241-242`
- `docs/web-interface-plan.md:1280-1282`

Risk:

- `facade.py` aggregates dashboard, config, mods, admins, bot, SAT, and schedule DTO construction and imports many backend modules.
- `filesystem.py` combines root allowlisting, path jail, listing, preview, download, upload staging, and publish behavior.
- `tests/test_web_app.py` still patches many module globals. This is a test-architecture risk, not a product bug by itself, because runtime code is not monkeypatched.
- Delete/edit/overwrite, SAT settings, or player/bans additions will be harder to review if they land in these modules before the split.

Smallest safe refactor slice:

- Split `facade.py` by page/domain DTOs without changing templates first.
- Split filesystem into roots/path resolution, listing, preview, download, upload staging/publish, and future destructive actions.
- Split web tests into focused route/service/facade/files/jobs modules and prefer patching stable service/facade boundaries before app creation.

### S6. Pending-work fallback clear path depends on primary DB success

Refs:

- `src/armactl/web/services/pending_work.py:454-496`
- `src/armactl/web/services/pending_work.py:580-609`
- `src/armactl/web/services/pending_work.py:663-685`

Risk:

- Pending-work writes can fall back to `pending-work-fallback.json` when normal `web.db` storage fails.
- Listing handles fallback if DB listing fails, but `clear_restart_pending_work()` opens and writes the primary DB before clearing fallback records.
- If fallback was used because the DB path/table is still unhealthy, a successful restart may fail to clear fallback pending work.

Smallest safe refactor slice:

- Make fallback clearing independent from primary DB clearing, and return a structured clear result with DB-cleared count, fallback-cleared count, and clear errors.
- Route handlers should surface a warning if clear partially fails, without losing the fact that restart completed.

## Follow-up

### F1. Keep CLI web imports lazy or move shared constants out of web

Refs:

- `src/armactl/cli.py:18`, `447`, `526-527`, `632-639`, `712-768`

Risk:

- CLI is the top-level dispatcher, so importing web for `armactl web ...` is expected.
- The top-level `DEFAULT_WEB_HOST` import slightly weakens optional web dependency boundaries.

Smallest safe refactor slice:

- Move web CLI defaults to a neutral constants module or import them inside the web command group only.

### F2. CI runs lint/tests/build but no explicit architecture-boundary gate

Refs:

- `.github/workflows/ci.yml:27-34`
- `tests/conftest.py:38-85`

Risk:

- Existing import-safety tests use subprocess isolation for selected packages, which is good.
- There is no broad CI gate that fails on core/backend importing `armactl.web`, `armactl.tui`, or `armactl.cli`.

Smallest safe refactor slice:

- Add a small import-boundary test or script that walks `src/armactl` and enforces adapter direction rules from `docs/system-modularity-audit.md`.

### F3. Compatibility wrappers should have a removal horizon

Refs:

- `src/armactl/web/services/pending_restart.py:1-88`
- `src/armactl/web/runtime/db.py:159-245`

Risk:

- The wrapper and legacy table migration are useful for current compatibility.
- Leaving old shapes indefinitely increases the number of pending-work sources reviewers must reason about.

Smallest safe refactor slice:

- Keep compatibility while supported deployments need it, then document a removal milestone and tests for the final migration window.

## Deferred

- Do not implement Windows backend in this audit slice. The Linux/systemd MVP scope is documented, but platform extraction should happen before Windows work.
- Do not add file delete/edit/overwrite until filesystem service split and destructive-action gates are done.
- Do not add bans/player activity/SAT runtime edits until the player/domain workflow and DB migration findings are addressed.
- UI polish remains behind reliability, operator-trust, storage, and platform-boundary work.

No reliability, security, data-loss, privacy, or operator-trust issue above is accepted as a risk. Deferred items are sequencing decisions only.

## Positive Findings

- Core/backend modules did not show direct imports of web, TUI, or CLI adapters in the precise import-direction grep.
- Web management routes are now split by domain: config, mods, admins, bot, schedule, service, jobs, files, players.
- Config/mod/admin routes are thin enough for current behavior: auth, permission, CSRF, service call, pending-work/render handling.
- File download uses allowlisted roots, relative path normalization, resolved containment, forbidden/system/source-tree checks, attachment filenames, and `application/octet-stream` responses.
- File upload stages bytes, enforces size, validates no overwrite, audits before publish, uses hard-link publish, and cleans staged files.
- Job enqueue uses a controlled action map, `get_or_create_active_job()`, duplicate-active maintenance, audit before worker start, and cancels newly queued jobs if audit fails.
- `web.db`, audit log, pending-work fallback, and `players.db` use private file mode `0600` on creation/ensure paths.
- Tests already isolate saved UI language through `tests/conftest.py:15-19` and run import-safety checks in subprocesses through `tests/conftest.py:38-85`.

## Docs Consistency

- `docs/web-interface-plan.md` is mostly consistent with current code and already tracks route split, facade/filesystem/test split debt, Windows backend as future work, player registry foundation, file manager constraints, and mutating-action guardrails.
- `docs/checklist.md` is consistent with the current known debt at `241-246`, `287`, and `331`.
- `docs/web-interface-handoff.md` is mostly consistent, but `docs/web-interface-handoff.md:362-370` has a misleading `Planned but not implemented` heading immediately followed by implemented `/schedule` behavior.
- `docs/architecture.md` has drift: `docs/architecture.md:83`, `109`, `229`, and `250` still describe web as planned, while the web panel is implemented. `docs/architecture.md:82` and `264` also say TUI has no business logic, while `src/armactl/tui/screens.py` still owns substantial orchestration.

## Check Commands And Results

Read-only checks used during audit:

```bash
wsl.exe --cd /home/deus/projects/armactl -- git rev-parse --abbrev-ref HEAD
# feat/web-interface

wsl.exe --cd /home/deus/projects/armactl -- git rev-parse HEAD
# c3e49d1d20cf6a34f210f7e737daf8c849472928

wsl.exe --cd /home/deus/projects/armactl -- git status -sb
# ## feat/web-interface...origin/feat/web-interface
#  M docs/architecture.md
#  M docs/checklist.md
#  M docs/web-interface-handoff.md
#  M docs/web-interface-plan.md
# ?? docs/system-modularity-audit.md

wsl.exe --cd /home/deus/projects/armactl -- rg -n -e "^(from|import) armactl\.(web|tui|cli)" src/armactl --glob '!src/armactl/web/**' --glob '!src/armactl/tui/**' --glob '!src/armactl/cli.py' --glob '!src/armactl/__main__.py'
# exit 1, no precise core/backend imports of web/tui/cli adapters found

wsl.exe --cd /home/deus/projects/armactl -- wc -l src/armactl/cli.py src/armactl/tui/screens.py src/armactl/web/facade.py src/armactl/web/services/filesystem.py tests/test_web_app.py tests/test_web_jobs.py
# 1583 cli.py, 3231 tui/screens.py, 915 web/facade.py, 835 web/services/filesystem.py, 2979 tests/test_web_app.py, 815 tests/test_web_jobs.py

wsl.exe --cd /home/deus/projects/armactl -- rg -n -e "subprocess" -e "os\.system" -e "systemctl" -e "journalctl" -e "sudo" -e "shell=True" src/armactl
# Found platform/process coupling in service_manager.py, installer.py, logs.py, ports.py, discovery.py, report.py, TUI screens, and web service helpers.

wsl.exe --cd /home/deus/projects/armactl -- rg -n -e "ALTER TABLE|PRAGMA user_version|schema_version|web_schema_meta|migration|migrate" src/armactl/web tests/test_web_runtime.py tests/test_web_jobs.py tests/test_web_auth.py tests/test_web_pending_restart.py
# Found schema metadata and a pending-restart data move; no versioned ALTER/user_version migration runner found.

wsl.exe --cd /home/deus/projects/armactl -- bash -lc "rg -n -F -e 'route.endpoint' -e 'app.router' -e 'dependency_overrides' -e 'monkeypatch.setattr' -e 'sys.modules' tests"
# No route.endpoint/app.router hits in the output; many monkeypatch.setattr hits, concentrated in web tests and service/facade test doubles.
```

Validation requested for this audit slice:

```bash
# Run after writing this report:
wsl.exe --cd /home/deus/projects/armactl -- git status -sb
wsl.exe --cd /home/deus/projects/armactl -- git diff --check
```

Pytest was not run because this audit did not change code.

Final validation results after writing this report:

```bash
wsl.exe --cd /home/deus/projects/armactl -- git status -sb
# ## feat/web-interface...origin/feat/web-interface
#  M docs/architecture.md
#  M docs/checklist.md
#  M docs/web-interface-handoff.md
#  M docs/web-interface-plan.md
# ?? docs/system-modularity-audit-results-20260618.md
# ?? docs/system-modularity-audit.md

wsl.exe --cd /home/deus/projects/armactl -- git diff --check
# exit 0, no output
```
