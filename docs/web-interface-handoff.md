# Web Interface Handoff

This document is the working handoff for implementing the armactl web panel with
one implementation chat/agent and one review chat/agent. The implementation
agent leaves changes uncommitted. The review agent validates the diff, requests
or applies fixes when needed, and creates the commit only after review passes.

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
- Do not commit from the implementation chat. Leave changes in the working tree
  for the review chat.
- The review chat creates one focused commit per approved implementation step.

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
- Treat web as an always-on service: `armactl web run` is for foreground
  development/debugging, while production uses `armactl-web.service`.
- Do not make web replace the default `./armactl` TUI startup path.
- Preserve the target remote scenario: a user can open a public HTTPS URL from
  another network, log in, and use the permitted panel features without SSH.
- Use the shared `ports.py` blocked web port helpers when implementing
  `armactl web init`, `armactl web run`, or service install port validation.
- When documenting or testing a central reverse proxy outside the game VM,
  remember that proxy upstreams must target the game VM private address, not
  `127.0.0.1`.
- Roll web out first on a paused/non-critical VM. Do not start with stable
  production or rented customer servers.
- Treat any existing noVNC/SFTP/browser file-manager VM as a separate external
  project, not as an armactl-web dependency or component.
- Do not let SSHFS/NFS/SMB or other remote mounts silently expand the web file
  manager's allowed roots.
- Scope the MVP cabinet to one local machine/server per `armactl-web`
  installation. Do not add multi-server enrollment or cross-VM management.
- Keep CLI and TUI usable as fallback management paths.
- Keep tests independent from saved operator UI language.

## Current implementation status

Completed foundation:

1. `src/armactl/web/` package skeleton and import-safety tests.
2. Optional web dependencies, package-data/bootstrap support, and `scripts/run-web`.
3. Web-facing facade/DTO layer for read-only dashboard data.
4. FastAPI app factory, `/healthz`, package-local templates/static assets, and
   minimal read-only dashboard routes.
5. Web runtime storage/config/db foundation under `~/armactl-data/web/`.
6. `armactl web init` first setup command for local runtime config and DB.
7. Auth DB/password foundation: `web_users`, Argon2 password helpers, and
   minimal active `owner` user helpers.
8. Owner-user setup flow via explicit `armactl web init --owner USERNAME`
   with hidden password confirmation and safe operator summary output.
9. Session and CSRF primitives: digest-only SQLite storage, expiry checks,
   session revoke/delete helpers, and CSRF tokens bound to active sessions.
10. Login/logout routes, login template, authenticated dashboard guard, and
    HttpOnly SameSite=Lax cookie wiring. `Secure` is enabled when
    `ARMACTL_WEB_HTTPS_REQUIRED=true`; Lax keeps localhost/dev form redirects
    usable while blocking routine cross-site POST cookie sending.
11. Foreground `armactl web run` smoke launcher: ensures runtime, honors
    transient `--host/--port`, prints a safe startup summary, and runs the
    FastAPI app with Uvicorn. `--dev` enables Uvicorn reload for local Python,
    template, and CSS work.
12. Web permission categories foundation: owner has all declared code-level
    permissions and dashboard routes require `dashboard:view`.

Next recommended implementation order:

1. Expand dashboard read-only parity with the useful TUI status information.
2. Add controlled server actions with confirmations and audit logging.
3. Add background jobs before exposing long-running operations.
4. Add config/mods/admins/bot web flows through existing backend modules.
5. Add the safe filesystem adapter before upload/download routes.
6. Add `armactl-web.service` and deployment docs.
7. Add reverse proxy / HTTPS deployment docs.

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
- Keep the diff small.
- Do not commit. Leave the working tree ready for review and suggest a concise
  commit message.

Before finishing, run the relevant tests and `git diff --check`.
Report:
- files changed
- tests run
- any known gaps or follow-up tasks
- suggested commit message
```

## Review prompt template

Use this template for a review chat:

```text
Review the latest uncommitted implementation changes on
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
summary. If there are no findings, say so clearly, mention residual risk, and
commit the approved changes with a concise message.
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
