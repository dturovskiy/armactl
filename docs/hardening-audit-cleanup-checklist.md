# Hardening Audit Cleanup Checklist

Цей документ фіксує cleanup-план після архітектурного аудиту `feat/web-interface`.

Правило для виконання: працювати тільки у WSL checkout `/home/deus/projects/armactl` на `feat/web-interface`. Windows checkout не використовувати для repo edits.

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

## P2: Follow-Up Cleanup (Closed For This Audit Pass)

Closed result:

- Kept web/facade.py, services/filesystem.py, and services/pending_restart.py as compatibility surfaces with explicit tests.
- Kept /players/refresh as a compatibility alias with a regression test for current-refresh enqueue behavior.
- Did not add a new dead-code dependency; low-noise tooling remains future work after an allowlist exists.


- Розібрати legacy compatibility surfaces:
  - `src/armactl/web/facade.py`;
  - `src/armactl/web/services/filesystem.py`;
  - `src/armactl/web/services/pending_restart.py`.
- Для кожної legacy surface:
  - або додати explicit compatibility test і залишити до окремого removal milestone;
  - або прибрати, якщо точно internal/dead і tests це підтверджують.
- Розібрати `/players/refresh` compatibility alias:
  - або залишити з deprecation/compatibility test;
  - або видалити окремим cleanup slice.
- Dead-code audit path для цього cleanup slice:
  - існуючого vulture/custom script у repo не знайдено;
  - нову dependency не додавати;
  - поки використовувати ruff F-rules і targeted rg usage checks;
  - окремий низькошумний script лишити future, коли буде allowlist.
  - не робити це blocker для P1.
- Перед майбутніми mutation-фічами зробити один спільний contract для recovery/rollback/pending markers.
- Після P2 можна повертатись до safe file editor, safe config controls і TUI/Web parity.
