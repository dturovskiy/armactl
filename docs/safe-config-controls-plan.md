# Safe Config Controls Plan

This plan started as a docs-only audit/design slice. Runtime grouping, helper
text, impact labels, and restart labels are now implemented for the existing
safe field set. Future expansion must extend the existing config editor instead
of creating a second config editor or save pipeline.

## Existing Config Editor Audit

### Structured Editor

The normal editor is the descriptor-driven `POST /config` path.

It already supports:

- Safe field ownership in `server_config_schema.WEB_CONFIG_FIELD_NAMES` and
  `web_config_field_descriptors()`.
- Page DTO projection through `config_edit.build_config_edit_fields()` and
  `build_config_edit_form()`.
- POST allowlisting through `config_edit.extract_config_edit_form()`.
- Per-field parsing and validation through `parse_form_field_value()` on each
  `ServerConfigField` descriptor.
- Creation of missing `game.gameProperties` when saving safe nested fields.
- No-op detection before backup, audit, save, or pending-restart work.
- Web-specific pre-save backups before changed config writes.
- A single config write through `config_manager.save_config(..., backup=False)`,
  with final config validation still enabled.
- Safe audit details: changed field names and backup basenames, not absolute
  paths or secrets.
- Restart-pending fingerprints through the existing pending-work service,
  including return-to-baseline clearing.
- No direct game-service restart from web config save.

`routes/config.py` is thin glue: auth, permission, CSRF, DTO extraction, service
calls, redirects, and controlled error rendering. `config.html` renders fields
from the page model and should not become a second field registry.

### Raw Config Editor

The advanced editor is the guarded `POST /config/raw` path.

It already supports:

- Redacted full `config.json` rendering through
  `config_edit.build_raw_config_editor_text()`.
- Required confirmation before raw JSON save.
- Bounded raw input with `RAW_CONFIG_MAX_BYTES`.
- JSON parse errors with line/column context and root-object enforcement.
- Full server-facing validation through `config_manager.validate_config()`.
- The same backup, save, audit, and pending-restart pipeline as structured
  config saves.
- Dotted changed-field reporting for non-secret raw config changes.
- Safe rerendering after validation errors, without echoing secret-change
  attempts.

The raw editor is an advanced guarded escape hatch. It is not the safe controls
UI and must not become an arbitrary JSON-path editor.

### Secret Placeholder Recovery

Secret handling is centralized in `config_edit`.

Protected paths:

- `game.password`
- `game.passwordAdmin`
- `rcon.password`

Existing behavior:

- Existing non-empty secrets render as `<redacted: unchanged>`.
- `_restore_secret_placeholders(...)` restores placeholders to the current disk
  value during raw saves.
- Secret removal, new secret insertion, or secret value changes are rejected
  before backup or save.
- Secret-change errors do not echo submitted raw JSON back to the browser.
- Audit details do not include secret values, raw absolute paths, or backup
  absolute paths.

Future safe controls must not edit passwords, tokens, generated secrets, hashes,
or any secret-bearing field unless a separate secret-rotation contract is
written and tested.

### Validation And Source Of Truth

The structured safe-controls source of truth is `server_config_schema`:

- `SERVER_CONFIG_FIELDS` defines paths, value types, validation messages, risk,
  permission, secret behavior, restart behavior, defaults, and UI metadata.
- `WEB_CONFIG_FIELD_NAMES` is the current web allowlist.
- `web_config_field_descriptors()` is consumed by `config_edit`.
- `parse_form_field_value()` owns scalar web form validation.
- `config_field_input_value()` owns display/default projection.

`config_manager.save_config()` is the final server-facing validator for writes.
The raw editor additionally calls `config_manager.validate_config()` while
preparing raw replacements.

`file_replacements` also participates for `/files/config/config.json`: config
replacement and runtime file-editor saves delegate to
`config_edit.validate_raw_config_replacement(...)` instead of duplicating raw
config parsing, secret restoration, or restart fingerprints.

### Backup, Audit, Pending Restart

Current config mutation bookkeeping is owned by `config_edit` plus
`mutation_recovery`:

- `create_web_config_backup(...)` creates `config.json.before-web-config-save-*`
  backups before changed structured/raw saves.
- `_audit_config_save(...)` writes intent and outcome events for changed saves.
- `_mark_restart_pending_for_config_result(...)` routes restart tracking through
  `mutation_recovery.mark_restart_pending_for_mutation(...)`.
- `mutation_recovery` owns primary pending-work writes plus fallback sidecar
  behavior when bookkeeping fails after mutation.
- No-op saves skip backup, mutation audit, and pending restart.

A future safe-controls implementation must reuse this pipeline. It must not add
another backup naming scheme, audit writer, pending-work writer, direct route
save, or template-level mutation behavior. Runtime grouping/labels for the
current allowlist reuse this pipeline; future field expansion must do the same.

## Modules To Reuse

Required reuse points:

- `server_config_schema` for the field registry, UI metadata, validation, risk,
  permission, and restart behavior.
- `config_edit` for structured projection, guarded raw config validation,
  backup/save/audit, changed fields, and config restart fingerprints.
- `mutation_recovery` for restart-pending primary/fallback behavior.
- `page_models.config` for read DTO construction.
- `routes.config` only for auth/permission/CSRF/DTO glue.
- `templates/config.html` only for descriptor-driven rendering glue.
- `file_replacements` for `/files/config` replacement/editor flows, with
  `config_edit` delegation intact for `config.json`.

## Minimum Safe Controls

The first runtime UI slice started with the current web allowlist only. Future
safe-control expansion must still add no new JSON paths without a field contract.

| Field | JSON path / source field | Operator effect | UI control | Validation | Restart | Risk | Tests needed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Server name | `game.name` / `name` | Changes the displayed/listed server name. | Required text input. | Non-empty string, max 512 chars, no control chars. | Yes. | Non-secret display/discovery field. | Descriptor coverage, render value/default, valid save, blank/too-long/control-char rejection, audit/backup/pending. |
| Scenario ID | `game.scenarioId` / `scenario_id` | Selects the mission/scenario. | Required text input. | Non-empty string, max 512 chars, no control chars. | Yes. | Non-secret, but bad values can break startup. | Descriptor coverage, render, valid save, invalid string rejection, audit/backup/pending, VM save/restart/revert smoke before expansion. |
| Max players | `game.maxPlayers` / `max_players` | Sets player capacity. | Required number input. | Integer `>= 1`; no decimal/empty/boolean. | Yes. | Capacity/performance impact. | Valid save, `0`/decimal/blank/non-integer rejection, changed-field audit, backup, pending restart. |
| Visible in server browser | `game.visible` / `visible` | Controls server-browser listing. | Checkbox. | Boolean checkbox parsing only. | Yes. | Public discoverability choice, not firewall/bind policy. | Checked/unchecked saves, invalid boolean rejection, audit/pending restart. |
| Disable third-person view | `game.gameProperties.disableThirdPerson` / `disable_third_person` | Controls a gameplay perspective rule. | Checkbox. | Boolean checkbox parsing only. | Yes. | Gameplay policy only. | Checked/unchecked saves, pending marker details, return-to-baseline clears pending work. |
| BattlEye | `game.gameProperties.battlEye` / `battleye` | Enables/disables BattlEye. | Checkbox. | Boolean checkbox parsing only. | Yes. | Security posture changes when disabled. | Checked/unchecked saves, invalid boolean rejection, audit/pending restart, VM smoke before stronger UX changes. |
| Server max view distance | `game.gameProperties.serverMaxViewDistance` / `server_max_view_distance` | Sets maximum view distance. | Required number input. | Integer `>= 1`; no decimal/empty/boolean. | Yes. | Performance/client-experience impact. | Valid save, `0`/decimal/blank/non-integer rejection, backup/audit/pending. |
| Server min grass distance | `game.gameProperties.serverMinGrassDistance` / `server_min_grass_distance` | Sets minimum grass distance. | Required number input. | Integer `>= 0`; no decimal/empty/boolean. | Yes. | Performance/visual impact. | Valid save, negative/decimal/blank/non-integer rejection, backup/audit/pending. |

## Forbidden And Out Of Scope

Future runtime implementation slices must not add:

- Password, token, secret, generated-secret, or password-hash editing.
- Controls for `game.password`, `game.passwordAdmin`, `rcon.password`, web
  session secrets, API tokens, bot/webhook secrets, or password hashes.
- An arbitrary JSON path editor or free-form nested key/value editor.
- Normal safe controls for raw network, RCON, firewall, bind, public address, or
  port fields without a separate contract for validation, reachability,
  rollback, operator recovery, and VM smoke.
- A duplicated save/backup/audit/pending-restart pipeline.
- Direct route/template writes to `config.json`.
- A new config writer, raw JSON parser, secret placeholder implementation,
  audit helper, backup naming scheme, or pending-work fallback implementation.
- File-system edits outside the safe file editor/replacement flow.
- Broad file-manager behavior such as delete, rename, move, copy, chmod,
  recursive operations, or arbitrary path editing.

Network/RCON examples that stay out of normal safe controls until a dedicated
contract exists: `bindAddress`, `bindPort`, `publicAddress`, `publicPort`,
`a2s.address`, `a2s.port`, `rcon.address`, `rcon.port`, `rcon.permission`, and
`rcon.maxClients`. The guarded raw editor may still save valid non-secret
advanced config changes; that does not make those fields safe controls.

## Implementation Contract For Future Slices

Required shape:

- Runtime grouping and labels for the current allowlist are implemented; future field expansion is a separate slice.
- Keep `routes/config.py` as thin glue.
- Keep `templates/config.html` descriptor-driven. If grouping metadata is
  needed, add it to `ServerConfigFieldUi` and tests instead of hard-coding a
  second registry in the template.
- Keep mutation and recovery logic in `config_edit` and `mutation_recovery`.
- Keep `file_replacements` as the owner for config/profile file editing and
  `config.json` replacement delegation.
- Preserve one structured editor and one guarded advanced raw editor.
- Preserve no-op behavior: no backup, mutation audit, or pending restart when
  values do not change.
- Preserve pending-work state fingerprints so returning to baseline clears the
  restart marker.
- Preserve audit redaction and safe details: field names and backup basenames
  only, no raw absolute paths or secrets.

Validation for future implementation slices:

- If only docs change, run `git diff --check`.
- If code/templates/tests change, run `ruff` and focused config tests.
- Add focused tests for any descriptor metadata, route/template glue,
  validation rule, changed-field audit, backup behavior, pending restart,
  return-to-baseline behavior, no-op behavior, and secret/raw-editor regression
  touched by the slice.

## Blocker Assessment

No code blocker is identified for future safe-control expansion. The main gate
remains scope discipline: consider one new field group at a time only after
behavior, recovery, and VM smoke are documented.
