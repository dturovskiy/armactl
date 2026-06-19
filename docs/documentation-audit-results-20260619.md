# Documentation Audit - 2026-06-19

## Branch / commit reviewed

- Branch: `feat/web-interface`
- Commit: `7777e2d21c804bd1d2980aa3b974c6cf99ad3ddc`
- Resolution note: this audit records documentation drift found before the follow-up docs patch. The current top-level docs were updated after this report; keep this file as audit evidence, not as the live status source.
- Checkout reviewed: WSL checkout only, `/home/deus/projects/armactl`
- Worktree note: `git status -sb` showed a pre-existing modified `docs/system-modularity-audit-results-20260619.md`. This audit did not edit that file.
- No code changes, commits, or pushes were made during the audit itself.

## Scope

Required docs reviewed:

- `docs/architecture.md`
- `docs/checklist.md`
- `docs/web-interface-plan.md`
- `docs/web-interface-handoff.md`
- `docs/web-deployment.md`
- `docs/web-system-audit.md`
- `docs/system-modularity-audit.md`
- `docs/system-modularity-audit-results-20260619.md`
- `docs/system-modularity-audit-results-20260618.md`
- `README.md`
- `website/`

Additional documentation drift found by the required search was also checked in:

- `docs/roadmap.md`
- `docs/troubleshooting.md`

Code was checked only as documentation ground truth for the web status and boundaries. No pytest run was needed because this slice only creates a docs audit report.

## Documentation status summary

The web dashboard is implemented on this branch and the source tree matches the recent web/refactor/modularity context: domain routes are registered, page models are split, the filesystem service is split, service/schedule web actions use the platform service adapter, jobs have active dedupe and atomic worker claim, pending operator work is separate from background jobs, and player registry/moderation is split across source, identity, storage, page, and action modules.

Most deep web docs are current:

- `docs/web-interface-plan.md` is mostly aligned with current code.
- `docs/web-interface-handoff.md` has strong current guardrails, but two stale status fragments.
- `docs/web-deployment.md` accurately describes current source-checkout web deployment and smoke checks.
- `docs/web-system-audit.md` and `docs/system-modularity-audit.md` remain valid protocols.
- `docs/system-modularity-audit-results-20260619.md` is the best current architecture/status source among audit reports.

The main drift is in top-level/current-state docs. `README.md`, `docs/architecture.md`, `docs/checklist.md`, `docs/roadmap.md`, and `docs/troubleshooting.md` still describe the web panel as planned or future in places that look current to operators.

## Blockers

### B1. Top-level docs still present the web panel as planned

Refs:

- `README.md:10-11`
- `README.md:134-142`
- `README.md:185-191`
- `docs/architecture.md:11`
- `docs/architecture.md:64`
- `docs/architecture.md:86`
- `docs/architecture.md:187`
- `docs/architecture.md:233`
- `docs/architecture.md:261`
- `docs/architecture.md:276`
- `docs/architecture.md:358`
- `docs/checklist.md:236`
- `docs/checklist.md:374`
- `docs/roadmap.md:7`
- `docs/roadmap.md:24`
- `docs/roadmap.md:40`
- `docs/roadmap.md:816`
- `docs/roadmap.md:869-871`
- `docs/troubleshooting.md:256-262`

Why it matters:

- These files look like current operator-facing documentation, not historical notes.
- They contradict implemented web routes and docs that already describe dashboard, config, mods, admins, schedule, files, logs, jobs, and players.
- New contributors could treat implemented code as speculative and make wrong patch decisions.

Required doc patch:

- Rephrase the web panel as implemented on `feat/web-interface`, with remaining future work called out separately.
- Keep release/stability wording precise if this branch is not yet the public stable release.
- Replace "planned web runtime" with current web runtime data where appropriate.
- Keep future-only items clearly scoped: DB migrations, player refresh failure outcome audit, settings registry, policy/roles/tiers, broader platform adapters, banlist, destructive file workflows, and SAT/mod runtime settings.

## Should-fix

### S1. `docs/architecture.md` needs a current web architecture refresh

Refs:

- `docs/architecture.md:37`
- `docs/architecture.md:64`
- `docs/architecture.md:86`
- `docs/architecture.md:113`
- `docs/architecture.md:187-221`
- `docs/architecture.md:233`
- `docs/architecture.md:248-253`
- `docs/architecture.md:261-293`
- `docs/architecture.md:358-371`

What to change:

- Update the repo tree to show current web subpackages: `routes/`, `page_models/`, `services/`, `jobs/`, `runtime/`, `auth/`, `templates/`, and `static/`.
- Update the module table:
  - `routes/` = HTTP glue.
  - `services/` = workflow, audit, pending work, mutation orchestration.
  - `page_models/` / `views/` = DTO/read models.
  - `platform/service_adapter.py` = service/schedule backend seam.
  - filesystem modules = `filesystem_roots`, `filesystem_paths`, `filesystem_listing`, `filesystem_preview`, `filesystem_transfer`, `file_uploads`, and compatibility `filesystem.py`.
  - player modules = `player_sources`, `player_identity`, `player_registry`, `page_models/players`, `player_actions`.
- Remove "planned" from `scripts/run-web`, `src/armactl/web/`, `armactl-web.service`, and the web dashboard flow.
- Keep Windows backend language as future adapter work.

### S2. `README.md` needs a branch-current web section

Refs:

- `README.md:10-11`
- `README.md:28`
- `README.md:134-142`
- `README.md:185-191`

What to change:

- Replace "planned as the next major interface" with a short current-state summary for this branch.
- Mention implemented web capabilities: dashboard, config, mods, admins, schedule, files, logs/report, jobs, players, auth/session/CSRF, and deployment docs.
- Keep CLI/TUI/Telegram as fallback/stable paths if that is still the release posture.
- Link `docs/web-deployment.md` for smoke/deployment and `docs/web-interface-plan.md` for current architecture/future work.
- Do not imply premium/paid features exist.

### S3. `docs/checklist.md` has stale phase framing and completed audit items

Refs:

- `docs/checklist.md:236`
- `docs/checklist.md:245`
- `docs/checklist.md:249`
- `docs/checklist.md:374`

What to change:

- Rename Phase 16 from "Web interface (planned)" to a current web implementation phase or split it into "implemented foundation" plus "remaining web work".
- Mark the repeat architecture/modularity audit and baseline system modularity audit as done if the intended source is `docs/system-modularity-audit-results-20260619.md`.
- Replace the final "current next stage" sentence with the next true slice, likely docs patch, DB migrations, or player refresh audit completeness.

### S4. `docs/web-interface-handoff.md` has two misleading stale status fragments

Refs:

- `docs/web-interface-handoff.md:226-233`
- `docs/web-interface-handoff.md:406-419`

What to change:

- Lines 226-233 say no install/repair/update handlers are registered, but lines 268-274 say install/repair web job flows are implemented. Update item 17 to say update and large-file handlers remain future while install/repair are implemented.
- Move implemented schedule/boot policy text out from under "Planned but not implemented", or rename the heading to separate "Implemented but needs polish" from future items.

### S5. `docs/web-interface-plan.md` has stale historical wording in otherwise current sections

Refs:

- `docs/web-interface-plan.md:1144-1156`
- `docs/web-interface-plan.md:1172-1176`

What to change:

- Update backend surface:
  - server actions and schedule use `platform/service_adapter.py` for web workflows, with Linux/systemd `service_manager` as the default backend.
  - players registry is no longer purely future; the foundation exists and banlist/details/history remain future.
- Change "Before implementing many routes..." to a current-state note: page models are split and `facade.py` is now a compatibility re-export.

### S6. `docs/troubleshooting.md` still says the web panel is not implemented

Refs:

- `docs/troubleshooting.md:256-262`

What to change:

- Replace "planned but not part of the current stable operator surface yet" with current deployment/troubleshooting framing.
- Point to `docs/web-deployment.md` for actual smoke checks and reverse proxy handling.
- Keep the layer separation bullets; those still match current architecture.

### S7. `docs/roadmap.md` needs historical/current wording cleanup

Refs:

- `docs/roadmap.md:7`
- `docs/roadmap.md:24`
- `docs/roadmap.md:40`
- `docs/roadmap.md:816`
- `docs/roadmap.md:869-871`

What to change:

- Preserve the roadmap as historical CLI/TUI/Telegram specification, but stop saying the web panel is the next unimplemented stage.
- Change Scenario G from planned to a current web-panel scenario with future additions clearly named.
- Replace the bottom "start with package skeleton/auth/dashboard" next step, because those are already implemented.

## Nice-to-have

- Add a compact "Web current capabilities" matrix to `README.md` or `docs/architecture.md` so operator-facing docs do not require reading the full plan.
- Add a short note to `docs/system-modularity-audit-results-20260618.md` that it is a historical baseline superseded by the 2026-06-19 repeat audit for current blocker status.
- In `docs/architecture.md`, update the test tree to include split web tests instead of only early core tests.
- Add removal criteria for compatibility shims: `src/armactl/web/facade.py`, `src/armactl/web/services/filesystem.py`, and `src/armactl/web/services/pending_restart.py`.
- Add a future docs slice after DB migrations to document schema/version policy for `web.db` and `players.db`.

## Stale or misleading sections

- `README.md:10-11`: web described as planned; should be current branch feature with release/stability caveat.
- `README.md:134-142`: "planned web runtime"; should be current web runtime.
- `README.md:185-191`: "Planned web interface"; should summarize implemented panel plus future work.
- `docs/architecture.md:64`, `86`, `113`, `187`, `233`, `261`, `276`, `358`: planned-web wording in current architecture.
- `docs/checklist.md:236`, `374`: Phase 16 still framed as planned/current next stage.
- `docs/web-interface-handoff.md:232-233`: install/repair handlers are no longer absent.
- `docs/web-interface-handoff.md:406-419`: implemented schedule section under "Planned but not implemented".
- `docs/web-interface-plan.md:1148`, `1153`: backend surface still lists `service_manager` directly for web service/schedule instead of the service adapter seam.
- `docs/web-interface-plan.md:1156`: players/moderation described as future even though registry foundation is implemented.
- `docs/web-interface-plan.md:1172-1176`: pre-implementation facade instruction should become historical/current-state wording.
- `docs/roadmap.md:7`, `24`, `40`, `816`, `869-871`: roadmap treats web as next planned work despite implementation.
- `docs/troubleshooting.md:256-262`: says web is planned/not implemented.

## Missing sections

- Top-level current web capability summary: dashboard, config, mods, admins, schedule, files, logs/report, jobs, players.
- Architecture section for the split web layout: routes/services/page_models/jobs/runtime/auth.
- Architecture section for player registry boundaries and privacy defaults.
- Architecture section for pending operator work vs background jobs.
- Architecture/future-work section for DB migrations covering both `web.db` and `players.db`.
- Architecture/future-work section for settings registry and feature policy/roles/tiers.
- Commercial posture note in top-level docs or architecture: paid features are future policy work; MIT/public repo history cannot be closed retroactively; proprietary premium implementation should not be casually committed to a public MIT repo; repo/license decisions need business/legal review.

## Docs that match current code

- `docs/web-deployment.md`: current source-checkout deployment, local/VM smoke, reverse proxy/HTTPS, port separation, install/repair background jobs.
- `docs/web-system-audit.md`: still matches current web audit protocol and red flags.
- `docs/system-modularity-audit.md`: still matches current whole-project audit protocol.
- `docs/system-modularity-audit-results-20260619.md`: matches current architecture and known future work: no current blockers, DB migrations, player refresh failure audit, platform adapters, policy/roles/tiers, settings registry, banlist, destructive file workflows, and SAT/mod settings.
- `docs/web-interface-plan.md`: mostly matches current code in sections for guardrails, pending work/jobs, testing rules, filesystem split, player registry foundation, paid posture, Windows future adapter, and implementation inventory.
- `docs/web-interface-handoff.md`: mostly matches current code in safety rules, route audit inventory, broad-exception inventory, current implementation status, pending work/jobs, route/page-model/filesystem splits, platform adapter, and player registry boundaries.
- `website/`: static marketing site; it does not currently describe the authenticated web/dashboard/server-management panel in a way that creates drift.

## Recommended next documentation patch slices

1. Top-level web status patch: update `README.md`, `docs/architecture.md`, `docs/checklist.md`, `docs/troubleshooting.md`, and `docs/roadmap.md` so web is not described as purely planned.
2. Web architecture precision patch: add split routes/services/page_models/filesystem/player/jobs/pending-work architecture details to `docs/architecture.md`.
3. Handoff/plan cleanup patch: fix stale install/repair handler wording, split implemented schedule polish from future items, and update the backend surface in `docs/web-interface-plan.md`.
4. Commercial/future-work policy patch: add a concise top-level note for paid features, repo/license review, entitlement-vs-permission policy, and public MIT constraints.
5. Migration/future readiness patch after the DB slice: document migration policy for `web.db` and `players.db`, then update handoff/plan/audit references.

## Validation run

Required commands:

```bash
git status -sb
```

Result from WSL checkout via `bash.exe -lc`:

```text
## feat/web-interface...origin/feat/web-interface
 M docs/system-modularity-audit-results-20260619.md
```

The modified `docs/system-modularity-audit-results-20260619.md` was pre-existing for this slice and was not edited by this audit.

```bash
git diff --check
```

Result: exit code 0, no whitespace errors reported.

```bash
rg -n "planned|not implemented|future|TODO|route global|player_moderation|facade|filesystem.py|service_manager|MIT|premium|paid|private|Windows|registry" docs README.md website src/armactl/web
```

Result: exit code 0, many expected matches. Reviewed categories included top-level stale web-planned wording, current guardrails, future work, MIT/premium posture, service-manager compatibility, facade/filesystem compatibility shims, and player registry references.

Additional targeted checks:

```bash
rg -n "sqlite3|SELECT |INSERT |UPDATE |DELETE |PRAGMA|CREATE TABLE|DROP TABLE" src/armactl/web/routes src/armactl/web/templates
```

Result: exit code 1, no raw SQL in routes/templates.

```bash
rg -n "__globals__|route global|endpoint\.__globals__|app\.router\.routes|dependency_overrides|monkeypatch\.setattr\([^\n]*(routes|endpoint|__globals__)" tests
```

Result: exit code 1, no route-global or FastAPI endpoint-internal test patching hits.

```bash
rg -n "players\.db|ip_address|CREATE TABLE|refresh_registry_and_audit" src/armactl/web/services/player_registry.py src/armactl/web/services/player_actions.py src/armactl/web/page_models/players.py src/armactl/web/routes/players.py tests
```

Result: exit code 0. Reviewed player registry schema and tests; `tests/test_web_player_registry.py:132` asserts `ip_address` is not present.

No full pytest run was performed because only this documentation audit report was edited.
