# Final web foundation audit results - 2026-06-20

## Commit / branch reviewed

- Checkout reviewed: `/home/deus/projects/armactl`
- Branch: `feat/web-interface`
- Commit: `8cb7dff7e2a2360ca647eb2acecbd0233de06d5a`
- Starting status: clean, tracking `origin/feat/web-interface`
- Scope: audit-only. Product code was not changed. This report is the only intended file change.

## Validation commands and exact results

```bash
$ git status -sb
## feat/web-interface...origin/feat/web-interface
```

```bash
$ rg -n "__globals__|route global|already-imported router|endpoint.__globals__|order-dependent|Bad Request" src tests docs || true
docs/checklist.md:247:- [x] Розбити oversized `tests/test_web_app.py` на focused web test modules і прибрати залежність від patching route globals / imported FastAPI endpoint internals
docs/checklist.md:248:- [x] Прибрати залишковий web test debt з monkeypatch route internals: web tests патчать stable service/page_model/adapter seams, не FastAPI route globals або endpoint internals
docs/system-modularity-audit.md:40:- Tests patch stable seams and public service APIs, not imported route globals or FastAPI endpoint internals.
docs/system-modularity-audit.md:51:rg -n "monkeypatch\.setattr|route global|endpoint|create_app|app\.router|dependency_overrides" tests
docs/system-modularity-audit.md:66:- Tests only pass by patching imported route globals after app creation or by relying on test order.
docs/documentation-audit-results-20260619.md:278:rg -n "planned|not implemented|future|TODO|route global|player_moderation|facade|filesystem.py|service_manager|MIT|premium|paid|private|Windows|registry" docs README.md website src/armactl/web
docs/documentation-audit-results-20260619.md:292:rg -n "__globals__|route global|endpoint\.__globals__|app\.router\.routes|dependency_overrides|monkeypatch\.setattr\([^\n]*(routes|endpoint|__globals__)" tests
docs/web-interface-plan.md:1498:endpoint `__globals__`, route function globals, app router endpoint internals,
docs/web-interface-plan.md:1540:- Done: split the oversized `tests/test_web_app.py` into focused app wiring, auth route, preference, dashboard, management layout, jobs route, and service route test modules. Touched tests patch stable service/page-model seams instead of imported route globals or FastAPI endpoint internals.
docs/web-interface-plan.md:2003:  FastAPI route globals, endpoint internals, or systemd internals.
docs/web-system-audit.md:23:- Tests and CI: isolation, Python version parity, no route-global monkeypatch dependency, no order-dependent failures.
docs/web-system-audit.md:39:- Do tests patch stable service/facade seams instead of imported route globals or FastAPI endpoint internals?
docs/web-system-audit.md:47:- Tests pass only by patching route globals after app creation.
docs/system-modularity-audit-results-20260619.md:31:- FastAPI route internals patching: verified as fixed. No `__globals__`, `endpoint.__globals__`, `app.router`, or route-module monkeypatch hits were found in tests; remaining monkeypatches target service/page-model/backend seams.
docs/system-modularity-audit-results-20260619.md:314:rg -n -e "__globals__" -e "route global" -e "endpoint\.__globals__" -e "service_manager" -e "except Exception" -e "TODO" -e "compatibility" -e "pending_work" -e "players\.db" src/armactl tests docs
docs/system-modularity-audit-results-20260619.md:317:Result: exit code 0, 636 matching lines. Reviewed categories included expected service-manager/Linux coupling, documented compatibility wrappers, pending-work service usage/tests, players.db references, and broad exception handling. No `__globals__`, `endpoint.__globals__`, or `route global` runtime/test hits requiring action were found.
docs/system-modularity-audit-results-20260619.md:322:rg -n -e "__globals__" -e "endpoint\.__globals__" -e "app\.router" tests
docs/web-interface-handoff.md:101:  for import safety. Do not monkeypatch FastAPI route globals, endpoint
docs/web-interface-handoff.md:102:  `__globals__`, or app router endpoint internals.
docs/web-interface-handoff.md:484:    page-model, and adapter seams rather than imported route globals or FastAPI
```

Result: exit code 0. Hits are documentation/history/checklist references only; no `src/` or `tests/` runtime/test route-global monkeypatch hits were found.

```bash
$ rg -n "web panel|web interface|planned|future|implemented|paid|premium|tier|permission|role|allowlist|break-glass|settings registry|Windows|adapter|server update|crossplay|third-person|timezone" README.md docs
```

Result: exit code 0, 721 matching lines. The required command is intentionally broad. Current-state docs were reviewed from those hits; drift is listed under Documentation drift below.

```bash
$ .venv/bin/ruff check src/armactl/web tests
All checks passed!
```

```bash
$ git diff --check
```

Result: exit code 0, no output.

```bash
$ ulimit -n 4096 && .venv/bin/python -m pytest -q
........................................................................ [  9%]
........................................................................ [ 19%]
........................................................................ [ 29%]
........................................................................ [ 39%]
........................................................................ [ 49%]
........................................................................ [ 59%]
........................................................................ [ 69%]
........................................................................ [ 79%]
........................................................................ [ 89%]
........................................................................ [ 99%]
.......                                                                  [100%]
727 passed in 106.68s (0:01:46)
```

## Blockers

None found.

No reliability, data-loss, traceback/secrets exposure, duplicate-worker, pending-work resurrection, or route-global test blocker was found in this pass.

## Should-fix before returning to feature plan

1. Normalize audit phases for job enqueue and file upload workflows.
   - `src/armactl/web/services/server_job_actions.py` creates or repairs active `web_jobs` state before writing an audit event. It starts the worker only after audit succeeds and cancels a newly-created job on audit failure, so the behavior is controlled, but it is not the clean `intent audit -> mutation/enqueue -> outcome audit` contract used by the other mutating workflows.
   - `src/armactl/web/services/file_uploads.py` stages bytes, writes one `file.upload` event before final publish, and writes `file.upload.publish-failed` only if publish fails. A successful publish does not get a distinct outcome event.
   - Fix should preserve atomic install/repair dedupe, no duplicate worker start, upload no-overwrite behavior, and controlled audit-failure rendering.

2. Move dashboard service/timer reads behind the platform adapter.
   - `src/armactl/web/page_models/dashboard.py` still imports `service_manager` and calls `get_service_status()` / `get_timer_status()` directly.
   - `src/armactl/web/page_models/schedule.py` already uses `ServiceAdapter`, and `platform/service_adapter.py` already exposes status methods. Align dashboard with that boundary so web read models do not retain Linux/systemd details.
   - This is read-only and not a mutating web leak, so it is not a blocker, but it is foundation debt directly in the web layer.

3. Clean current documentation drift.
   - `docs/development.md:104-106` still says the web interface is planned and warns "Until the implementation exists".
   - `docs/troubleshooting.md:276-279` and `docs/roadmap.md:882-885` still list player refresh failure outcome audit as future work, but `docs/checklist.md`, `docs/web-interface-plan.md`, `docs/web-interface-handoff.md`, code, and tests show that slice is done.

## Nice-to-have / future debt

- CLI/TUI/bot/report/install/repair still import Linux/systemd `service_manager` directly. This matches the current compatibility path and should remain future platform-adapter migration, not a web-foundation blocker.
- `src/armactl/web/service.py` manages the `armactl-web.service` systemd unit for CLI/deployment lifecycle. Keep it Linux/systemd-scoped unless broader web-service platform support becomes part of the adapter plan.
- Several focused web test modules are large (`test_web_jobs.py`, `test_web_player_registry.py`, `test_web_files.py`, `test_web_dashboard.py`, `test_web_admin_actions.py`, `test_web_mod_actions.py` are roughly 800-900 lines each). This is no longer the old giant `test_web_app.py` regression, but future slices should keep splitting by workflow when new cases land.
- Future feature work called out in docs remains correctly scoped: server update/version flow, schema inventory for third-person/crossplay/platform config, timezone-aware schedule UX, users/system admin panel, policy/roles/permissions/tiers, IP allowlist/trusted proxy handling, local CLI/SSH break-glass recovery, settings registry, broader platform adapters/Windows backend, banlist/player history, destructive file/raw config/terminal/host controls.

## Documentation drift

Primary current-state docs are mostly consistent now:

- `README.md` marks the browser management panel foundation as implemented on `feat/web-interface` and keeps release/production-hardening caveats.
- `docs/architecture.md` describes current web capabilities, platform adapter boundaries, future update/timezone flows, roles/permissions/tiers separation, and MIT/public-history constraints.
- `docs/checklist.md` marks web foundation and player refresh outcome audit as done while keeping future slices explicit.
- `docs/web-interface-plan.md` and `docs/web-interface-handoff.md` clearly separate implemented web surfaces from future dangerous/commercial/platform work.

Remaining drift:

- `docs/development.md` still describes the web interface as planned.
- `docs/troubleshooting.md` and `docs/roadmap.md` still carry `player refresh failure outcome audit` in future-work lists.
- Dated audit docs such as `docs/documentation-audit-results-20260619.md` and `docs/system-modularity-audit-results-20260619.md` are historical. They should not be treated as live current-state docs after this report, though they still explain useful context.

Commercial/licensing consistency looks good in current docs. I did not find docs implying `premium = admin`; the docs repeatedly state that product tiers, roles, permissions, and entitlements must remain separate. `README.md` and `docs/architecture.md` also avoid claiming public MIT history can be made private retroactively.

## Final recommendation

Not ready; run these fix/refactor slices first.

The foundation has no blocker and the suite is green, but I would not stamp it as fully accepted until the narrow should-fix items above are closed: explicit audit phase consistency for jobs/uploads, dashboard status reads through the service adapter, and small doc drift cleanup.

No blocker was found, so no blocker prompt for Arnold is required.
