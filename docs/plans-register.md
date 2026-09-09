# Plan Document Register

Audit date: 2026-09-09

This file classifies public `armactl` planning documents. It does not track task
completion. All unfinished public/free work is tracked only in
[checklist.md](checklist.md).

Private/commercial dashboard planning stays in the private documentation
repository. It must not be copied into this public register or used as an
implicit public-main merge instruction.

## Active Detailed Contracts

These documents define requirements for work that still has an active or gated
entry in the single checklist. They are not secondary status trackers.

| Document | Contract scope |
| --- | --- |
| [banlist-moderation-contract.md](banlist-moderation-contract.md) | Native ban source, typed mutation, identity, privacy, recovery, UI, and staged acceptance requirements. |
| [server-update-compatibility.md](server-update-compatibility.md) | Update baselines, isolated candidate build, modded/vanilla canaries, named profiles, addon preservation, and rollback behavior. |

## Completed Implementation References

These documents describe implemented foundations or closed audits. Their
historical checklists and acceptance evidence are reference material, not open
work. Any genuine follow-up extracted from them belongs only in
[checklist.md](checklist.md).

- [admin-permissions-contract.md](admin-permissions-contract.md)
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

## Historical Inventories And Evidence

These are source/evidence inventories. They must not be read as current
implementation order or as a competing backlog.

- [player-data-inventory.md](player-data-inventory.md)
- [player-log-event-inventory.md](player-log-event-inventory.md)
- [player-data-truth-readonly-audit.md](player-data-truth-readonly-audit.md)

## Recurring Runbooks

These documents define procedures that are invoked by checklist gates. An
unchecked recurring procedure is not a permanent feature backlog item.

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
5. archive internal history instead of letting another status tracker emerge.
