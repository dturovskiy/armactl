# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog, and the project aims to follow
Semantic Versioning once public releases begin.

## [Unreleased]

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
