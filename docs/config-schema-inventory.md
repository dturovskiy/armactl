# Config schema inventory

Date: 2026-06-22

Scope: inventory plus first descriptor-backed web config editing foundation.
This slice refactors the existing seven web-safe fields into an allowlist
registry. It does not add third-person, crossplay, platform, secret, advanced,
raw JSON, or file-manager config editing.

Sources checked:

- `src/armactl/web/services/config_edit.py`
- `src/armactl/web/templates/config.html`
- `src/armactl/web/routes/config.py`
- `src/armactl/web/page_models/config.py`
- `src/armactl/tui/screens.py` (`ConfigEditorScreen`, `RawConfigScreen`)
- `src/armactl/config_manager.py`
- `src/armactl/server_config_schema.py`
- `src/armactl/status_summary.py`
- `docs/examples/config.full-example.json`
- `templates/config.json.j2`
- `tests/test_web_config_edit.py`
- `tests/test_config_manager.py`
- `tests/test_status_summary.py`

Current conclusions:

- Normal web `/config` editing is limited to seven allowlisted safe fields:
  `game.name`, `game.scenarioId`, `game.maxPlayers`, `game.visible`,
  `game.gameProperties.battlEye`,
  `game.gameProperties.serverMaxViewDistance`, and
  `game.gameProperties.serverMinGrassDistance`.
- Those seven fields are now described by the shared registry in `src/armactl/server_config_schema.py`. Web `CONFIG_FIELD_DESCRIPTORS` in `src/armactl/web/services/config_edit.py` is a thin projection over that registry. The shared field metadata records the form field name, config path, parser/validation metadata, risk class, permission, secret/redaction behavior, audit changed-field name, restart/pending-work behavior, and UI control metadata. The route keeps permission and CSRF checks; the service layer still owns validation, save, audit, backup, and changed-only pending restart behavior.
- Structured TUI config editing now consumes registry paths/defaults and shared scalar validation for existing non-secret fields, and still edits network ports and passwords: `bindPort`, `publicPort`, `a2s.port`, `rcon.port`, `game.password`, `game.passwordAdmin`, and `rcon.password`.
- TUI `RawConfigScreen` can edit arbitrary JSON after `validate_config()`, but
  raw JSON remains future web break-glass work, not normal web config UI.
- `status_summary` and the web config summary expose only non-secret summary
  fields: name, scenario, max players, game/A2S/RCON ports, visibility, and
  BattlEye.
- `config_manager.validate_config()` validates a narrow server-facing subset and
  intentionally does not reject unknown future upstream keys.
- No local code, template, or test confirms a crossplay/platform config key.
  Do not invent one.
- `game.gameProperties.disableThirdPerson` is locally confirmed in the generated
  config template and full example as a boolean, but their values differ (`false` in
  `docs/examples/config.full-example.json`, `true` in `templates/config.json.j2`) and exact
  upstream semantics plus restart behavior still need verification before a web
  control is added.

## Field Inventory

| Field path | Current source | Value type | Validation rule | Restart required | Risk class | Permission needed | UI control type | Notes / unknowns |
|------------|----------------|------------|-----------------|------------------|------------|-------------------|-----------------|------------------|
| `bindAddress` | template only; TUI raw | string | `config_manager`: required string | unknown | dangerous | future `settings:advanced` | no normal UI | Binding can change network exposure; no structured web/TUI control. |
| `bindPort` | TUI structured; web read-only summary; template | integer | `config_manager`: required integer 1..65535 | unknown; TUI offers Save & Restart | advanced | future `settings:advanced` | number | TUI writes this and `publicPort` from one game-port input. |
| `publicAddress` | template only; TUI raw | string | `config_manager`: string if present | unknown | advanced | future `settings:advanced` | no normal UI | Public advertisement behavior needs operator guidance before UI. |
| `publicPort` | TUI structured; template | integer | `config_manager`: optional integer 1..65535 | unknown; TUI offers Save & Restart | advanced | future `settings:advanced` | number | TUI mirrors the game-port input into this field. |
| `a2s.address` | template only; TUI raw | string | no current field validation; `a2s` must be an object if present | unknown | advanced | future `settings:advanced` | no normal UI | Address shape and exposure behavior need verification. |
| `a2s.port` | TUI structured; web read-only summary; template | integer | `config_manager`: optional integer 1..65535 | unknown; TUI offers Save & Restart | advanced | future `settings:advanced` | number | TUI-only advanced field today. |
| `rcon.address` | template only; TUI raw | string | no current field validation; `rcon` must be an object if present | unknown | dangerous | future `settings:advanced` plus policy | no normal UI | RCON exposure-sensitive. |
| `rcon.port` | TUI structured; web read-only summary; template | integer | `config_manager`: optional integer 1..65535 | unknown; TUI offers Save & Restart | advanced | future `settings:advanced` | number | TUI-only advanced field today. |
| `rcon.password` | TUI structured password field; template | string | no current validation | unknown | secret | future secret policy; not `settings:manage` | password-masked / no normal UI | Web tests assert this secret is not rendered or audited. |
| `rcon.permission` | template only; TUI raw | string enum-like | no current validation | unknown | dangerous | future policy | select / no normal UI | Sample value is `admin`; allowed values not locally verified. |
| `rcon.blacklist[]` | full example only; TUI raw | list | no current validation | unknown | dangerous | future policy | no normal UI | Present in `docs/examples/config.full-example.json`, absent from Jinja template. |
| `rcon.whitelist[]` | full example only; TUI raw | list | no current validation | unknown | dangerous | future policy | no normal UI | Present in `docs/examples/config.full-example.json`, absent from Jinja template. |
| `rcon.maxClients` | Jinja template only; TUI raw | integer | no current validation | unknown | advanced | future `settings:advanced` | number / no normal UI | Present in `templates/config.json.j2`, absent from full example. |
| `game.name` | web descriptor edit; TUI structured; template | string | web: required, <=512 chars, no control chars; `config_manager`: required string | yes; web marks pending restart | safe | `settings:manage` | text | Current web-safe field. |
| `game.password` | TUI structured password field; template | string | no current validation | unknown | secret | future secret policy; not `settings:manage` | password-masked / no normal UI | Do not expose casually in web UI. |
| `game.passwordAdmin` | TUI structured password field; template | string | no current validation | unknown | secret | future secret policy; not `settings:manage` | password-masked / no normal UI | TUI-only advanced secret today. |
| `game.admins[]` | web admins flow; `admins_manager`; template | list of strings | `config_manager`/`admins_manager`: list, <=20 unique non-empty IdentityId or SteamID strings | yes; web marks pending restart | dangerous | `admins:manage` | no normal config UI | Dedicated admins page, not `/config` safe toggles. |
| `game.scenarioId` | web descriptor edit; TUI structured; template | string | web: required, <=512 chars, no control chars; `config_manager`: required string | yes; web marks pending restart | safe | `settings:manage` | text | Current web-safe field. |
| `game.maxPlayers` | web descriptor edit; TUI structured; template | integer | web: integer >=1; `config_manager`: positive integer | yes; web marks pending restart | safe | `settings:manage` | number | Current web-safe field. |
| `game.visible` | web descriptor edit; template | boolean | web: checkbox boolean; no `config_manager` bool validation | yes; web marks pending restart | safe | `settings:manage` | checkbox | Current web-safe field. |
| `game.gameProperties.serverMaxViewDistance` | web descriptor edit; template | integer | web: integer >=1; no `config_manager` field validation | yes; web marks pending restart | safe | `settings:manage` | number | Current web-safe field. |
| `game.gameProperties.serverMinGrassDistance` | web descriptor edit; template | integer | web: integer >=0; no `config_manager` field validation | yes; web marks pending restart | safe | `settings:manage` | number | Current web-safe field. |
| `game.gameProperties.networkViewDistance` | template only; TUI raw | integer | no current validation | unknown | advanced | future `settings:advanced` or verified safe policy | number | Non-secret numeric candidate, but bounds/defaults need verification. |
| `game.gameProperties.disableThirdPerson` | template only; TUI raw | boolean | no current validation | unknown | advanced | future `settings:advanced` until verified safe | checkbox | Third-person candidate. Local templates disagree on default (`false` sample, `true` Jinja); do not implement until semantics/defaults/restart behavior are verified. |
| `game.gameProperties.fastValidation` | template only; TUI raw | boolean | no current validation | unknown | advanced | future `settings:advanced` | checkbox | Non-secret boolean candidate; operational/security meaning needs verification. |
| `game.gameProperties.battlEye` | web descriptor edit; web read-only summary; template | boolean | web: checkbox boolean; no `config_manager` bool validation | yes; web marks pending restart | safe | `settings:manage` | checkbox | Current web-safe field. |
| `game.gameProperties.VONDisableUI` | full example only; TUI raw | boolean | no current validation | unknown | advanced | future `settings:advanced` | checkbox | Sample-only candidate; absent from Jinja template. |
| `game.gameProperties.VONDisableDirectSpeechUI` | full example only; TUI raw | boolean | no current validation | unknown | advanced | future `settings:advanced` | checkbox | Sample-only candidate; absent from Jinja template. |
| `game.gameProperties.VONCanTransmitCrossFaction` | full example only; TUI raw | boolean | no current validation | unknown | advanced | future `settings:advanced` | checkbox | Cross-faction voice candidate, not a confirmed platform crossplay key. |
| `game.mods[]` | web mods flow; TUI mod manager; template | list of objects | `config_manager`: list; each item object; `modId` required, 16 hex, unique | yes; web marks pending restart | advanced | `mods:manage` | no normal config UI | Dedicated mods page; bulk modpack workflows remain future. |
| `game.mods[].modId` | web mods flow; TUI mod manager; template | string | `config_manager`/`mods_manager`: exactly 16 hexadecimal characters | yes; web marks pending restart | advanced | `mods:manage` | no normal config UI | Managed through mod actions, not generic config editing. |
| `game.mods[].name` | web mods flow; TUI mod manager; template | string | no strict `config_manager` validation beyond containing object | yes; web marks pending restart | advanced | `mods:manage` | no normal config UI | Display label only by convention. |
| `game.disabledMods` | legacy/sidecar metadata only | list | `config_manager`: rejected in server config | no | runtime | no normal UI | Must stay out of server `config.json`; use disabled-mods sidecar. |
| `operating.lobbyPlayerSynchronise` | full example only; TUI raw | boolean | no current validation | unknown | runtime | future policy | no normal UI | Present only in full example; runtime behavior needs verification. |
| unknown crossplay/platform field(s) | not locally confirmed | unknown | unknown | unknown | advanced | future `settings:advanced` after verification | no normal UI | No local key/value shape found in code, tests, or templates. Do not add UI until verified from official docs or real config samples. |

## Implemented Foundation And Next Slice

1. Implemented now: runtime server config source of truth is
   `~/armactl-data/<instance>/config/config.json`.
2. Implemented now: generated defaults are owned by
   `src/armactl/server_config_schema.py` plus `templates/config.json.j2`.
   Install and repair render the Jinja template with strict variables from the
   shared registry.
3. Implemented now: `docs/examples/config.full-example.json` is sample-only
   large mod-pack data, not a runtime default. Tests assert the exact known
   divergences from generated defaults.
4. Implemented now: `/config` remains the only normal web config editing
   surface; `/files` is still not a config editor.
5. Implemented now: the current seven safe fields stay under
   `settings:manage` and derive from the shared registry through the web
   projection.
6. Implemented now: TUI structured config uses registry paths/defaults and shared scalar
   validation for existing non-secret fields, and CLI `config set-*` commands are compatibility
   adapters over registered fields and validation.
7. Implemented now: tests cover descriptor coverage, generated defaults,
   example-only config role, GET rendering, POST save, invalid values,
   unchanged/changed pending restart behavior, preservation of unrelated
   advanced/secret fields, safe audit changed-field names, and the fact that
   `disableThirdPerson` is not rendered yet.
8. Future: verify candidate keys and value shapes before implementation. First
   review official Arma Reforger docs or real deployed config samples for
   game.gameProperties.networkViewDistance,
   game.gameProperties.disableThirdPerson,
   game.gameProperties.fastValidation, and the sample-only VON fields. For
   disableThirdPerson, expose a positive UI field such as third_person_view
   and map it inversely to the server key.
9. Future: do not implement crossplay/platform controls until a real key and
   allowed value shape are confirmed. No local source currently confirms them.
10. Future: keep ports, RCON/A2S, bind/public addresses, and other
   network/security fields behind future settings:advanced; keep passwords
   behind a stronger future secret policy and out of normal rendered pages.
11. Future: update this inventory and the web handoff docs in the same
   implementation slice as any new safe toggle.
