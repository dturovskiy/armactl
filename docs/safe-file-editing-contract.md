# Safe File Editing Contract

This is the Slice 1 read-only design and allowlist audit for future narrow file
editing. It documents the contract for Slice 2; it does not implement an editor.

## Current File Browser Audit

The file browser exposes four fixed roots under the instance data root:

- `server` - server install files.
- `config` - instance config/profile files.
- `backups` - instance backups.
- `logs` - instance logs.

Roots are available only when they resolve inside the configured armactl data
root, are real directories, are not source-tree wrappers, are not system paths,
and do not include forbidden path segments such as `.git` or `.venv`.

Current actions:

- Authenticated `FILES_READ` users can browse fixed roots, preview bounded text
  candidates, and download one safe file.
- Authenticated `FILES_WRITE` users can upload one new file only under
  `/files/server`; uploads use safe basenames, size limits, staging in the
  destination directory, no-overwrite publish, and intent/outcome audit.
- Authenticated `FILES_WRITE` users can replace only existing allowlisted
  config/profile files under `/files/config`; replacement is separate from
  upload and is not a generic overwrite operation.
- There is no delete, rename, move, copy, recursive, or bulk operation.

Current validation and containment:

- Browser paths are relative POSIX paths only. Absolute paths, traversal,
  backslashes, NUL bytes, `.git`, and `.venv` are rejected.
- Resolved paths must stay inside the selected fixed root and outside system
  paths and the source tree.
- Listing hides forbidden entries, symlink escapes, system paths, and source-tree
  paths.
- Preview reads at most 64 KiB, rejects obvious binary content, and redacts
  known secret patterns before rendering.
- Replacement reads at most 512 KiB, rejects binary/control content, requires
  UTF-8 text, validates JSON for JSON targets, rejects symlinks, and requires
  the target to already exist as a regular file.
- `config.json` replacement reuses the guarded raw-config validator, including
  server config shape checks and secret-field protections.

Current recovery and audit rules:

- Replacement writes intent audit before mutation.
- Replacement creates a backup under `backups/file-replacements/` before
  publishing.
- Replacement stages a temp file in the target directory, publishes with
  `os.replace`, and fsyncs the directory.
- Replacement marks restart-pending work through `mutation_recovery` when the
  validated content changes. If primary pending-work storage fails, the shared
  fallback sidecar is used; if both fail, the route returns controlled text.
- Replacement writes outcome audit after publish and avoids raw absolute paths
  and secrets in audit details.

## Editable Targets For Slice 2

Slice 2 must add a narrow text editor, not a general file manager. A file is
editable only when all of these are true:

- It is under `/files/config`.
- It already exists and is a regular non-symlink file.
- It passes the same containment and source/system path checks as the current
  file browser.
- It passes the current safe replacement candidate checks, or a stricter editor
  allowlist layered on top of those checks.
- It is small enough to read and save within the replacement size limit.
- It is valid UTF-8 text before rendering and before saving.
- It is not a secret-bearing file unless a dedicated placeholder/restore flow
  exists for that target class.

Initial editable target classes:

- `config.json`, through the existing raw-config validation and secret guards.
- Top-level config text/JSON files with the current safe text suffixes:
  `.cfg`, `.conf`, `.ini`, `.json`, `.properties`, `.txt`, `.yaml`, `.yml`.
- One-level profile/config files under `profile/`, `profiles/`, and `settings/`
  with the same safe text suffixes.
- `AdminServerSettings/*.json`.
- `profile/CMPlayerStatsHUD/*.json`.

For nested config/profile directories, Slice 2 should start JSON-only unless the
replacement allowlist is intentionally broadened and tests lock that broader
scope. If a replacement candidate is valid but outside the editor allowlist, the
UI may keep the existing replace upload action but must not show `Edit`.

## Must Not Edit

The editor must not edit:

- Logs.
- Backups.
- Server binaries or install files.
- Generated service files or runtime units unless a separate safe flow owns
  them.
- Secrets, including RCON passwords, game passwords, admin passwords, web
  session secrets, API keys, tokens, and password hashes.
- Raw arbitrary paths, absolute paths, traversal paths, `.git`, `.venv`, source
  tree paths, system paths, or symlinks.
- Workshop/addon directories and downloaded content.
- Backup/temp files such as `.bak`, `.old`, `.orig`, `.tmp`, or hidden
  `.armactl-*` files.
- Directories.
- Recursive, bulk, delete, rename, move, copy, chmod/chown, or archive
  operations.

## Reuse Contract For Slice 2

The runtime editor must not create a second editing pipeline. It must reuse the
existing owning modules unless
[reuse-solid-duplication-audit.md](reuse-solid-duplication-audit.md) records a
reason to extract a smaller shared helper first:

- filesystem_* for root lookup, relative path parsing, containment, and safe
  labels.
- config_edit for config.json raw validation, secret restoration/rejection,
  backup semantics, and operator-facing validation errors.
- file_replacements or a deliberately extracted helper for allowlist checks,
  UTF-8/JSON validation, backup creation, same-directory temp staging, atomic
  publish, fsync, audit-safe details, and controlled error text.
- mutation_recovery for restart-pending primary/fallback marker behavior.
- Existing audit/job/result conventions for intent-before-mutation and bounded
  outcome reporting.

Exact rules:

- config.json editing must call the existing config_edit validation path,
  directly or through file_replacements; it must not reimplement raw config
  parsing, server config validation, secret placeholder restore, or secret
  rejection.
- Non-config profile/text editing must call the existing replacement
  staging/publish path or a small shared helper extracted from it; it must not
  copy temp staging, backup, atomic publish, fsync, audit, or pending-restart
  handling into a route.
- Routes and templates must stay thin: authentication, permission checks, CSRF,
  DTO extraction/rendering, service calls, and controlled responses only.
- Stale baseline checks belong in the service layer. They must run after target
  re-resolution and before intent audit, backup, pending work, or publish.

Do not duplicate:

- Secret placeholder/restore/reject logic.
- Raw config.json validation or server config shape validation.
- Generic JSON validation for non-config JSON replacement targets.
- Fingerprint and stale-baseline logic.
- Backup creation, same-directory temp staging, atomic replace, and directory
  fsync.
- Intent/outcome audit details and controlled audit-failure messages.
- Restart-pending primary/fallback marker behavior.
- Browser root lookup, relative path parsing, root containment, source/system
  path denial, .git/.venv denial, or symlink rejection.
- Preview/read redaction logic, especially saving redacted preview text.

Allowed new code:

- A small service-layer editor DTO/read/save helper in file_replacements, or a
  deliberately extracted helper used by both replacement upload and editor save.
- A narrow route/template/JS layer for GET edit and POST save that only wires
  auth, permissions, CSRF, DTOs, rendering, and service calls.
- Focused tests for edit-link visibility, editable read, stale baseline
  rejection, validation, no-op, publish, audit, pending fallback, and controlled
  errors.
- Minimal page copy or translation strings needed to expose the narrow editor
  safely.

## Slice 2 Save Contract

GET edit page:

- Require an authenticated session and `FILES_READ`.
- Resolve the target through the same root/path allowlist used by `/files`.
- Require the target to be an editable candidate, not just a preview candidate.
- Read only bounded bytes and validate UTF-8 before rendering.
- For `config.json`, render through the existing redacted raw-config editor text.
- For any other target, reject rendering if known secret patterns are detected;
  do not render redacted editable content that could be saved back accidentally.
- Include a baseline content fingerprint from the bytes rendered to the browser.
- Render root label and relative breadcrumbs only; do not render raw absolute
  paths.

POST save:

- Require an authenticated session, `FILES_WRITE`, and CSRF validation.
- Re-resolve and revalidate the same editable target allowlist on every save.
- Require the submitted baseline fingerprint to match the current disk content;
  reject stale saves with a controlled reload-required error.
- Validate submitted size and UTF-8 before any backup or publish.
- Validate content before publish: `config.json` through raw-config validation,
  JSON files through JSON parsing, and any future target-specific format through
  its owning validator.
- Reject known secret edits unless the target has a dedicated placeholder/restore
  validator.
- Write intent audit before mutation.
- Create a backup before publish.
- Stage the new content in a temp file in the target directory and fsync it.
- Publish with atomic replace and fsync the parent directory.
- Mark restart-pending work through `mutation_recovery` when the validated
  content changes.
- Write outcome audit after publish, including only root id, relative path,
  file kind, size, changed-field/category names, backup basename, and
  pending-work status.
- Return controlled errors only: no raw absolute path, secret, traceback, temp
  path, or backup absolute path in HTTP responses.

No-op saves should not create backups, write mutation audits, or mark restart
pending.

## Slice 2 UI Contract

- Show `Edit` only for files that pass the editor allowlist.
- Do not show `Edit` for directories or non-editable files.
- Do not add delete, rename, move, copy, chmod/chown, recursive, bulk, or generic
  path input controls.
- Keep the page framed as config/profile editing, not a file manager.
- Show only root labels, relative breadcrumbs, and safe basenames. Do not show
  raw absolute paths on the editor page.
- Keep existing preview/download/replace behavior separate from editor save
  behavior.

## Existing Test Coverage

Existing tests already cover:

- Auth, permission, and CSRF guards for browsing, upload, replacement, and config
  save flows.
- Root/path containment for traversal, absolute paths, symlink escapes, `.git`,
  `.venv`, source tree roots, and unknown roots.
- Bounded preview and preview redaction.
- Single-file download and safe download filenames.
- No-overwrite upload, upload size limits, upload staging cleanup, and upload
  audit failure behavior.
- Replacement visibility for safe config candidates only.
- Replacement of top-level profile text files, `config.json`,
  `AdminServerSettings/*.json`, and `profile/CMPlayerStatsHUD/*.json`.
- Replacement rejection for read-only roots, server binaries, logs, backups,
  invalid JSON, binary content, oversize content, traversal, absolute paths,
  `.git`, `.venv`, symlinks, and unknown/deep nested config paths.
- Replacement backup creation, intent/outcome audit order, pending restart
  tracking, pending fallback, outcome-audit failure, and controlled error text.
- Config editor safe field allowlist, raw config secret protections, backups,
  pending restart tracking, no-op behavior, and audit redaction.
- Shared mutation recovery fallback behavior.

Missing Slice 2 tests:

- `Edit` link visibility only for editable files, including no edit links for
  directories, logs, backups, server files, binaries, deep nested files, and
  replacement candidates outside the editor allowlist.
- GET edit auth and `FILES_READ` permission behavior.
- GET edit rejects traversal, absolute paths, `.git`, `.venv`, source/system
  paths, symlinks, non-files, missing files, binary content, oversize content,
  invalid UTF-8, and detected secrets before rendering.
- GET edit does not render raw absolute paths, session/CSRF tokens outside their
  intended form fields, traceback text, temp paths, backup paths, or secret
  values.
- POST save auth, `FILES_WRITE`, CSRF, target revalidation, and stale baseline
  fingerprint rejection with no mutation.
- POST save invalid UTF-8, binary/control content, oversize content, invalid
  JSON, secret edits, and target-specific validation failures with no backup,
  no publish, no pending restart, and controlled error text.
- POST save no-op behavior with no backup, mutation audit, or pending restart.
- POST save backup creation, same-directory temp staging, atomic publish,
  cleanup, intent/outcome audit order, pending restart marker, pending fallback,
  outcome-audit failure, and publish failure behavior.
- Config JSON editor saves reuse raw-config secret restoration/rejection and
  server config validation.
- UI and routes do not introduce delete, rename, move, copy, recursive, bulk,
  or arbitrary-path controls.
