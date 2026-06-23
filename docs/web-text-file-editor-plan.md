# Web Text File Editor Plan

Status: future feature plan. This is not part of the current `/files` write
surface and must not be implemented as a generic edit-anything shortcut.

## Goal

Add a safe browser-based text editor for selected allowlisted files that are not
covered by structured web pages yet. The editor should help operators make small
controlled edits without SSH while keeping backups, audit, validation, and clear
ownership boundaries.

This feature is separate from the structured `/config` editor:

- Normal `config.json` changes belong in `/config` safe/advanced controls.
- Full raw `config.json` editing remains a future `/config` break-glass mode,
  not a normal `/files` edit button.
- `/files` text editing is for allowlisted text files where no dedicated page
  exists yet.

## Candidate Files

Allowed only after root/path/extension/size checks:

- small `.txt`, `.md`, `.cfg`, `.conf`, `.json`, `.jsonc`, `.ini`, `.yaml`, and
  `.yml` files in approved roots;
- mod or server auxiliary config files that do not yet have a dedicated form;
- SAT/mod runtime files only after their owner module and validation rules are
  reviewed;
- backup/example files in read-only or save-copy mode, depending on root policy.

Not allowed by default:

- logs as editable files;
- binaries, archives, executables, scripts, symlinks, sockets, devices;
- files outside the allowlisted roots;
- large files over the configured editor limit;
- secrets or service files without stronger future permission/policy.

## UX Requirements

The table in `/files` may show an `Edit` action only when a file is editable.
The action should open a dedicated editor page, for example
`/files/{root_id}/edit?path=...`, not inline editing inside the listing table.

The editor page should show:

- root and relative path;
- file size and last modified timestamp;
- read-only/editable state;
- syntax mode when known;
- clear warning if the file is better managed by another page.

Minimum editor controls:

- Save;
- Save copy as / Save as;
- Cancel / Back without saving;
- Reload from disk;
- dirty-state warning before navigation;
- browser/editor shortcuts for Ctrl+S, Ctrl+Z, Ctrl+Y or Ctrl+Shift+Z,
  Ctrl+A, Ctrl+C, Ctrl+X, Ctrl+V;
- search with Ctrl+F;
- replace with Ctrl+H can be a later enhancement;
- line numbers;
- monospace font;
- basic syntax highlighting for JSON/config-like files when the frontend editor
  supports it;
- JSON formatting and JSON validation for JSON files.

A first implementation may use a plain `<textarea>` if the backend contract is
clean. A later UI pass can replace it with CodeMirror 6 or another maintained
browser editor without changing the backend workflow.

## Save Workflow

Save must be an explicit mutating workflow:

1. permission check;
2. CSRF check;
3. root/path/extension/size revalidation;
4. reject if file changed on disk since opened unless operator reloads or uses a
   future force-confirm flow;
5. validate content when the file type has a validator;
6. write audit intent without file contents;
7. create backup before overwrite;
8. atomic publish where practical;
9. write audit outcome without file contents;
10. return controlled result with backup path, changed state, and validation
    errors when relevant.

The audit log must not store file contents or secrets. It may store root,
relative path, size, checksum/fingerprint, validator name, backup path, and
result class.

## Permissions And Policy

Suggested permissions:

- `files:view`: browse and preview;
- `files:download`: download;
- `files:write`: upload new files where allowed;
- `files:edit`: edit existing allowlisted text files;
- stronger future permissions for secrets, service files, raw config, or other
  dangerous roots.

Dangerous edits must wait for the future users/policy/security foundation.

## Config Boundary

`config.json` remains special:

- the normal path is `/config` structured safe fields;
- advanced fields belong to `/config` advanced mode with `settings:advanced`;
- full JSON edit/import/export belongs to `/config` break-glass with
  `config:raw_edit`, double confirmation, validation, backup, audit, and pending
  restart behavior;
- `/files` should link to `/config` for normal config editing instead of making
  config editing a generic file operation.

## Tests

Required test coverage before implementation is accepted:

- editable and non-editable file classification;
- traversal/symlink escape rejection;
- extension and size limits;
- stale-on-disk detection;
- JSON validation success/failure;
- backup creation before overwrite;
- no file contents in audit;
- permission and CSRF failures;
- read-only roots stay read-only;
- config boundary behavior points to `/config` or break-glass mode;
- UI smoke for dirty-state and shortcut wiring when frontend code exists.
