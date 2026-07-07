# Reuse, SOLID, And Duplication Audit

This audit was run before runtime safe file editor work and before broader
feature expansion. It is a static architecture audit only; it does not implement
editor routes, UI, save handlers, service refactors, deployment changes, or
production operations.

## Executive Conclusion

No P0 blockers were found.

The audit found one P1 gate before starting the runtime safe file editor
UI/save route: add a minimal service-layer reuse helper around the existing
replacement pipeline so the editor can save text with stale-baseline protection
without copying validation, backup, atomic publish, audit, or restart-pending
behavior. That gate is now closed by the file_replacements editor read/save
helper. The next slice can be the thin editor route/template layer over that
helper.

The existing architecture already has clear owners for the major contracts:
config_edit owns guarded config.json editing, file_replacements owns allowlisted
config/profile replacement, mutation_recovery owns restart-pending fallback
markers, filesystem_* owns browser root/path containment and preview behavior,
the job store/runner owns active job and lease semantics, and player_registry
plus player_current_cache separate persisted player/session truth from
current-roster snapshots.

## P0 Blockers

None.

## P1 Should-Fix Before File Editor Or Broad Features

### 1. Add An Editor-Safe Replacement Helper Before UI/Route Work

Status: closed.

replace_file_and_audit(...) already staged uploaded bytes, validated content,
created a backup, atomically published, audited intent/outcome, and marked
restart-pending work. The follow-up helper slice added an editor-oriented API
for reading an editable text target and rejecting a stale baseline before intent
audit, backup, or publish.

This keeps the runtime editor route/template slice from duplicating target
resolution, UTF-8 validation, JSON validation, fingerprint checks, staging,
backup, audit details, or pending-restart handling.

Minimum refactor points:

- Add EditableReplacement in src/armactl/web/services/file_replacements.py.
  It should contain root id, relative path, directory href, file kind, size,
  text, and baseline fingerprint. It must not expose absolute paths.
- Add read_editable_replacement_text(...) in
  src/armactl/web/services/file_replacements.py. It should reuse
  _validated_replacement_target(...), the replacement allowlist, bounded reads,
  UTF-8/control checks, and config/profile file-kind detection. For config.json,
  it must use config_edit.build_raw_config_editor_text(...).
- Add replace_text_and_audit(...) or extend replace_file_and_audit(...) with an
  expected_baseline_fingerprint argument. The stale check must happen after
  target re-resolution and before intent audit, backup, pending work, or publish.
  For config.json, validation must keep using
  config_edit.validate_raw_config_replacement(...).
- Keep route/template code thin: auth, permission, CSRF, DTO extraction, service
  call, and controlled response only.

Tests that should lock this refactor:

- tests/test_web_files.py for editable read target allowlist, no absolute path
  exposure, invalid UTF-8/binary/oversize/secret rejection before render, stale
  baseline rejection, no-op behavior, and reuse of replacement publish/audit
  behavior.
- tests/test_web_config_edit.py for config.json secret placeholder restore and
  server config validation.
- tests/test_web_mutation_recovery.py for pending restart fallback behavior if
  the helper changes restart marker wiring.

## P2 Backlog

### 1. Consolidate Post-Mutation Workflow Boilerplate

Admin, mod, service, and schedule action services each implement a similar
intent-audit, run, outcome-audit pattern. The pattern is readable and tested,
but repeated. A future helper such as
src/armactl/web/services/mutation_workflows.py could centralize common result
flags, controlled audit-failure messages, and backend-success preservation while
leaving domain-specific validation in each service.

This is not required before the file editor.

### 2. Route Profile Cleanup Restart Tracking Through mutation_recovery

mod_profile_cleanup already uses the same primary/fallback restart-pending
semantics, but it calls pending_work.mark_restart_pending_for_state(...) and
pending_work.mark_restart_pending_fallback(...) directly. This duplicates the
fallback pattern now owned by mutation_recovery.

Suggested refactor:

- Replace the local fallback block in
  src/armactl/web/services/mod_profile_cleanup.py with
  mutation_recovery.mark_restart_pending_for_mutation(...).
- Lock with tests/test_mod_profile_cleanup.py and
  tests/test_web_mutation_recovery.py.

### 3. Avoid Another Atomic File Writer Copy

Directory fsync, temp-file staging, and os.replace(...) appear in several
legitimate domains: config writes, runtime settings, upload/replacement,
profile cleanup, service file generation, pending-work sidecars, and addon
cleanup. The web editor must not add another copy. It should reuse
file_replacements first; if multiple new file mutation flows appear later,
extract a tiny helper such as src/armactl/web/services/atomic_files.py with
fsync_file(...), fsync_directory(...), and publish_existing_file(...).

### 4. Reduce Background Job Enqueue Wrapper Repetition

Server jobs and player/session jobs all use the same broad pattern: audit
intent, ensure_*_job(...), start worker if created, return active/created
status. The job store remains the single source for active-job dedupe and
lease/heartbeat state, so this is not a correctness blocker. A future helper
could reduce repeated enqueue wrappers after job behavior settles.

### 5. Align Discord/Public Stats With Dashboard Snapshot Before Enrichment

/public/server-status.json correctly uses
web.page_models.dashboard.load_dashboard_snapshot(...), so dashboard and the
public JSON route share status precedence. src/armactl/public_stats.py, used for
Discord/text stats, still derives lifecycle and public player fields through its
own snapshot path. It already reuses the current-roster cache and has tests, but
richer Discord/player enrichment should first decide whether to consume the
dashboard snapshot or a shared public-status read model.

### 6. Add Low-Noise Dead-Code Tooling Later

Do not add broad dead-code tooling until there is an allowlist. The current
compatibility surfaces are intentional and tested.

## Reuse Map: Owning Modules And Contracts

- src/armactl/web/services/config_edit.py: safe config form descriptors,
  guarded raw config.json parsing, server config validation, secret placeholder
  restore/rejection, config changed-field detection, config backup naming,
  config save audit, and config restart-pending state fingerprints.
- src/armactl/web/services/file_replacements.py: /files/config replacement
  allowlist, target validation, symlink rejection, UTF-8/control validation,
  generic JSON validation, config.json delegation to config_edit, replacement
  backup, same-directory temp staging, atomic publish, audit details, and
  restart-pending tracking for replacement saves.
- src/armactl/web/services/mutation_recovery.py: shared post-mutation
  restart-pending marker with fallback sidecar and controlled error/warning
  status. Callers still own validation, intent audit, backup/stage/apply, and
  outcome audit.
- src/armactl/web/services/pending_work.py: pending-work storage, fallback
  sidecar storage, state fingerprints, return-to-baseline clearing, and
  restart-pending clear helpers.
- src/armactl/web/services/filesystem_paths.py: relative POSIX path parsing,
  root jail checks, forbidden .git/.venv, system/source-tree denial, and
  parent/child relative path helpers.
- src/armactl/web/services/filesystem_roots.py: fixed browser roots, root
  availability, data-root containment, and upload root policy.
- src/armactl/web/services/filesystem_listing.py: listing DTOs, breadcrumbs,
  safe metadata, preview link visibility, download link visibility, and replace
  link visibility.
- src/armactl/web/services/filesystem_preview.py: bounded text preview and
  preview-only redaction. Editor code must not save redacted preview text.
- src/armactl/web/services/filesystem_transfer.py: safe upload/download filename
  handling, no-overwrite upload staging/publish, and download targets.
- src/armactl/web/jobs/store.py and src/armactl/web/jobs/runner.py: job row
  state, active job dedupe, duplicate queued repair during mutating enqueue,
  worker token, heartbeat, lease, bounded redacted output, and terminal state
  transitions.
- src/armactl/web/services/job_integrity.py and
  src/armactl/web/runtime/job_store_maintenance.py: read-only diagnostics and
  mutating duplicate queued repair semantics.
- src/armactl/web/services/player_registry.py: persisted known-player,
  player-log-event, player-session, absence-window, stale-close, and retention
  truth in players.db.
- src/armactl/web/services/player_current_cache.py: runtime and web.db
  current-roster snapshots, freshness/stale fallback, count-only handling, and
  safe current-roster DTOs. This is not session truth.
- src/armactl/web/services/player_sources.py: live RCON/A2S current-roster
  collection with sanitized source labels.
- src/armactl/web/page_models/dashboard.py: dashboard snapshot and operational
  status precedence used by dashboard and public JSON status.
- src/armactl/public_stats.py: separate public/Discord stats snapshot. Reuse or
  align this before future Discord enrichment.

## Duplication And Source-Of-Truth Findings

- Secret placeholder and restore logic is not duplicated today. It lives in
  config_edit and is reused by file_replacements for config.json.
- Raw config.json validation is not duplicated today. file_replacements
  delegates to config_edit.validate_raw_config_replacement(...).
- Generic JSON validation exists in file_replacements for non-config JSON
  replacement targets. The file editor must call that path or an extracted
  helper instead of adding a third JSON parser/error path.
- Stale baseline/fingerprint support exists in pending_work and replacement
  fingerprints, but the editor-specific stale-save guard is not exposed as a
  public service API yet. This is the P1 file-editor gate.
- Backup/stage/atomic publish/fsync behavior exists in file_replacements and
  should be reused by the editor. mod_profile_cleanup has a similar local
  pipeline for a domain-specific cleanup flow; future new file mutations should
  not copy it.
- Audit logging has a central append-only writer, but several action services
  repeat intent/outcome wrapper code. This is P2 boilerplate, not a current
  correctness blocker.
- Restart-pending fallback has an owner in mutation_recovery; most config, file
  replacement, admin, and mod paths use it. mod_profile_cleanup still duplicates
  the fallback directly and should be cleaned up later.
- Path/root containment for file browser flows is centralized under
  filesystem_*. mod_profile_cleanup reuses the low-level containment helpers for
  its narrower profile-settings cleanup candidates.
- Preview/read/redaction logic is preview-only. Slice 2 editor GET must not
  render redacted non-config text as editable content; it should reject
  secret-bearing files unless a target-specific placeholder/restore validator
  exists.
- Job active/dedupe/lease semantics have a single store owner. Feature-specific
  enqueue services are repetitive but still route through ensure_*_job(...) and
  start_*_worker(...).
- Player data truth is separated: current-roster cache is a fresh/stale
  observation cache, players.db is persisted identity/history/session truth, and
  session writes go through observe_player_session(...) and close_player_session(...).
  UI/tests avoid current-session K/D/playtime/role claims.
- Dashboard and /public/server-status.json share load_dashboard_snapshot(...) and
  the same operational precedence. Discord/public stats still have a separate
  snapshot path and should be aligned before enrichment.

## Dead-Code And Compatibility Classification

Keep with tests:

- src/armactl/web/facade.py: legacy page-model re-export surface covered by
  tests/test_web_facade.py.
- src/armactl/web/services/filesystem.py: legacy filesystem facade covered by
  tests/test_web_files.py.
- src/armactl/web/services/pending_restart.py: legacy pending-restart adapter
  covered by tests/test_web_pending_restart.py.
- POST /players/refresh: compatibility alias for current-roster refresh covered
  by tests/test_web_player_registry.py.

Deprecate later:

- /players/refresh can be retired only after route/docs consumers have moved to
  /players/refresh-current.
- The facade modules can be retired only after a downstream import window or
  public merge decision confirms they are no longer needed.

Safe to remove later:

- None identified in this audit without adding noisy dead-code tooling.

Unsafe to touch before merge:

- Compatibility facades and alias routes above.
- Public/private docs and dashboard extraction boundary content outside the
  current docs gate.
- Production, SSH, deploy, restart, or service state.

## Recommended Next Slice

The file editor reuse helper is now in place. The next slice can implement the
runtime safe file editor as a thin route/template/UI layer over
file_replacements:

- Use the editor read/save service API in file_replacements.
- Reuse config_edit for config.json.
- Reuse mutation_recovery for pending restart.
- Keep routes/templates limited to auth, permissions, CSRF, DTO extraction,
  rendering, and controlled responses.
- Do not add delete, rename, move, copy, bulk, arbitrary path editing, or a
  general-purpose file manager.

Broader action/job/public-stats cleanup can stay P2 unless those areas become
the next feature expansion target.
