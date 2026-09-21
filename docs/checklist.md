# Active Work Checklist

This is the single status tracker for unfinished work in the public/free,
local-first `armactl` repository.

Rules for this file:

- keep only open or explicitly decision-gated work here;
- remove completed items after their outcome is recorded in `CHANGELOG.md`, the
  relevant contract, or Git history;
- use detailed plan/contract documents for requirements, not competing status
  checklists;
- keep private/commercial `armactl-dashboard` planning in the private docs
  repository and do not duplicate it here;
- treat deployment, hardening, and release runbooks as recurring procedures,
  invoked only by the gates below.

The document classification is maintained in
[plans-register.md](plans-register.md).

## Closed Milestone Index

This is a navigation index, not a second checklist. Completed implementation
and acceptance detail remains in the linked historical records and Git history.

| Milestone | Closed result | Record |
| --- | --- | --- |
| Update/profile safety foundation | Profile create/rename/delete and manual switching were accepted on Serhiivka; switching changes only scenario/mod selection, leaves Workshop payloads in place, and deterministic recovery tests park failed canaries for retry. | [Server update compatibility](server-update-compatibility.md) |
| Startup and incident diagnosis foundation | Startup states distinguish service, telemetry, failure, and crash states; retained incidents expose bounded evidence and suspects. The Serhiivka timer and retained-incident presentation baseline are accepted. | [Incident monitoring](incident-monitoring.md) |
| P1 operator-critical parity | Critical web mutations have an intentional CLI or TUI recovery path backed by shared services; rich read-only presentation remains web-first where documented. | [Web dashboard parity matrix](web-interface-plan.md#7-clituiweb-operational-parity) |
| Admin/GM synchronization implementation | Web and TUI use one transactional synchronization service for `game.admins` and supported SAT/WCS roles with backup and rollback. | [Admin permission contract](admin-permissions-contract.md) |
| Native moderation through Slice 7d | Typed native ban/unban service, authenticated mutation UI, and CLI fallback are implemented with authoritative verification and recovery records. | [Banlist and moderation contract](banlist-moderation-contract.md) |
| P3 operator diagnostics | Bounded report download, incident CLI history, and active-log anomaly warnings are implemented. | [Changelog](../CHANGELOG.md#unreleased) |
| Player/session foundation | Incremental ingest, supervised session processing, truth-gated stats, and authenticated session search/detail have completed implementation and staged acceptance. | [Plan register](plans-register.md#completed-implementation-references) |

## P0 - Stabilize The Current Update, Incident, And Admin Baseline

- [ ] Run the full local validation suite for the current branch and resolve any
  remaining CI failure before production acceptance.
- [ ] Complete the remaining persistent-incident-monitor acceptance defined in
  [incident-monitoring.md](incident-monitoring.md): exercise early live
  stale-telemetry and sustained-critical-FPS capture during an approved safe
  Serhiivka window, then perform the separately approved Chervonopilya
  acceptance without restarting an occupied game server. The Serhiivka
  15-second timer, journal access, redaction, retained artifact links, and
  core-capability report are already accepted.
- [ ] Verify an existing game admin is not offered the add-admin action and test
  full Game Master access with a designated non-`deus` identity.
- [ ] Complete staged acceptance on Serhiivka first and record the exact deployed
  commit and results.

Detailed update behavior is defined in
[server-update-compatibility.md](server-update-compatibility.md). Deployment
acceptance must not copy runtime config, profiles, addons, or scenario payloads
between the two servers.

## P2 - Finish Native Moderation

- [ ] Complete Slice 7e Serhiivka-first read/mutation/retry/recovery acceptance
  with a designated test identity and audited action.
- [ ] Review authoritative ban state, journals, audit, and recovery state before
  any explicitly approved Chervonopilya rollout.

The detailed contract remains
[banlist-moderation-contract.md](banlist-moderation-contract.md).

## P4 - Prepare `feat/web-interface` For Public `main`

### 4.1 Public Scope And Integration Decision

- [ ] Decide explicitly whether public `main` receives a sanitized local/free
  dashboard merge or selected public-core backports; do not merge the current
  branch as-is by default.
- [ ] Confirm the public boundary contains only the free/local Arma-specific
  core, CLI/TUI, local backend, and approved local dashboard surfaces.
- [ ] Confirm billing, subscriptions, tenants, organizations, hosted identity,
  hub orchestration, provisioning, commercial entitlements, and private
  infrastructure remain outside the public repository.
- [ ] Keep any future private dashboard snapshot/extraction decision independent
  from the public merge and tracked only in private documentation.

### 4.2 Documentation Boundary

- [ ] Sanitize public docs and examples for private hostnames, IPs, routes,
  provider details, topology, credentials, and operator-only identifiers.
- [ ] Reduce `README.md`, architecture, roadmap, troubleshooting, and web
  deployment documentation to stable public/free behavior and links.

### 4.3 Upgrade, Architecture, And Compatibility Review

- [ ] Audit `main...feat/web-interface` for public API, CLI, config-schema,
  runtime-layout, SQLite migration, systemd-unit, and packaging compatibility.
- [ ] Verify an existing v0.5.3 installation can upgrade without losing config,
  mods, profiles, admins, schedules, player data, or rollback state.
- [ ] Verify a fresh install and existing-server discovery from a built release
  artifact on a clean supported Ubuntu environment.
- [ ] Confirm runtime data, backups, logs, databases, secrets, VM-specific files,
  and private operator notes are absent from the merge diff and package.
- [ ] Review retained compatibility facades and alias routes; remove none solely
  from heuristic dead-code output before the downstream compatibility window.
- [ ] Re-audit current module ownership and call sites, starting with
  `service_manager.py`, and record which responsibilities belong to shared
  platform contracts, Linux/systemd implementations, or compatibility facades.
- [ ] Decide explicitly which `service_manager.py` extractions are public-main
  merge requirements and which remain decision-gated; do not combine a
  behavior-preserving decomposition with unrelated operator features.
- [ ] For each approved extraction, separate systemd execution, privileged
  operations, unit rendering, service status, and restart-timer behavior behind
  tested boundaries while retaining `service_manager.py` as a compatibility
  facade for the supported downstream window.
- [ ] Prove CLI, TUI, web, bot, installer, and recovery callers retain the same
  operator behavior and use the intended shared backend or platform adapter
  after each extraction.
- [ ] Define the supported-Python policy, then align `requires-python`, package
  classifiers, development documentation, local validation, and CI with that
  policy instead of implying untested runtime support.

### 4.4 Final Validation And Production Smoke

- [ ] Run `git diff --check`, Ruff, the full pytest suite, wrapper/bootstrap
  checks, and package build/install smoke.
- [ ] Run the test suite in GitHub Actions on every explicitly supported Python
  runtime, including 3.10, 3.11, and 3.12 while those classifiers remain
  published.
- [ ] Add a gradual `mypy` or `pyright` check with an explicit initial scope,
  checked configuration, and no blanket suppression of existing errors; expand
  the enforced surface as module boundaries are stabilized.
- [ ] Require all GitHub Actions checks to pass on the exact proposed merge head.
- [ ] Run secret, infrastructure-identifier, generated-file, and large-artifact
  scans over the complete merge diff and built artifacts.
- [ ] Verify localhost-first binding, HTTPS-required cookie responsibility,
  login throttling, CSRF, schema readiness, audit redaction, and gateway/firewall
  assumptions against the public deployment and hardening runbooks.
- [ ] Re-run the network/deployment checklist if gateway, nginx, firewall, VPN,
  bind, TLS, or routing state changed.
- [ ] Smoke CLI and TUI install/repair/status/config/mod/schedule/log/report and
  all operator-critical parity paths.
- [ ] Smoke authenticated web login, dashboard, config, mods, admins, schedule,
  files, logs, report, jobs, updates, profiles, incidents, players, sessions,
  moderation, bot, service controls, health, readiness, and public status.
- [ ] Re-run Serhiivka-first production acceptance on the exact merge candidate;
  keep Chervonopilya changes approval-gated and non-disruptive while occupied.
- [ ] Confirm no new traceback, HTTP 500, failed unit, restart loop, stale worker,
  secret exposure, or unexplained telemetry state appears during the observation
  window.
- [ ] Refresh public screenshots after visible UI changes and verify that their
  text and capability claims match the merge candidate.

### 4.5 Merge Gate

- [ ] Produce a final merge-readiness report listing the chosen public scope,
  intentional parity differences, validation evidence, production evidence,
  migrations, operator actions, rollback path, and accepted residual risks.
- [ ] Confirm the branch is clean, synchronized, based on the intended public
  `main`, and contains no unresolved conflict or unrelated private work.
- [ ] Open the public-main PR only after Sections 4.1-4.4 are complete.
- [ ] Merge only after required review and CI; do not treat deployment smoke or a
  passing local suite alone as merge approval.

## P5 - Release After Public Merge

- [ ] Select the next version from the actual change scope and prepare a separate
  release PR using [release-process.md](release-process.md).
- [ ] Update only the release source-of-truth files allowed by repository policy,
  build the artifacts, and require CI before tagging.
- [ ] Install the release artifact on a clean supported environment and verify
  upgrade/recovery notes before publishing the GitHub Release.

## Decision-Gated Backlog

These items are not current merge blockers. Activate an item only after its gate
is explicitly satisfied.

- [ ] Add kick only after fresh reliable roster resolution, exact identity plus
  current player ID, immediate re-resolution, and response fixtures are proven.
- [ ] Define a separate IP moderation/privacy contract before storing, searching,
  displaying, or acting on player IP data.
- [ ] Expand safe config controls one field group at a time only after validation,
  backup, restart, rollback, and VM behavior are proven for that group.
- [ ] Choose Discord/public player enrichment scope before adding combat/session
  columns; keep Role unavailable without a reliable source.
- [ ] Design live job cancellation only with a real worker lease, cancellation,
  and process-termination contract; abandoned metadata is not cancellation.
- [ ] Add mod quarantine/restore before expanding destructive cleanup beyond the
  current bounded `config/addons` behavior.
- [ ] Add CLI current-roster cache status only if authenticated web diagnostics
  prove insufficient for operators.
- [ ] Add a player-session JSON API or bulk export only after a demonstrated
  operator need; keep authenticated server-rendered search/detail as the current
  supported surface.
- [ ] Consolidate audit/enqueue wrappers, add dead-code tooling, or retire tested
  compatibility facades only when a concrete downstream need and safe migration
  window exist.

## Fixed Operating Constraints

These are rules, not checklist items:

- production deployment is Git-only;
- never copy runtime payloads or configuration from Serhiivka to Chervonopilya
  or in the opposite direction;
- profile switching ignores inactive Workshop addons; it never clones, moves, or
  deletes their files;
- preserve per-instance configuration and use its own update baselines and
  infrastructure snapshots;
- do not restart Chervonopilya while players are present without explicit
  approval;
- use Serhiivka-first rollout whenever a change can affect the game process,
  configuration, mods, profiles, permissions, or persistent data.
