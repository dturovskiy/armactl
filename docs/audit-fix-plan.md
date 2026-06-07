# armactl Audit And Fix Plan

Branch: `fix/tui-mod-bulk-add-validation`

This document tracks the follow-up audit work after the TUI bulk mod add fix.
Keep it updated as fixes land so the remaining work is visible outside chat.

## Validation Baseline

- Mod bulk-add regression group passed through `scripts/run-host-tests`.
- Core grouped checks passed for config/state/mods/bot-core, network/report/status,
  cleanup/integrity/service-manager, and TUI dashboard/display/mod-manager.
- Remaining failures are classified as environment/test hermeticity or separate
  bugs, not as mod count limits.

## Findings

### High

- [x] Centralize instance-name validation.
  - Risk: raw `instance` values are used in instance paths and systemd unit
    names. A value such as `../../escape` can resolve outside the data root and
    alter generated unit paths.
  - Fix: add a strict validator in `armactl.paths`, use it in all
    instance-scoped path helpers, and use it in systemd unit-name helpers.

- [x] Protect Telegram bot `.env` permissions.
  - Risk: `ARMACTL_BOT_TOKEN` is written atomically but without private file
    mode enforcement.
  - Fix: write temp files with `0600`, replace atomically, and chmod the final
    file to `0600`.

### Medium

- [x] Make tests hermetic against Git markers above `/tmp`.
  - Risk: installer/discovery/repair/service tests assume pytest `tmp_path` or
    `/tmp/server` is outside a Git working tree. This is false in the current
    SFTP/GVFS dev environment.
  - Fix: patch the Git marker probe in tests that are not specifically testing
    Git-worktree rejection, and keep explicit rejection tests intact.

- [x] Make `generate_services()` fail fast on unit install failures.
  - Risk: daemon reload and timer restart can run after one or more unit files
    failed to install, producing misleading mixed results.
  - Fix: stop after the first failed install result and return the collected
    results without reload/restart.

- [x] Fix TUI test hang in `test_main_action_bar_buttons_fit_english_and_ukrainian_labels`.
  - Risk: the root app test times out on the final case, likely due to startup
    lifecycle or background worker behavior.
  - Fix: isolate the blocking startup side effect, make the test deterministic,
    and keep coverage for label sizing.

- [x] Add a transaction/reconciliation layer for config plus sidecar updates.
  - Risk: `config.json` and `mods-state.json`/`admins-state.json` are written
    as separate commits. A failure in the second write can leave active and
    disabled state inconsistent.
  - Fix: introduce a shared helper with rollback or post-failure reconciliation
    for two-file state transitions.

### Low

- [x] Harden `cleaner.py` path safety.
  - Risk: junk cleanup recursively scans and deletes logs/dumps/backups with
    weaker canonical/symlink guards than addon cleanup.
  - Fix: add canonical root checks, skip symlinks, and cover with tests.

- [x] Avoid backup filename collisions.
  - Risk: backup names use second-resolution timestamps and can collide during
    rapid saves.
  - Fix: use higher-resolution timestamps or deterministic collision suffixes.

## Execution Order

1. Instance-name validation and affected tests.
2. Test hermeticity for `/tmp/.git`.
3. Bot `.env` permissions.
4. `generate_services()` fail-fast behavior.
5. TUI hang.
6. Config/sidecar transaction layer.
7. Cleaner path safety.
8. Backup filename collision fix.

## Current Batch

- [x] Add plan file.
- [x] Implement instance-name validation.
- [x] Patch fragile tests around Git marker discovery.
- [x] Enforce bot `.env` permissions.
- [x] Make service generation fail fast.
- [x] Run targeted host checks through `scripts/run-host-tests`.

Validation completed:

- `scripts/run-host-tests -- tests/test_paths.py tests/test_service_manager.py tests/test_service_integration.py tests/test_installer.py tests/test_discovery.py tests/test_repair.py tests/test_bot_config.py -q`
  - Result: 80 passed, ruff clean.
- `scripts/run-host-tests -- tests --ignore=tests/test_tui_app.py -q`
  - Result: 262 passed, ruff clean.

Second batch completed:

- Backup filenames now use collision-safe nanosecond timestamps.
- Junk cleanup now skips symlinks and re-validates files before deletion.
- `scripts/run-host-tests -- tests/test_config_manager.py tests/test_cleaner.py -q`
  - Result: 8 passed, ruff clean.
- `scripts/run-host-tests -- tests --ignore=tests/test_tui_app.py -q`
  - Result: 266 passed, ruff clean.

Third batch completed:

- TUI root app sizing test no longer leaves a Textual worker active during
  `asyncio.run()` cleanup.
- `scripts/run-host-tests -- tests/test_tui_app.py -q`
  - Result: 7 passed, ruff clean.
- `scripts/run-host-tests -- tests -q`
  - Result: 273 passed, ruff clean.

Fourth batch completed:

- Mods/admin manager paths now roll back config or sidecar writes when the
  paired write fails.
- Legacy disabled-mod migration restores sidecar state if config save fails.
- `scripts/run-host-tests -- tests/test_disabled_mods_workflow.py tests/test_mods.py tests/test_addon_cleanup.py tests/test_config_validation_sidecar.py tests/test_admins_manager.py -q`
  - Result: 54 passed, ruff clean.
- `scripts/run-host-tests -- tests -q`
  - Result: 278 passed, ruff clean.
