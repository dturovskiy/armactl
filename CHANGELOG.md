# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog, and the project aims to follow
Semantic Versioning once public releases begin.

## [Unreleased]

### Added
- Added a bounded active-log health warning for unusually large current
  `console.log`, `error.log`, or `script.log` files and repeated known engine
  error signatures, without exposing paths or raw lines.
- Added `armactl players bans` list/ban/unban/pending/retry commands as a
  confirmation-gated non-web adapter over the same typed native moderation
  service and recovery records used by the dashboard.
- Added `armactl incidents list/show` as a bounded, path-free CLI view of the
  same retained/inferred crash explanations used by the incidents page.
- Added an authenticated, bounded, redacted diagnostic-report download that
  reuses the existing web preview and `armactl report` builder.
- Added authenticated native ban/unban controls with reliable-identity targets,
  fixed duration choices, explicit confirmations, authoritative read-before-write
  verification, and operator-visible read-first recovery for uncertain outcomes.
- Added a supervised 15-second incident monitor that persistently captures bounded journal, engine-log, systemd, active-profile, and live-process evidence without restarting the game.
- Added deduplicated incident capture for sustained critical server FPS,
  including the healthy-to-critical transition, bounded Game Master/resource
  correlation, active profile, and live process evidence without automatic
  recovery.
- Added authenticated incident artifact viewing and explicit monitor health/storage status to the incident page and dashboard card.
- Added `armactl update profile check <name>` as a CLI fallback for the web profile compatibility canary without activating the tested profile.

### Changed
- Added a strict gradual `mypy` gate for the extracted restart-timer and
  systemd platform boundary, while leaving expansion into legacy modules as an
  explicit follow-up rather than suppressing their errors globally.
- Continued the behavior-preserving `service_manager.py` decomposition by
  moving pure restart-schedule parsing, systemctl command execution, generated
  unit/helper rendering, privileged file/timer operations, and read-only
  systemd status queries into the platform layer, while retaining the
  established facade and making platform adapter exports lazy to remove an
  import cycle without changing its API.
- Defined the public `main` integration as the sanitized local/free dashboard
  plus shared core, kept the separately maintained marketing website outside
  this repository, and removed deployment-specific VM names and local paths
  from public planning and acceptance documentation.
- Repair now refreshes the secure privileged helper before replacing systemd
  units, explicitly reports that the game remains stopped, and restores the
  bounded runtime files it writes to the instance owner after an intentional
  root bootstrap.
- Generated game-server units now stop retrying after five failed starts within
  ten minutes instead of allowing an unbounded deterministic restart storm.
- Consolidated unfinished project work into `docs/checklist.md`, converted
  completed plan checkboxes into historical acceptance records, and corrected
  stale player/moderation documentation after Slice 7d completion. A regression
  check now covers every public Markdown path and requires every current docs
  file to remain classified in the plan register.
- Generated game-server services now allow native core dumps so the host core handler can retain an authoritative backtrace after a future native crash.

### Fixed
- Preserved the public legacy `paths.logs_dir()` runtime location for existing
  callers while keeping the web file browser on its explicit centralized log
  root, avoiding a silent downstream path change during upgrade.
- Preserved the restart-schedule regex exports on the legacy
  `service_manager` facade after moving their implementation into the platform
  layer.
- Made shared service/config templates discoverable from an installed wheel as
  well as a Git checkout, so release-artifact installs no longer look in the
  Python standard-library directory and fail before rendering configuration.
- Kept web login throttling compatible with Python 3.10 by using
  `timezone.utc` instead of the Python 3.11-only `datetime.UTC` constant.
- Made the local host-test runner fail fast when a restricted sandbox blocks
  local socketpair communication needed by web `TestClient` tests, instead of
  appearing to hang on Python 3.14.
- Recognized an existing SteamID64 game admin by the explicitly mapped RCON
  IdentityId on the Admins player roster, so the add-admin button is not offered
  for that person; player names are never treated as identity proof.
- Reused an existing mapped SteamID64 in the shared Web/TUI admin mutation when
  its RCON UUID is submitted, preventing a duplicate official admin entry.
- Preserved the actionable pre-fatal canary lines in update failures, so a
  missing Workshop addon is reported by exact ID instead of being reduced to a
  generic `Unable to initialize the game` message; that ID can now drive
  per-mod incompatibility evidence.
- Identified missing Workshop addons by exact ID in startup status and incident
  evidence, correlated the ID back to the configured mod name, and collapsed a
  repeated outage into one incident with first/last-seen times and a counter.
- Reported systemd `auto-restart` as a restart loop in CLI status instead of
  calling the server stopped.
- Installation now stops with the real error when it cannot enable or start
  the generated game service, instead of reporting a false success.
- Rejected out-of-range friendly restart times before they reach systemd.
- Masked game, admin, and RCON passwords in `armactl config show` by default;
  operators can request the raw values explicitly with `--show-secrets`.
- Rejected malformed `game`/`game.mods` shapes with a controlled config error
  across list, add, remove, dedupe, and import workflows.
- Prevented fresh repeated startup log lines from leaving the dashboard in `Starting` indefinitely after telemetry failed to appear, and prevented a stale `Starting` marker from hiding a systemd auto-restart loop.
- Attributed native crashes immediately preceded by an unresolved `SAL_DroneBulletComponent` to the Realistic Combat Drones/FPV dependency path and retained the exact unknown-class evidence instead of reporting only a generic active-addon suspect.
- Allowed the generated privileged helper to manage the incident monitor service and timer after installation.
- Prevented append-only engine logs from recreating the same incident as their mtime changes, preserved the original PID and early process-artifact links during correlation, deduplicated retained journal context, and recognized systemd `status=11/SEGV` exits directly.
- Bounded stalled SteamCMD update attempts with a five-minute no-output watchdog, terminated the isolated process group before retry, kept failed candidate downloads from changing the active generation, and allowed the managed candidate directory to ignore unrelated parent Git markers without permitting Steam installs inside the source checkout.
- Kept canonical vanilla clean when its scenario or mod selection is changed through the basic or raw Config editor, and exposed a truthful post-save warning if profile reconciliation cannot complete.
- Prevented restart schedule edits from immediately running newly added past slots on persistent systemd timers.
- Allowed bounded scheduled restarts to recover through transient game-service `auto-restart` attempts before reporting failure.

### Validation
- Added a byte-preserving v0.5.3 runtime-tree upgrade acceptance covering
  config, Workshop payloads, profiles, admins, schedules, player data,
  backups, legacy logs, and rollback state; installed-wheel CI now also proves
  fresh runtime config generation and existing-server discovery outside the
  source checkout. Ruff and all 1695 tests pass locally on the audited diff.
- Audited the public merge delta against v0.5.3: all 48 baseline CLI command
  paths and all owned public symbols across 37 baseline Python modules remain
  available, including the explicitly retained web and systemd compatibility
  facades.
- Strict `mypy` passes for all seven modules in `armactl.platform`, with the
  check wired into every supported-Python CI job and no blanket suppression.
- Completed the `service_manager.py` platform-boundary extraction with direct
  and caller integration coverage; Ruff, all 1692 local tests, package build,
  and an installed-wheel smoke outside the source checkout passed.
- Public-boundary scans found no runtime databases, logs, backups, key files,
  deployment identifiers, or recognized secret-token patterns in the merge
  diff or built wheel/sdist; the tracked `.env.example` remains a placeholder
  and is not packaged.
- GitHub Actions run `35737242232` passed Ruff, all 1665 tests, and package
  build independently on Python 3.10, 3.11, and 3.12.
- The full local suite on CPython 3.14.4 passed all 1665 tests with one
  non-fatal `fork()` deprecation warning once local socket I/O was permitted;
  the blocked-socket preflight failed fast as intended.
- GitHub Actions run `35694522416` passed Ruff, all 1664 pytest cases, and
  package build for the mapped-admin roster fix.
- GitHub Actions run `35695653132` passed Ruff, all 1665 pytest cases, and
  package build for the shared mapped-admin duplicate guard.
- GitHub Actions run `35636874913` passed Ruff, all 1662 pytest cases, and
  package build for the actionable-canary and repair-bootstrap fixes.
- Canary-server live monitor acceptance retained bounded stale-telemetry and
  sustained-critical-FPS evidence on the same PID and recovered to fresh 120
  FPS with zero restarts; a primary server read-only pass captured no false
  incident and left its game PID untouched.
- Active-log anomaly checks passed bounded-tail, allowlist, symlink, path
  redaction, permission, and localization coverage; GitHub Actions passed Ruff,
  all `1616` tests, and package build for commit `c3c3816`.
- GitHub Actions passed Ruff, all `1608` tests, and package build for the
  non-web incident-history and native-moderation recovery adapters.
- Typed native moderation service, RCON, and localization coverage passed locally
  (`75 passed`); GitHub Actions passed Ruff, all `1591` tests, and package build
  for the authenticated native-moderation UI head.
- Focused lifecycle, telemetry, incident, and i18n coverage passed (`75 passed`), including prolonged startup telemetry timeout and systemd auto-restart precedence.
- Canary-server retained-incident acceptance on `32ec2e9` kept the game process untouched while the 15-second monitor and web view surfaced the bounded `SAL_DroneBulletComponent` evidence as a Realistic Combat Drones/FPV trigger correlation without claiming the final native owner.
- GitHub Actions passed Ruff, all 1572 pytest cases, and package build for the CLI profile-check head.
- Canary-server current-build acceptance passed named profile create, rename, delete, check, vanilla switch, and modded switch-back while preserving every non-profile config setting and leaving Workshop storage shared.
- Focused fallback, parked-profile retry, and failed-canary recovery pytest coverage (`6 passed`) in WSL; the canary policy toggle was also verified on/off without a game restart.
- Focused safe-update, i18n, and config/profile-selection pytest coverage (`39 passed`) in WSL.
- `.venv/bin/python -m pytest tests/test_installer.py tests/test_safe_update.py tests/test_paths.py -q` (`60 passed`) in WSL.
- `python3 -m pytest tests/test_service_manager.py tests/test_web_schedule.py tests/test_web_cli.py -q` in WSL.
- `python3 -m pytest -q` in WSL (`1559 passed`).
- `python3 -m ruff check .` in WSL.

### Operational notes
- Existing installations must regenerate the game-service unit with
  `armactl service install` to apply the bounded startup retry policy; this does
  not restart the running game service.
- Game-package updates use SteamCMD and isolated runtime directories; they do not run or depend on Git repository operations.
- Updating a restart schedule now preserves whether the timer was active and clears only its persistent trigger timestamp before rearming it.
- Incident evidence is stored per instance under `<data-root>/<instance>/incidents/`; the monitor records evidence only and never performs automatic game recovery.

## [0.5.3] - 2026-06-07

### Added
- Added regression coverage for large TUI mod lists and sidecar rollback behavior.
- Added an audit fix plan documenting completed reliability and safety findings.

### Changed
- Changed TUI mod input handling to collect all valid 16-character Workshop IDs from pasted text in one flow.
- Changed mod/admin metadata updates to roll back paired `config.json` and sidecar writes when one write fails.

### Fixed
- Fixed TUI bulk mod adding for large pasted mod lists without adding a mod-count limit.
- Fixed unsafe instance names reaching data-root paths or systemd unit-name generation.
- Fixed service generation continuing after a failed unit install.
- Fixed Telegram bot `.env` file writes to use owner-only permissions.
- Fixed addon cleanup path safety by skipping symlinks and revalidating paths before deletion.
- Fixed backup filename collisions during rapid repeated config saves.

### Validation
- `./scripts/run-host-tests -- tests -q`
- `python3 -m pytest -q` via `./scripts/run-host-tests` (`278 passed`)
- `python3 -m ruff check src tests` via `./scripts/run-host-tests`

### Operational notes
- Existing installations do not need a migration.
- No mod-count limit was added; operators can paste large mod lists into the TUI add-mod flow.
- Bot `.env` files written by this release are created with `0600` permissions.

## [0.5.2] - 2026-05-21

### Added
- Added generated runtime script synchronization for existing installations after template updates.
- Added `sync-generated` to refresh generated runtime launch files without running full repair.
- Added TUI operational server status from recent console logs.
- Added operational states for ready, starting, downloading mods, retrying downloads, mission/config errors, waiting for telemetry, and stale telemetry.

### Changed
- Manage Server now surfaces the real log-derived operational state in addition to systemd running/stopped status.

### Fixed
- Fixed stale generated launch scripts after updating armactl templates.
- Fixed FPS telemetry visibility for existing installations that still used the legacy profile path.
- Added retry handling for SteamCMD server download/update during install.
- Made clean installs more resilient to transient SteamCMD backend/network failures such as `Missing configuration`.

### Notes
- Existing installations should run `./armactl sync-generated` and restart the Arma Reforger service after updating.
- New installations work out of the box and retry transient SteamCMD install failures automatically.

## [0.5.1] - 2026-05-15

### Fixed
- Improved Telegram inline button responsiveness after idle periods by shortening `getUpdates` polling/read windows.
- Reduced Telegram status snapshot stalls by using a shorter Telegram-only A2S player-status timeout.
- Retried transient Telegram callback acknowledgement network failures.
- Treated stale Telegram callback acknowledgements as benign during delayed or restarted polling.
- Reduced RCON player roster latency for Players views.

### Changed
- Hardened Telegram API timeout handling for callback answers, message edits, replies, and polling.
- Dropped pending Telegram updates on bot startup to avoid processing stale callback queues.

### Added
- Added redacted `armactl report` diagnostics for troubleshooting bot/server state.

## [0.5.0] - 2026-05-15

### Added
- Added real Arma Reforger Dedicated Server FPS/frame-time telemetry from the server engine's `-logStats 10000` output.
- Added `Server FPS`, average frame time, maximum frame time, and telemetry age to `armactl status`.
- Added Server FPS/frame-time display to the TUI overview/status views.
- Added Server FPS/frame-time display to Telegram bot metrics.
- Added stale, missing, malformed, and unavailable telemetry handling for server FPS metrics.
- Added focused parser, CLI, TUI, Telegram, and generated start-script tests for FPS telemetry.

### Changed
- Generated `start-armareforger.sh` now starts `ArmaReforgerServer` with `-logStats 10000` before `-maxFPS`.
- Metrics now distinguish real server engine FPS from generic host CPU/RAM metrics.

### Notes
- Server FPS is parsed from the Arma Reforger engine log output and is not estimated from CPU usage.
- Existing installations should regenerate the start script and restart `armareforger.service` to enable FPS telemetry.

## [0.4.0] - 2026-05-15

### Added
- Added a unified full-width Textual TUI shell for the main menu and server management views.
- Added dashboard-style server management with horizontal navigation tabs.
- Added inline dashboard panels for overview, configuration summary, mods summary, schedule, Telegram bot, cleanup, logs, status, and ports.
- Added lightweight dashboard formatting helpers and focused TUI layout tests.

### Changed
- Replaced the old centered button-only main menu with a consistent shell layout.
- Replaced the left-sidebar dashboard prototype with top navigation that uses terminal width more effectively.
- Improved TUI action/navigation button sizing so English and Ukrainian labels remain visible.
- Kept deeper tools such as raw config editing, mods manager, cleanup actions, schedule editor, bot config, and live logs as dedicated screens where appropriate.

### Fixed
- Fixed stale main menu state after install, repair, or detect flows; the menu now updates without restarting `armactl`.
- Serialized main menu refreshes to avoid concurrent Textual DOM rebuilds.
- Fixed top navigation/action labels being truncated after dynamic context changes.

## [0.3.1] - 2026-05-13

### Fixed
- Fixed a Textual TUI crash in the mods manager when refreshing the installed
  mods list after add/remove/import/dedupe operations.

## [0.3.0] - 2026-05-13

### Added
- Added automated cleanup of local Arma Reforger addon files when mods are removed from `config.json`.
- Added maintenance cleanup for unused Workshop addon directories.
- Added cleanup metadata reporting for TUI and CLI mod operations.
- Added safeguards that prevent SteamCMD install, repair, discovery, and service generation from using the `armactl` source repository or any Git working tree as the server install directory.
- Added `.gitignore` rules for accidental Arma Reforger runtime artifacts in the repository root.
- Added regression tests for addon cleanup safety, symlink/path containment, ENOSPC retry behavior, config backup safety, install-dir validation, discovery, repair, and service generation.

### Changed
- Mod removal, import, replace, and clear flows now compute removed mod IDs and clean only addon directories for IDs no longer present in `game.mods`.
- `repair` now validates `install_dir` before running SteamCMD.
- Discovery now ignores unsafe install paths from `state.json`, systemd units, legacy paths, and manual discovery.
- Systemd service generation now validates runtime paths before rendering unit files.
- Config backup creation now rotates old backups before creating a new backup.

### Fixed
- Fixed disk-full crashes when saving config after removing mods by cleaning removed addon files and retrying once.
- Fixed stale Workshop addon directories accumulating under `config/addons` after mods are removed.
- Fixed unsafe repair behavior that could run SteamCMD with `+force_install_dir` pointed at the source repository.
- Fixed partial backup and temporary config files being left behind after failed writes.
- Fixed misleading TUI/CLI cleanup success reporting by using actual cleanup results instead of dry-run previews.

## [0.2.0]

### Added
- Added server package integrity tracking for armactl-managed installs.
- Added local package manifest creation after successful SteamCMD installs.
- Added install-in-progress markers to prevent interrupted downloads from being treated as complete.
- Added repair support for validating and completing incomplete or unverified server installs.

### Changed
- Server discovery now checks package integrity, SteamCMD app manifests, config presence, and install markers before reporting an instance as installed.
- `armactl status` and `armactl detect` now surface incomplete or unverified installations with actionable repair guidance.
- `armactl start` and `armactl restart` now fail early when the server config is missing.
- Installation smoke checks now verify package integrity metadata in addition to the server binary.
- Updated PR template and contributing guidance to require issue relevance, human review, and disclosure for automated contributions.
- Updated Ukrainian localization for integrity and repair messages.

### Fixed
- Avoid treating partial SteamCMD downloads or missing-config installs as ready-to-manage servers.
- Improved repair behavior when default install/config paths need to be inferred.

## [0.1.3]

### Fixed
- Synchronized in-repo version metadata after the `v0.1.2` release.
- Updated release-process examples to use generic semantic version tags.
- Cleaned up the package version module header text.

## [0.1.2]

### Fixed
- Corrected the privileged sudoers drop-in naming so Telegram bot control and
  schedule actions work without prompting for a password after reinstalling the
  bot service.
- Improved privileged helper diagnostics for mismatched Linux users between the
  bot service and the secure control channel.
- Hardened privileged-channel detection when sudoers files are unreadable.
- Updated Telegram bot troubleshooting and helper-related documentation.

## [0.1.1]

### Fixed
- Improved Telegram players menu formatting.
- Filtered BattlEye RCON noise from player roster output.
- Improved Arma Reforger player roster parsing for semicolon-delimited RCON output.
- Improved handling of player roster lines without slot suffixes.
- Expanded automated test coverage for RCON player roster parsing and fallback behavior.

## [0.1.0]

### Added
- End-to-end TUI flow for install, repair, config, mods, schedule, and logs.
- Repo-local launcher via `./armactl` with automatic bootstrap.
- Telegram bot integration with per-instance `.env` config and a dedicated
  `armactl-bot.service`.
- Runtime diagnostics, host metrics, config summaries, mod summaries, and
  player visibility in TUI and Telegram.
- Repair mode, host test runner, and localization scaffolding.

### Changed
- README now documents the repo-local workflow as the primary install path.
- Product docs are organized under `docs/`.
- Release and community-health scaffolding are now tracked in-repo.
