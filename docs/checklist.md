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

## P0 - Stabilize The Current Update, Incident, And Admin Baseline

- [ ] Run the full local validation suite for the current branch and resolve any
  remaining CI failure before production acceptance.
- [ ] Verify the game update path depends on SteamCMD/game-package state, not on
  Git checkout cleanliness, fetchability, or repository deployment state.
- [ ] Verify transient SteamCMD connection/download failures produce a bounded,
  actionable failure and can be retried without corrupting the active package,
  config, or compatibility state.
- [ ] Verify the pre-update baseline is written under
  `<instance>/backups/update-baselines/<UTC timestamp>/` and contains the config
  hash, non-addon profile, addon inventory, metadata, and manifest.
- [ ] Verify profile create, rename, delete, compatibility check, manual switch,
  and automatic vanilla fallback against the current server build.
- [ ] Verify editing mods/scenario while clean vanilla is active creates or
  selects a named modified profile instead of mutating the canonical vanilla
  definition.
- [ ] Verify profile switching changes only `game.scenarioId` and `game.mods`;
  admins, passwords, player limits, ports, RCON, persistence, server name, and
  all Workshop addon payloads must remain unchanged.
- [ ] Verify a failed modded canary leaves its profile parked and usable for a
  later retry, while a failed vanilla canary leaves the active package/profile
  unchanged.
- [ ] Verify dashboard startup state distinguishes service activation, telemetry
  readiness, controlled shutdown, startup failure, and native crash instead of
  showing indefinite unexplained telemetry waiting.
- [ ] Verify retained incidents expose bounded evidence, confidence, and likely
  mod/scenario suspects without claiming certainty or exposing raw paths,
  secrets, or unbounded log output.
- [ ] Verify an existing game admin is not offered the add-admin action and test
  full Game Master access with a designated non-`deus` identity.
- [ ] Complete staged acceptance on Serhiivka first and record the exact deployed
  commit and results.
- [ ] Keep Chervonopilya read-only while players are present; deploy or restart
  its game service only after explicit approval and a safe maintenance window.

Detailed update behavior is defined in
[server-update-compatibility.md](server-update-compatibility.md). Deployment
acceptance must not copy runtime config, profiles, addons, or scenario payloads
between the two servers.

## P1 - Reopen And Finish Operator-Critical CLI/TUI/Web Parity

- [ ] Re-audit the actual current CLI commands, TUI screens, web routes, and
  shared backend owners; the existing parity table predates the compatibility
  profile and incident work.
- [ ] Approve one explicit operational-parity rule: every critical mutation and
  recovery flow must use a shared backend and remain operable without a working
  web process through CLI and, where required by the operator workflow, TUI.
- [ ] Classify status/service controls, install/repair, config, mods, admins,
  schedule, logs/report, update/check/rollback, compatibility profiles, vanilla
  fallback, and incident diagnosis against that rule.
- [ ] Implement or explicitly document every critical gap found by the audit;
  do not mark parity complete merely because a difference was classified.
- [ ] Keep rich player/session tables and other read-heavy presentation web-first
  only where there is a deliberate operator decision and a reliable CLI/TUI
  recovery path is not required.
- [ ] Add focused shared-backend and adapter tests for each parity gap closed.

## P2 - Finish Native Moderation

- [ ] Implement Slice 7d authenticated ban/unban mutation UI using the existing
  typed native moderation service.
- [ ] Keep mutations POST-only, CSRF-protected, and gated by
  `players:moderate`; require reliable identity and separate confirmations.
- [ ] Render bounded changed, no-op, failed, uncertain, and recovery outcomes
  without IPs, raw commands/responses, secrets, paths, or tracebacks.
- [ ] Add focused permission, route, template, CSRF, read-only GET, and
  sensitive-output regression coverage.
- [ ] Complete Slice 7e Serhiivka-first read/mutation/retry/recovery acceptance
  with a designated test identity and audited action.
- [ ] Review authoritative ban state, journals, audit, and recovery state before
  any explicitly approved Chervonopilya rollout.

The detailed contract remains
[banlist-moderation-contract.md](banlist-moderation-contract.md).

## P3 - Close Remaining Operator Diagnostics

- [ ] Add one authenticated, bounded, redacted diagnostic report download/export
  response that reuses the existing report builder and keeps `armactl report` as
  the CLI fallback.
- [ ] Add a bounded operator-visible warning when active `console.log`,
  `error.log`, or `script.log` becomes anomalously large or matches the defined
  spam signal.
- [ ] Keep log anomaly checks bounded and expose neither raw filesystem paths nor
  unbounded raw log lines.

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

- [ ] Apply the keep/trim/move/archive classification to every public planning,
  audit, handoff, deployment, player-data, and hardening document.
- [ ] Move or archive internal implementation histories instead of leaving
  completed plans mixed with active public work.
- [ ] Sanitize public docs and examples for private hostnames, IPs, routes,
  provider details, topology, credentials, and operator-only identifiers.
- [ ] Reduce `README.md`, architecture, roadmap, troubleshooting, and web
  deployment documentation to stable public/free behavior and links.
- [ ] Confirm this file remains the only public unfinished-work checklist and
  detailed contracts contain no competing task-status checkboxes.

### 4.3 Upgrade And Compatibility Review

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

### 4.4 Final Validation And Production Smoke

- [ ] Run `git diff --check`, Ruff, the full pytest suite, wrapper/bootstrap
  checks, and package build/install smoke.
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
