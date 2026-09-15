# Plan Document Register

Audit date: 2026-09-14

This file classifies public `armactl` planning documents. It does not track task
completion. All unfinished public/free work is tracked only in
[checklist.md](checklist.md).

Project-status checkboxes are reserved for `checklist.md`. Detailed contracts
use requirement bullets, completed references preserve outcome/evidence bullets,
and recurring runbooks describe reusable procedures. None of those forms is a
second project backlog. The repository PR template is the only explicit
checkbox exception: it is a per-PR validation/risk form, not persistent project
status.

Private/commercial dashboard planning stays in the private documentation
repository. It must not be copied into this public register or used as an
implicit public-main merge instruction.

## Active Detailed Contracts

These documents define requirements for work currently active in P0/P2 of the
single checklist, not work that is merely parked in the decision-gated backlog.
They are not secondary status trackers and do not carry project-status
checkboxes.

| Document | Contract scope |
| --- | --- |
| [banlist-moderation-contract.md](banlist-moderation-contract.md) | Native ban source, typed mutation, identity, privacy, recovery, UI, and staged acceptance requirements. |
| [server-update-compatibility.md](server-update-compatibility.md) | Update baselines, isolated candidate build, modded/vanilla canaries, named profiles, addon preservation, and rollback behavior. |
| [incident-monitoring.md](incident-monitoring.md) | Persistent incident capture, evidence bounds, redaction, core capability, service/timer lifecycle, and staged acceptance requirements. |
| [admin-permissions-contract.md](admin-permissions-contract.md) | Canonical admin/GM synchronization, supported mod ACLs, rollback, and remaining non-`deus` production identity acceptance. |

## Completed Implementation References

These documents describe implemented foundations or closed audits. Their
historical acceptance records are reference material, not open work. Any
genuine follow-up extracted from them belongs only in
[checklist.md](checklist.md). A separately decision-gated extension does not
reopen the completed baseline document until that work is explicitly activated.

- [safe-config-controls-plan.md](safe-config-controls-plan.md)
- [player-session-stats-contract.md](player-session-stats-contract.md)
- [chervonopilya-fps-roster-stability-plan.md](chervonopilya-fps-roster-stability-plan.md)
- [web-interface-plan.md](web-interface-plan.md)
- [safe-file-editing-contract.md](safe-file-editing-contract.md)
- [player-log-ingest-incremental-contract.md](player-log-ingest-incremental-contract.md)
- [player-session-detail-search-contract.md](player-session-detail-search-contract.md)
- [player-session-supervised-pipeline-contract.md](player-session-supervised-pipeline-contract.md)
- [player-data-truth-remediation-plan.md](player-data-truth-remediation-plan.md)
- [reuse-solid-duplication-audit.md](reuse-solid-duplication-audit.md)
- [hardening-audit-cleanup-checklist.md](hardening-audit-cleanup-checklist.md)

## Archived In Git History

The twelve internal audit/inventory/plan/handoff files removed by commit `8582e8a`
during the public/internal documentation split remain available in Git history.
They were already closed or superseded, are intentionally absent from the
working tree, and must not be restored as active checklists or copied back from
private planning. The original root project checklist and project plan were
renamed into `docs/checklist.md` and `docs/roadmap.md`; they were not lost.

## Historical Inventories And Evidence

These are source/evidence inventories. They must not be read as current
implementation order or as a competing backlog.

- [player-data-inventory.md](player-data-inventory.md)
- [player-log-event-inventory.md](player-log-event-inventory.md)
- [player-data-truth-readonly-audit.md](player-data-truth-readonly-audit.md)

## Recurring Runbooks

These documents define procedures that are invoked by checklist gates. An
individual procedure step is not a permanent feature backlog item.

- [network-hardening-runbook.md](network-hardening-runbook.md)
- [web-deployment.md](web-deployment.md)
- [release-process.md](release-process.md)
- [release-notes-template.md](release-notes-template.md)

## Public Product And Contributor Documentation

These describe the stable public/free product and contributor workflow. They
must link to the single checklist rather than maintain their own open task list.

- [roadmap.md](roadmap.md)
- [architecture.md](architecture.md)
- [development.md](development.md)
- [troubleshooting.md](troubleshooting.md)
- [telegram-bot.md](telegram-bot.md)
- [localization.md](localization.md)

## Classification Rule

When a new plan is proposed:

1. put unfinished task status in [checklist.md](checklist.md);
2. add a separate contract only when detailed invariants or acceptance rules are
   needed;
3. classify that contract here;
4. remove its completed tasks from the active checklist after the result is
   recorded;
5. convert completed plan checkboxes to historical evidence bullets;
6. archive internal history instead of letting another status tracker emerge.
