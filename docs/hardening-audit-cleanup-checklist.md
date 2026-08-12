# Hardening Audit Cleanup Checklist

Цей документ фіксує cleanup-план після архітектурного аудиту `feat/web-interface`.

Boundary note: this is an internal audit carryover checklist. Do not treat it as public release/user documentation or a public `main` merge signal; move or archive it in private planning docs during public docs cleanup.

Правило для виконання: працювати тільки у WSL checkout `/home/deus/projects/armactl` на `feat/web-interface`. Windows checkout не використовувати для repo edits.

## P1: Stale Running Job Recovery (Closed)

Closed result:

- The Jobs page has an explicit POST-only operator action for stale running job metadata.
- Eligible rows are marked abandoned, not cancelled or handler failed.
- Recovery requires a non-fresh worker lease and refuses rows with a live in-process worker token.
- The action does not kill worker processes, does not fake cancellation, and does not run automatic destructive repair.
- Abandoned rows remain visible for audit/history but no longer block active queued/running dedupe.
- Intent/outcome audit uses safe counts/status metadata only; raw command output, paths, worker tokens, and secrets are not emitted by the recovery result.


## P1: Web Service Restart CLI Ambiguity (Closed)

Closed result:

- `armactl web service restart` reports `systemctl restart`, `/healthz` liveness, and `/readyz` schema readiness as separate bounded outcomes.
- Success requires the restart command, HTTP liveness, and running-process schema compatibility to pass.
- A stale process, missing readiness route, newer DB schema, restart timeout, or HTTP failure returns controlled diagnostics; liveness alone is never treated as deploy success.
- Scope is only `armactl-web.service`; game server restart/helper behavior stays out of scope.

## P1: Long-Lived Theme Toggle CSRF Drift (Closed)

Closed result:

- Long-open authenticated pages can refresh the theme cookie through the async preference endpoint even when their hidden form CSRF token is stale.
- The exception is intentionally narrow: it applies only to the fetch-style cookie-only theme preference update.
- Normal form posts and other mutating routes still fail closed on invalid CSRF.
- VM smoke confirmed the theme preference route returns controlled 200 responses after the fix.

## P2: Composite SSH Smoke Timeout (Classified)

Classification:

- Split web-service checks pass quickly: wrapper/bootstrap check, `armactl web service status`, `systemctl is-active armactl-web.service`, local `/healthz`, public status, and recent web journals are healthy.
- A previous long nested SSH smoke command timed out while the service was already active and health checks were OK.
- Treat this as an outer SSH/smoke-command guard issue unless local `systemctl`, `/healthz`, or web journals show service failure.
- Future smoke should prefer short commands or an outer timeout that accounts for SSH overhead; do not add code-level restart delays or fake success states for this symptom.

## P1: Immediate Should-Fix (Closed)

Closed result:

- Removed the unused mod restart-pending fallback helper.
- Routed admin restart-pending recovery through the shared mutation_recovery helper.
- Added regression coverage for controlled admin pending-work storage failure after mutation.


- Перед роботою виконати `git fetch`, `git pull --ff-only`, `git status -sb`.
- Якщо checkout не clean або є divergence, зупинитись і показати status.
- Не чіпати production, SSH, deploy, service restart.
- Не додавати нові фічі: file editor, banlist, Discord/player enrichment, scheduler, broader config controls.
- Перевірити `src/armactl/web/services/mod_actions.py`.
- Якщо `_mark_restart_pending_fallback_for_mod_result` справді unused після переходу на `mutation_recovery`, видалити dead code і пов'язані unused imports/tests.
- Перевірити mutation recovery contract у:
  - `config_edit.py`;
  - `file_replacements.py`;
  - `admin_actions.py`;
  - `mod_actions.py`;
  - mod cleanup/remove flow.
- Після фактичної mutation operator має отримати controlled sanitized result/recovery marker навіть якщо audit або pending bookkeeping падає.
- Якщо `admin_actions.py` досі має локальний дубль pending fallback, безпечно перевести його на `mutation_recovery.mark_restart_pending_for_mutation(...)`.
- Якщо semantics admin flow не збігаються, не ламати behavior: додати або залишити regression tests і пояснити, чому локальний path лишається deliberate.
- Додати або оновити тільки вузькі regression tests для touched behavior.
- Validation:
  - `git diff --check`;
  - `.venv/bin/python -m ruff check .`;
  - focused pytest для touched admin/mod/config/file replacement/mutation recovery tests;
  - full `.venv/bin/python -m pytest -q`, якщо зміни зачепили shared services.

## Slice 6 UX/Ops Hardening Note (Closed)

- This closes only dashboard/gateway UX hardening; it is not the final authenticated UI/production smoke gate.
- Dashboard refresh must keep the last successful snapshot visible across a
  transient failed poll and show stale wording only after a bounded age or
  repeated failures.
- Hidden/offline/background-return dashboard states should use neutral
  paused/reconnecting wording until an immediate refresh succeeds.
- Gateway docs should make login throttles explicit 429 responses, avoid strict
  GET `/login` limits that turn expired multi-tab sessions into false 503
  outages, and keep `/dashboard/status.json` bounded without breaking normal
  polling.
- Production nginx config, deploy, SSH, restart, auth/session semantics, CSRF,
  polling frequency, WebSocket/SSE, and player/session truth stay out of scope.

## P2: Follow-Up Cleanup (Closed For This Audit Pass)

Closed result:

- Kept web/facade.py, services/filesystem.py, and services/pending_restart.py as compatibility surfaces with explicit tests.
- Kept /players/refresh as a compatibility alias with a regression test for current-refresh enqueue behavior.
- Did not add a new dead-code dependency; low-noise tooling remains future work after an allowlist exists.
- A later follow-up routed mod profile-settings cleanup through the shared mutation recovery helper, removing the remaining local primary/fallback pending-work duplicate identified by the reuse audit.
- Remaining audit/enqueue wrapper consolidation and compatibility removal are conditional P2 work, not current correctness or production blockers.
Historical execution checklist (completed):

- [x] Classified `web/facade.py`, `services/filesystem.py`, and `services/pending_restart.py` as tested compatibility surfaces rather than unproven dead code.
- [x] Classified `/players/refresh` as a tested compatibility alias; removal remains conditional on the downstream compatibility window.
- [x] Used existing ruff rules and targeted usage checks without adding noisy dead-code tooling.
- [x] Added the shared recovery/rollback/pending-marker contract before later mutation features.
- [x] Completed the safe file editor and TUI/Web parity classification; safe config field expansion remains separately gated.
