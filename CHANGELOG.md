# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog, and the project aims to follow
Semantic Versioning once public releases begin.

## [Unreleased]

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
