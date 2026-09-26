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
| Update/profile safety foundation | Profile create/rename/delete and manual switching were accepted on canary server; switching changes only scenario/mod selection, leaves Workshop payloads in place, and deterministic recovery tests park failed canaries for retry. | [Server update compatibility](server-update-compatibility.md) |
| Startup and incident diagnosis foundation | Startup states distinguish service, telemetry, failure, and crash states; retained incidents expose bounded evidence and suspects. The canary server timer and retained-incident presentation baseline are accepted. | [Incident monitoring](incident-monitoring.md) |
| P1 operator-critical parity | Critical web mutations have an intentional CLI or TUI recovery path backed by shared services; rich read-only presentation remains web-first where documented. | [Web dashboard parity matrix](web-interface-plan.md#7-clituiweb-operational-parity) |
| Admin/GM synchronization implementation | Web and TUI use one transactional synchronization service for `game.admins` and supported SAT/WCS roles with backup and rollback. | [Admin permission contract](admin-permissions-contract.md) |
| Native moderation through Slice 7d | Typed native ban/unban service, authenticated mutation UI, and CLI fallback are implemented with authoritative verification and recovery records. | [Banlist and moderation contract](banlist-moderation-contract.md) |
| P3 operator diagnostics | Bounded report download, incident CLI history, and active-log anomaly warnings are implemented. | [Changelog](../CHANGELOG.md#unreleased) |
| Player/session foundation | Incremental ingest, supervised session processing, truth-gated stats, and authenticated session search/detail have completed implementation and staged acceptance. | [Plan register](plans-register.md#completed-implementation-references) |

## P0 - Stabilize The Current Update, Incident, And Admin Baseline

- [ ] Complete canary-first live Game Master acceptance with a designated
  non-owner identity while the relevant mods are safely loaded; verify the
  intended in-game privileges and record the result. The existing-admin UI
  and shared Web/TUI identity-mapping fixes are implemented; see the
  [admin permission contract](admin-permissions-contract.md).

Detailed update behavior is defined in
[server-update-compatibility.md](server-update-compatibility.md). Deployment
acceptance must not copy runtime config, profiles, addons, or scenario payloads
between the two servers.

## P2 - Finish Native Moderation

- [ ] Complete Slice 7e canary-first read/mutation/retry/recovery acceptance
  with a designated test identity and audited action.
- [ ] Review authoritative ban state, journals, audit, and recovery state before
  any explicitly approved primary server rollout.

The detailed contract remains
[banlist-moderation-contract.md](banlist-moderation-contract.md).

## P3 - Reduce High-Risk Architecture Debt

The current audit no longer treats raw file length as sufficient evidence of a
god object. `service_manager.py` remains a compatibility facade, but systemd
execution, privileged operations, rendering, status, and timer behavior are
already implemented in focused platform modules. The extension
`background.js` cited by an earlier external review is not part of this
repository.

Strict `mypy` now covers 21 modules: the complete platform package plus the
update state machine, separated FPS and host/process metrics, bounded server-log
I/O, log diagnostics, operational status, incident analysis, metric formatting,
shared metric models, service facade, runtime settings, restart timing, and the
player-registry schema/migration boundary.
It runs in every Python 3.10-3.12 CI job without blanket error suppression.

- [ ] Split `web/services/player_registry.py` behind its existing public facade
  into schema/migrations, ingest/checkpoint storage, session mutation, and
  read/query/summary modules. Preserve the SQLite schema, migration ordering,
  transaction boundaries, and public DTO/function contracts while expanding
  strict type checking over each extracted boundary.
  Schema extraction is in progress: private database creation, read-only/write
  connection lifecycle, reusable SQLite schema primitives, and the exact
  version 1-14 migration coordinator now live in the strictly typed
  `player_registry_schema.py` module. The facade retains its established names
  and supplies the unchanged table-specific migration steps; moving those DDL
  steps and the remaining storage/query boundaries is still open.
- [ ] Break the 500-line `run_player_session_scheduler_once` orchestration into
  typed scan, ingest, sessionization, stale-close, retention, and result phases.
  Preserve the single-writer lock, checkpoint commit ordering, partial-failure
  semantics, and supervised oneshot/timer contract.
- [ ] Split `safe_update.py` by transaction phase: candidate/download lifecycle,
  compatibility canary, named-profile catalog/switching, promotion, and
  rollback/recovery. Preserve the public module contract and byte-preserving
  config/profile/addon guarantees; do not combine this refactor with behavior
  changes to a live update.
Completed metrics-facade extraction: host/process collection, formatting,
bounded log I/O, shared safe predicates, FPS parsing, operational
classification, incident analysis, and DTOs now have focused modules. The
220-line `metrics.py` facade retains the established public functions, DTOs,
regex, thresholds, and test seams. Add new AI, vehicle, projectile, or
dynamic-entity fields only in their owning focused modules.
- [ ] Split the 1,100-line TUI `ManageScreen` into focused panel/controller
  classes that continue to call shared backend services. Do not duplicate web
  workflows or move backend rules into Textual event handlers.

The large `cli.py` command registry is not currently classified as a god object:
its commands are mostly thin adapters. Revisit it only where complexity or
duplicated backend behavior is demonstrated. Retiring the `service_manager`
facade is likewise a separate downstream migration, not a line-count-only
rewrite during this merge window.

## P4 - Prepare `feat/web-interface` For Public `main`

### 4.1 Public Scope And Integration Decision

The selected integration is the sanitized local/free dashboard plus its shared
core. Its public boundary and the separate website/private-dashboard decisions
are recorded in [roadmap.md](roadmap.md#public-main-integration).

### 4.2 Documentation Boundary

Public docs and examples use placeholders or generic canary/primary roles and
contain no deployment-specific hostnames, external IPs, credentials, home
paths, or VM names. `README.md`, architecture, roadmap, troubleshooting, and
web deployment describe stable public/free behavior and reusable procedures.

### 4.3 Upgrade, Architecture, And Compatibility Review

Completed compatibility audit: all 48 baseline CLI command paths remain, the
37 baseline Python modules have no removed owned public symbols, established
systemd unit names remain stable, and the intentional secret masking on
`config show` is documented with `--show-secrets` as the explicit raw-value
path. The audit caught and restored the legacy `paths.logs_dir()` location and
the `service_manager` restart-schedule regex exports before merge.

Completed v0.5.3 runtime acceptance: a byte-preserving fixture covers config
including unknown operator fields and secrets, Workshop payloads, named
profiles, admin sidecars, restart schedules, player data, backups, legacy logs,
and update rollback state. Existing `web.db` and `players.db` migration suites,
package build, and installed-wheel discovery smoke pass without source-tree
imports. The real-system gate also passed on a clean Ubuntu 24.04 VM: Git-only
bootstrap installed SteamCMD and the current dedicated-server build, generated
the package manifest, config, state, and systemd units, reached fresh 60 FPS
telemetry with all configured UDP ports listening, and recovered automatically
after a VM reboot with no service restart loop. A separately installed wheel,
run outside the source checkout, discovered and reported the same live server.

Completed compatibility-surface review: the legacy web facade, filesystem
facade, pending-restart adapter, `/players/refresh` alias, lazy platform
exports, and `service_manager` facade remain intentional and have direct
identity or behavior regression tests. None were removed from heuristic
dead-code output.

Completed architecture boundary: systemd execution, privileged operations,
unit rendering, service status, and restart-timer behavior now sit behind
focused modules while `service_manager.py` remains the compatibility facade for
the supported downstream window. CLI, TUI, web, bot, installer, and recovery
callers retain the established backend contract. Direct boundary tests, caller
integration tests, the full 1692-test suite, package build, and installed-wheel
smoke passed on the completed extraction head.

### 4.4 Final Validation And Production Smoke

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
- [ ] Re-run canary-first production acceptance on the exact merge candidate;
  keep primary server changes approval-gated and non-disruptive while occupied.
- [ ] Confirm no new traceback, HTTP 500, failed unit, restart loop, stale worker,
  secret exposure, or unexplained telemetry state appears during the observation
  window.
- [ ] Refresh public screenshots after visible UI changes and verify that their
  text and capability claims match the merge candidate.

Completed local validation gate: `git diff --check`, Ruff, strict 21-module
`mypy`, all 1700 tests, wrapper/bootstrap checks, package build, clean wheel
install, and installed-wheel discovery/status smoke passed on Ubuntu 24.04.

Completed clean-canary update and authenticated-web evidence: the Git-only
checkout downloaded the candidate Steam package into isolated storage, reached
vanilla readiness in 75.4 seconds, atomically promoted it without changing the
semantic operator config, retained rollback state, and returned to stable 60
FPS with zero systemd restarts. Twenty private HTML/JSON routes returned HTTP
200 after login; infrastructure snapshot rollback removed the temporary owner,
and game/web auto-start recovered with health and readiness green.

Completed expanded type-check baseline: strict `mypy` now covers 21 modules,
including the complete `armactl.platform` package and the update, separated
FPS/host metrics, bounded server-log I/O, log diagnostics, lifecycle and
incident inference, formatting, shared models, and service/runtime-settings
boundaries, plus the player-registry schema/migration layer. It runs in the
Python 3.10-3.12 CI matrix and has no blanket error
suppression. Future expansion follows each extracted module boundary rather
than weakening the gate.

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
- never copy runtime payloads or configuration from canary server to primary server
  or in the opposite direction;
- profile switching ignores inactive Workshop addons; it never clones, moves, or
  deletes their files;
- preserve per-instance configuration and use its own update baselines and
  infrastructure snapshots;
- do not restart primary server while players are present without explicit
  approval;
- use canary-first rollout whenever a change can affect the game process,
  configuration, mods, profiles, permissions, or persistent data.
