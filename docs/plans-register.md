# Work Plan Register

Audit date: 2026-08-12

This is the cross-document register for plan status. The detailed contracts remain
authoritative for implementation requirements. This register prevents a checkbox-only
scan from hiding prose-only work, conditional gates, or recurring operator procedures.

Status meanings:

- **Open**: concrete work is defined and not implemented.
- **Gated**: work is optional or cannot start until an explicit product, truth,
  recovery, or operator-need decision is made.
- **Recurring**: repeat after relevant changes; it is not a one-time feature.
- **Complete**: code/tests or documented production acceptance close the plan.
- **Out of scope**: intentionally excluded; it is not an armactl backlog item.

## Concrete Open Work

### P1. Native moderation Slices 7d-7e

Status: **Open**.

Source: [banlist-moderation-contract.md](banlist-moderation-contract.md).

Completed foundation:

- Slice 7a selected native Reforger RCON as the only ban source of truth and
  defined identity, privacy, permission, recovery, and architecture rules.
- Slice 7b added typed bounded `#ban list` parsing/querying and the authenticated
  read-only page gated by `players:moderate`.
- Slice 7c added typed bounded create/remove commands plus a verified service with
  idempotent classification, redacted intent/outcome audit, and read-first recovery.
- `web.db` schema v16 stores only non-authoritative recovery metadata, not reason text or
  duration.
- No shadow ban table, SAT/WCS mirror, IP storage, arbitrary RCON endpoint, or
  GET-side mutation was added.

Still open:

- Deferred kick follow-up only after fresh reliable roster resolution, exact
  matching, immediate re-resolution, and proven response fixtures.
- Slice 7d POST-only CSRF-protected ban/unban routes, confirmations, notices, and
  browser/security regressions.
- Slice 7e Serhiivka-first audited acceptance, followed by Chervonopilya only
  after explicit approval.

Code verification:

- `rcon.py` exposes bounded typed list/create/remove operations only; no arbitrary executor.
- `native_moderation.py` owns validation, the shared lock, verified baseline/outcome,
  idempotency, redacted audit orchestration, and read-first recovery.
- Schema v16 stores recovery metadata only; no ban truth, reason text, or duration.
- There is still no ban/unban/kick POST route, mutation UI, or kick command.

### P2. Diagnostic report download/export

Status: **Open**.

Sources: [checklist.md](checklist.md), [web-interface-plan.md](web-interface-plan.md),
and [roadmap.md](roadmap.md).

The authenticated web UI has a bounded redacted report preview at `GET /report`.
The CLI `armactl report` remains the export fallback. Add one authenticated,
bounded, redacted download/export response that reuses the existing report builder;
do not create a second report implementation.

### P3. Active game-log anomaly warning

Status: **Open**.

Sources: [chervonopilya-fps-roster-stability-plan.md](chervonopilya-fps-roster-stability-plan.md),
[checklist.md](checklist.md), and [roadmap.md](roadmap.md).

Add an operator-visible bounded warning when active `console.log`, `error.log`,
or `script.log` files become anomalously large or show the defined spam signal.
The warning must not read the files unbounded or expose raw paths/lines.

The related ingest-safety requirement is already complete: active oversized logs
use bounded tail/bootstrap plus persisted incremental offsets, and historical
oversized files remain controlled counts rather than blocking current freshness.

### P4. Public/private documentation and extraction boundary

Status: **Open public-main merge gate**.

Sources: [checklist.md](checklist.md), [web-interface-plan.md](web-interface-plan.md),
and [roadmap.md](roadmap.md).

Remaining decisions/work:

- finish the public/private documentation review;
- remove or move internal hosted, infrastructure, commercial, and extraction
  planning that does not belong in the free/local public core;
- decide the future private dashboard extraction/snapshot path separately from
  public armactl backports;
- run the final public-safe hostname/IP/route/topology review.

Stale wording that still described the already implemented dashboard as "planned"
was corrected in `SUPPORT.md`, `SECURITY.md`, `docs/localization.md`, and
`docs/telegram-bot.md` during this audit. That cleanup does not close the larger
extraction decision.

## Gated Or Conditional Work

These items are recorded so they are not lost, but they are not current
correctness blockers.

| ID | Item | Gate |
| --- | --- | --- |
| C1 | Expand safe config controls one field group at a time | Choose a field group and prove server behavior, validation, backup, restart, rollback, and VM smoke. Network/bind/port/RCON/secret fields need separate contracts. |
| C2 | Discord/public player enrichment | Decide current session vs last session vs no combat stats; align the separate public-stats snapshot with authenticated truth owners; keep Role blocked without a reliable source. |
| C3 | CLI current-roster cache status | Add only if the existing authenticated `/players` source/cache/age/availability/error diagnostics are insufficient for operators. |
| C4 | Live background-job cancellation/process termination | Requires a real worker lease/cancel design. Mark-abandoned currently repairs metadata only and must not be presented as cancellation. |
| C5 | Shared audit and job-enqueue wrappers | Extract only when another multi-service slice proves identical semantics and reduces real duplication. |
| C6 | Low-noise dead-code tooling and compatibility retirement | Define an allowlist and wait for the downstream compatibility/public-merge window before retiring tested facades or `/players/refresh`. |
| C7 | Mod-cleanup quarantine/restore | Required only before deliberately broadening deletion beyond the current bounded `config/addons` behavior. |
| C8 | Session JSON API or bulk export | Add only after demonstrated operator need; authenticated server-rendered search/detail is complete. |
| C9 | IP moderation/privacy | Separate security/product decision. Current Slices 7b-7e intentionally exclude IP storage, search, history, and bans. |

## Recurring Operator And Release Gates

These are procedures, not unfinished features:

- repeat focused/full validation when shared code changes;
- repeat authenticated browser and production readiness smoke after relevant
  web/auth/schema/deployment changes;
- use Serhiivka-first rollout where a contract explicitly requires it;
- refresh screenshots after visible UI changes;
- follow [release-process.md](release-process.md), the release-notes template,
  and `CHANGELOG.md` for an actual release;
- re-run the network/deployment checklist when gateway, nginx, firewall, VPN,
  bind, TLS, or routing state changes;
- on Chervonopilya, recapture bounded evidence if ACE exception spam or roster
  flicker recurs before changing game/mod state.

## Completed Plan Families

- Core install/repair/service/TUI/CLI/Telegram flows.
- Local dashboard auth, health/readiness, jobs, updates, config, files, mods,
  admins, schedule, logs preview, public status, and Discord publisher.
- Scheduled restart hardening and twice-daily timer generation.
- Current safe config fields and guarded raw config editing.
- Narrow safe file editor/replacement contract.
- Admin ACL synchronization into supported SAT/WCS files.
- Mod mutation recovery manifests and shared pending-restart recovery, including
  stale profile-settings cleanup.
- Project-wide reuse/SOLID audit P1 closure and current compatibility
  classification.
- Operator-critical TUI/Web parity classification. Full feature-for-feature
  parity is intentionally not a goal.
- Player identity/history/session schema, ingest, supervised timers, reconnect
  model, current-session stats, search/detail UI, and staged VM acceptance.
- FPS fractional display, roster stale/unavailable clarity, and current
  Chervonopilya infrastructure validation.
- Native moderation Slices 7a-7c: design, read-only list, and verified ban/unban backend.

Representative evidence commits include `3d27b18` (scheduled restart hardening),
`42ecb77`/`4f50c93` (read-only native bans), `38d071e` through `0b3168c`
(supervised sessions and acceptance), `566c068` (roster fallback), `747ece3`
(FPS precision), `bb90999` (collapsible mod lists), and `e98988b` (shared
profile-cleanup recovery and planning correction).

## Plan Source Classification

| Source | Current classification |
| --- | --- |
| `docs/checklist.md` | Concise active/conditional public checklist; points here for cross-document status. |
| `docs/roadmap.md` | Current high-level completed/open summary. |
| `docs/web-interface-plan.md` | Implemented web baseline, parity decisions, historical audit closure, and remaining gates. |
| `docs/banlist-moderation-contract.md` | Active authoritative contract: 7a-7c complete; 7d-7e open; kick and IP moderation separately gated. |
| `docs/chervonopilya-fps-roster-stability-plan.md` | Infrastructure investigation complete; log warning open; CLI cache status conditional; mod remediation out of scope unless recurrence. |
| `docs/safe-config-controls-plan.md` | Current safe set complete; future field groups gated. |
| `docs/safe-file-editing-contract.md` | Implemented contract; broad file management remains out of scope. |
| `docs/admin-permissions-contract.md` | Implemented synchronization contract. |
| `docs/player-session-stats-contract.md` | Slices A-F complete; Slice G Discord/public decision gated. |
| `docs/player-session-detail-search-contract.md` | Slices 6a-6d complete, including production acceptance. |
| `docs/player-session-supervised-pipeline-contract.md` | F3a-F3c complete, including both production hosts. |
| `docs/player-log-ingest-incremental-contract.md` | Complete, including busy-log production acceptance. |
| `docs/player-data-truth-remediation-plan.md` | Slices 0-6 complete for the current deployed baseline; future deploy smoke is recurring. |
| `docs/player-data-inventory.md` | Current source-of-truth inventory; remaining player work redirects to moderation and gated Discord. |
| `docs/player-log-event-inventory.md` | Historical inventory; original next slices are superseded by implemented contracts. |
| `docs/player-data-truth-readonly-audit.md` | Historical baseline; timestamp recommendations are implemented. |
| `docs/hardening-audit-cleanup-checklist.md` | P1/P2 audit pass closed; remaining items are conditional only. |
| `docs/reuse-solid-duplication-audit.md` | P1 closed; named P2 follow-ups are conditional only. |
| `docs/network-hardening-runbook.md` | Recurring operator/deployment guidance, not a code backlog. |
| `docs/web-deployment.md` | Recurring deployment/smoke guidance, not a one-time feature plan. |
| `docs/telegram-bot.md` | Implemented runtime and operator workflow. |
| `docs/release-process.md` | Per-release recurring checklist; no release is implied by an open checkbox elsewhere. |

## Explicitly Not Armactl Infrastructure Work

- ACE Medical or scenario/mod code fixes when the exception signature is not
  reproduced in armactl infrastructure.
- LVOAC/Workshop client-content repair inside a cloud gaming provider.
- Arbitrary RCON console access, automatic moderation, SAT ban mirroring,
  nickname-only actions, or IP tracking.
- Broad file manager operations or destructive cleanup outside the existing
  allowlisted boundaries.
- Role/loadout truth, historical oversized-log backfill, or public player IDs
  without new explicit source/privacy contracts.
