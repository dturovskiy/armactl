# Web Interface Handoff

This document is the working handoff for implementing the armactl web panel with
one implementation chat/agent and one review chat/agent.

## Source of truth

Before implementing any web-panel task, read:

- `docs/web-interface-plan.md`
- `docs/checklist.md`, Phase 16
- `docs/architecture.md`
- `docs/development.md`
- `CONTRIBUTING.md`

Release files are not part of web-panel implementation unless the task is
explicitly a release task.

## Working branch and workspace

- Work in the WSL checkout: `/home/deus/projects/armactl`.
- Use branch `feat/web-interface` unless the maintainer asks for a smaller
  sub-branch.
- Do not edit or commit the Windows `E:\Projects\armactl` checkout.
- Keep changes small and reviewable.
- Prefer one focused commit per implementation step.

## Implementation rules

- Keep backend logic in reusable Python modules, not in TUI screens or web route
  handlers.
- Do not import Textual/TUI code from web code.
- Do not shell out to `armactl --json-output` for normal web behavior; call the
  existing Python backend modules directly.
- Add a web-facing facade/DTO layer before routes start combining many backend
  calls.
- Keep `website/` separate. It is the public marketing site, not the
  authenticated management panel.
- Do not expose arbitrary host filesystem access.
- Do not run install/repair/update as blocking HTTP requests.
- Keep CLI and TUI usable as fallback management paths.
- Keep tests independent from saved operator UI language.

## Recommended implementation order

1. Add `src/armactl/web/` package skeleton and tests.
2. Add optional web dependencies and package-data/bootstrap support.
3. Add web runtime config and local `web.db` initialization.
4. Add auth/session/CSRF primitives.
5. Add the web-facing facade/DTO layer for a read-only dashboard.
6. Add the dashboard UI.
7. Add controlled server actions with confirmations and audit logging.
8. Add background jobs before exposing long-running operations.
9. Add config/mods/admins/bot web flows through existing backend modules.
10. Add the safe filesystem adapter before upload/download routes.
11. Add `armactl-web.service` and deployment docs.

## Implementation prompt template

Use this template for a new implementation chat:

```text
You are implementing the armactl web panel in the WSL repo
`/home/deus/projects/armactl` on branch `feat/web-interface`.

Read these files first:
- docs/web-interface-plan.md
- docs/web-interface-handoff.md
- docs/checklist.md
- docs/architecture.md
- docs/development.md

Task:
<one focused Phase 16 task>

Constraints:
- Work only in the WSL checkout.
- Do not touch the Windows E: checkout.
- Keep backend logic in reusable modules, not route handlers.
- Do not import TUI code from web code.
- Do not shell out to armactl --json-output for normal web behavior.
- Add focused tests for the behavior you change.
- Keep the diff small and commit with a concise message.

Before finishing, run the relevant tests and `git diff --check`.
Report:
- files changed
- tests run
- any known gaps or follow-up tasks
```

## Review prompt template

Use this template for a review chat:

```text
Review the latest implementation commit(s) on
`/home/deus/projects/armactl`, branch `feat/web-interface`.

Use a code-review stance. Prioritize:
- security regressions
- auth/session/CSRF mistakes
- filesystem path traversal or unsafe writes
- route handlers containing business logic
- TUI imports inside web code
- subprocess usage where backend modules should be called directly
- missing tests
- packaging/bootstrap omissions
- docs/checklist drift

Check the diff against:
- docs/web-interface-plan.md
- docs/web-interface-handoff.md
- docs/checklist.md Phase 16
- docs/architecture.md
- docs/development.md

Run relevant tests and `git diff --check` if possible.
Return findings first with file/line references, then test coverage and a short
summary. If there are no findings, say so clearly and mention residual risk.
```

## Review gates

Every web-panel step should answer these before merge/next task:

- Does it reuse existing backend modules where possible?
- Does it avoid TUI imports and terminal-only formatting?
- Are new route handlers thin?
- Are secrets redacted?
- Are mutating actions protected by auth, permissions, and CSRF once auth
  exists?
- Are path operations contained inside explicit allowed roots?
- Are long-running operations modeled as jobs instead of request blocking?
- Are package data and bootstrap changes included when templates/static/deps are
  added?
- Are docs/checklist updated when scope changes?
