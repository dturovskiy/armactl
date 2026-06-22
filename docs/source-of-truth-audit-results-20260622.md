# Source-of-truth audit results - 2026-06-22

Branch: audit/source-of-truth-20260622, based on feat/web-interface.

Scope: project-wide source-of-truth review for JSON/config/state ownership before
adding more web config toggles such as third-person view or future crossplay and
platform controls.

## Result

Addressed by the follow-up implementation slice on 2026-06-22. No new
third-person, crossplay, platform, raw JSON, or secret web controls were added.

The blocker found by the audit was not current production breakage. The blocker
was ownership ambiguity: generated defaults, sample data, web field descriptors,
TUI structured fields, and CLI config adapters could drift before new config
controls were added.

The next state is now explicit:

- runtime server config source of truth: ~/armactl-data/<instance>/config/config.json;
- generated default source: src/armactl/server_config_schema.py plus templates/config.json.j2;
- example config source: docs/examples/config.full-example.json, sample only;
- Web/TUI/CLI structured config surfaces must consume the shared registry or a
  thin projection of it, not define independent competing field truth.

## Next-State Source-Of-Truth Map

| Area | Source of truth | Notes |
|------|-----------------|-------|
| Deployed server config | ~/armactl-data/<instance>/config/config.json | Runtime file read by the server and edited by armactl. |
| Generated default field registry | src/armactl/server_config_schema.py | Owns supported field paths, generated defaults, validation metadata, risk, permission, secret, restart, audit, and UI metadata. |
| Generated default renderer | templates/config.json.j2 | Install and repair renderer only; receives values from the shared registry context with strict template variables. |
| Full sample/mod-pack example | docs/examples/config.full-example.json | Example data and large mod-pack fixture only. It is not a runtime default. |
| Config read/write/validation | src/armactl/config_manager.py | Central load/save/backup/validation helper; intentionally accepts unknown upstream keys. |
| Normal web config fields | server_config_schema.web_config_field_descriptors() | Web keeps workflow, audit, backup, and pending-work behavior, but field metadata is a shared projection. |
| TUI structured config fields | server_config_schema.tui_structured_config_fields() | Structured TUI editor uses registry paths/defaults and shared scalar validation for existing non-secret fields. |
| CLI config commands | server_config_schema.save_registered_config_value() | Existing config set-* commands remain compatibility adapters over registered fields and validation. |
| Official game admins | config.json key game.admins | Game-facing ACL source. |
| Admin display labels/source metadata | admins-state.json | Local armactl sidecar; not a replacement for game.admins. |
| Active mods | config.json key game.mods | Server-facing mod list. |
| Disabled mods metadata | mods-state.json | Local armactl sidecar; game.disabledMods is rejected and migrated out. |
| Web runtime state | ~/armactl-data/web/web.db and web.env | Web users, sessions, jobs, pending work, version cache, and runtime config. |
| Player registry | ~/armactl-data/<instance>/players.db | Reliable IDs and nickname history; no IP storage by default. |
| Web audit | ~/armactl-data/logs/web/audit.log | Append-only audit trail, not operational state. |

## Sample Versus Generated Defaults

The full example intentionally keeps a large real-looking mod-pack payload and
therefore overlaps generated default fields. This is acceptable because it now
lives under docs/examples/ and tests assert the exact known divergences. If the
example changes a generated-default field, the divergence list must be updated on
purpose.

Known example divergences from generated defaults:

| Field | Example role |
|-------|--------------|
| game.name | Sample server label. |
| game.scenarioId | Sample mission. |
| game.maxPlayers | Sample capacity. |
| game.mods | Large mod-pack fixture. |
| game.passwordAdmin | Sample secret value, not a default. |
| rcon.password | Sample secret value, not a default. |
| game.gameProperties.serverMinGrassDistance | Sample tuning. |
| game.gameProperties.networkViewDistance | Sample tuning. |
| game.gameProperties.disableThirdPerson | Sample value only; no UI was added in this slice. |

Sample-only fields include rcon.blacklist, rcon.whitelist, VON booleans, and
operating.lobbyPlayerSynchronise. Generated-only field currently includes
rcon.maxClients.

## Config UI Duplication Risk

The risk is addressed for current supported fields:

- Web safe fields derive from the shared registry.
- TUI structured field reads/writes use registry paths, generated defaults, and shared scalar validation for existing non-secret fields.
- CLI config set-* commands use a registry save helper and remain documented
  compatibility adapters over registered fields and validation.

Future web config expansion must add field metadata to the shared registry first,
then expose a Web/TUI/CLI projection only where the risk and permission class are
appropriate.

## Guardrails Still Active

- Do not add a third-person UI control in this slice.
- Do not invent crossplay or platform keys.
- Do not expose raw JSON editing in normal web config UI.
- Do not move config editing into /files.
- Keep secrets out of normal web rendering and audit details.

When third-person is implemented later, expose a positive operator field such as
third_person_view and map it explicitly to the negative server key:

- third_person_view = true -> game.gameProperties.disableThirdPerson = false
- third_person_view = false -> game.gameProperties.disableThirdPerson = true

## Validation Coverage

Regression tests now cover:

- generated default template output matching server_config_schema defaults;
- install/write and repair missing-config generation through the shared defaults;
- full example config being sample-only and absent from active templates;
- exact example/generated divergence tracking;
- Web safe field projection matching the shared registry;
- preservation of unrelated advanced and secret fields during web save;
- disableThirdPerson not rendering in the web UI;
- CLI compatibility command update and invalid string rejection through a registered config field;
- TUI/Web/CLI overlapping safe fields sharing registry paths and shared scalar validation where applicable.
