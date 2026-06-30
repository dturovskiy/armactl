# Player Log Event Inventory

This inventory records a read-only pass over real Arma Reforger server logs from the target game VM. It is intended to guide a future player history/statistics slice without adding code, schema, or live-server changes in this pass.

## Scope And Method

- Checked the target game VM through the approved host-to-jump-host-to-game-VM route.
- Read only runtime paths and service metadata. Commands were limited to bounded `find`, `stat`, `grep`, `head`, `tail`, `journalctl`, and `systemctl show` style reads.
- Checked the instance runtime log tree, latest retained game console/script/error logs, the game service journal for the active unit, a small crash-report marker file, and non-game armactl log locations for context.
- Did not restart, stop, signal, update, clean, repair, install, or modify the game service, configs, logs, web state, or game process.
- Did not copy raw large logs into the repo. Samples below are structural patterns with player names, player IDs, addresses, scenario resources, and machine-specific details replaced by placeholders.

## Observed Log Sources

| Source | Freshness | Contains | Privacy risk | Notes |
| --- | --- | --- | --- | --- |
| `~/armactl-data/default/config/logs/*/console.log` | Retained session directories from the latest sampled day | Startup/lifecycle lines, mission load, RCON polling traces, `-logStats` FPS/count telemetry, script warnings | High if copied raw | Latest retained files mostly showed zero active players. Useful for lifecycle and aggregate telemetry, but not sufficient alone for historical player sessions in the sampled window. |
| `~/armactl-data/default/config/logs/*/script.log` | Same retained session directories | Addon/script initialization, ServerAdminTools and WCS component messages, script warnings | High if copied raw | May contain player-event lines when those scripts emit them. The sampled latest retained files did not provide a complete player history by themselves. |
| `~/armactl-data/default/config/logs/*/error.log` | Same retained session directories | Script errors, warnings, stack traces | Medium to high | Not a primary player-event source. Useful only to explain parser gaps or crash-adjacent behavior. |
| `~/armactl-data/default/config/logs/CrashReports.log` | Older small marker file | Crash-report availability marker | Medium | Confirms the crash-report path exists. It did not provide useful player-event details in this pass. |
| `journalctl -u armareforger.service` | Retained service journal across runs | Real player auth/connect/GUID/name lines, connect/disconnect, player count, faction joins, mod-emitted kill/death events, mission lifecycle, shutdown-save markers | High | Strongest observed source for historical player activity. Raw lines can include player names, stable IDs, addresses, and scenario details, so parser output must be bounded and redacted. |
| `~/armactl-data/logs/web/audit.log` | Fresh web audit log | armactl web actions and player refresh summaries | High/moderation data | Not a game runtime event source. Useful only to correlate operator actions already audited by armactl. |
| `~/armactl-data/default/logs/host-tests-*.log` | Older host-test output | Host-test diagnostics | Low to medium | Not useful for player history/statistics. |

## Event Pattern Findings

### Connect / Disconnect

Connect is present in the service journal with several joinable lines:

- backend authentication with `rplIdentity`, stable `identityId`, and player name;
- BattlEye player slot/name connect line;
- BattlEye GUID and BE GUID hash lines;
- network `connectionID` connect/update lines;
- ServerAdminTools `player_joined` event line;
- aggregate `Players connected: N / N` and `FPS: ... Player: N` telemetry.

Disconnect is present, but requires stateful joining:

- RPL disconnect line with `rplIdentity` and reason;
- BattlEye disconnect line with player slot/name;
- network disconnect line with `connectionID`.

The disconnect lines observed do not always repeat the stable `identityId`, so a parser needs an in-memory or persisted mapping from recent connect/update lines. Session close should be confidence-scored when only slot or connection ID is available.

### Player Identity / Name

Observed identity candidates:

- stable `identityId` UUID from backend/network/ServerAdminTools lines;
- Steam64-like GUID from BattlEye `Setting GUID` lines;
- BE GUID hash from BattlEye lines;
- per-run `rplIdentity` values;
- per-session `PlayerId` values;
- network `connectionID` values;
- player display name snapshots.

For armactl storage, the safest primary key remains the existing reliable player ID path from the roster/registry flow. Log-derived IDs should be joined to that registry where possible. Per-run values such as `rplIdentity`, `PlayerId`, slot, and `connectionID` are useful correlation fields, not durable player IDs.

### Kill / Death / Teamkill

Kill/death lines were observed in the service journal on both checked VMs, including a control pass on a server where the ServerAdminTools `player_killed` wrapper was not present. The strongest common source is the generic script-emitted `INFO: KILL ...` line, while another checked server additionally emitted ServerAdminTools event-wrapper lines. These still appear to be script/mod component output rather than a vanilla server logging contract. Observed patterns include:

- player killed by AI;
- player killed by another player;
- player suicide;
- teamkill;
- victim and instigator names/IDs;
- victim and instigator faction labels;
- position, damage type, and hit zone;
- generic `INFO: KILL ENEMY`, `INFO: KILL SUICIDE`, `INFO: KILL TK`, and `INFO: KILL OTHER_DEATH` lines;
- optional ServerAdminTools `player_killed` event wrapper with `friendly: true/false` when that component emits it.

Teamkill parsing is confirmed when `INFO: KILL TK` is present. On the wrapper-emitting server, the optional ServerAdminTools wrapper also confirmed `friendly: true`. On the no-wrapper control server, the generic combat log still emitted kill and suicide records.

Based on observed logs, kill/death/KD is feasible for the current deployments if armactl treats these script-emitted combat lines as the source of truth and stores the detected source/capability with each event. It is still not safe to claim vanilla/no-mod combat statistics until a no-mod server produces the same lines.

### Faction / Role / Team

Faction joins were observed in script lines that include player name, session player ID, stable UUID, faction resource, and a side label such as a short faction name. These can be parsed when the full line is present.

The no-wrapper control pass confirmed this pattern around a live manual test: the journal contained a player identity update, a faction join line, and subsequent `KILL ENEMY` / `KILL SUICIDE` lines for the same stable UUID.

Side/team is not separately authoritative in the observed logs. It can be inferred only from faction labels in faction and kill/death lines.

Per-player role/loadout selection was not observed. Logs include loadout/squad component initialization and warnings, but no reliable per-player role/loadout event pattern was confirmed.

### Mission Lifecycle

Mission/server lifecycle markers were observed in retained console logs and the service journal:

- game created;
- playthrough starting for a mission resource;
- server registered;
- online game state entered;
- ServerAdminTools `game_started` event;
- persistence save started for shutdown.

Mission start is parseable. Mission end is less explicit in the sampled logs and should be treated as shutdown/save/service-lifecycle inference unless a future explicit finish event is observed.

### Restart / Shutdown / Crash

Shutdown-save markers are present. Service state was read only and showed the game service active during the pass. A crash-report marker file exists, but no useful crash detail was confirmed from the sampled content.

Restart/shutdown history can be inferred from journal/service lifecycle and game startup/shutdown-save sequences. Crash classification needs more evidence before it should become a structured event type.

### Player Count / FPS Telemetry

Aggregate telemetry is parseable now:

- `NETWORK: Players connected: N / N` gives count changes near connect/disconnect moments;
- `FPS: ... Player: N ...` gives count plus FPS/frame/memory/AI/RTT style telemetry.

This telemetry does not identify players. The latest retained console logs sampled mostly showed `Player: 0`; nonzero player counts were confirmed in the service journal for earlier active sessions.

### Admin / RCON / Moderation Events

RCON polling traces were observed for local roster polling commands such as player-list requests. They are useful as evidence that the current roster collector is active, but they are not admin moderation events.

No real kick, ban, admin action, or moderation event was confirmed in the bounded game-log sample.

## Sample Sanitized Patterns

These examples show parser shapes only. They are not raw log excerpts.

```text
BACKEND : Authenticated player: rplIdentity=<RPL_ID> identityId=<PLAYER_ID> name=<PLAYER_NAME>
DEFAULT : BattlEye Server: 'Player #<SLOT> <PLAYER_NAME> (<PLAYER_ADDRESS>) connected'
DEFAULT : BattlEye Server: Setting GUID for player identity=<RPL_ID>, GUID=<STEAM64_OR_GUID>
DEFAULT : BattlEye Server: 'Player #<SLOT> <PLAYER_NAME> - BE GUID: <BE_GUID_HASH>'
NETWORK : ### Updating player: PlayerId=<SESSION_PLAYER_ID>, Name=<PLAYER_NAME>, rplIdentity=<RPL_ID>, IdentityId=<PLAYER_ID>
SCRIPT  : ServerAdminTools | Event serveradmintools_player_joined | player: <PLAYER_NAME>, identity: <PLAYER_ID>
```

```text
RPL     : ServerImpl event: disconnected (identity=<RPL_ID>), group=<GROUP>, reason=<REASON>
DEFAULT : BattlEye Server: 'Player #<SLOT> <PLAYER_NAME> disconnected'
NETWORK : Player disconnected: connectionID=<CONNECTION_ID>
```

```text
SCRIPT : INFO: Faction: player <PLAYER_NAME> (playerID = <SESSION_PLAYER_ID> | UUID = <PLAYER_ID>) has joined faction #<FACTION_RESOURCE> (<SIDE_LABEL>)
```

```text
SCRIPT : INFO: KILL ENEMY: <VICTIM_NAME> (playerID = <VICTIM_SESSION_ID> | UUID = <VICTIM_ID>) from <VICTIM_FACTION> faction at <POSITION> was killed by <INSTIGATOR_NAME_OR_AI> from <INSTIGATOR_FACTION> faction. With last inflicted damage type <DAMAGE_TYPE> to the '<HIT_ZONE>' hit zone
SCRIPT : INFO: KILL SUICIDE: <VICTIM_NAME> (playerID = <VICTIM_SESSION_ID> | UUID = <VICTIM_ID>) from <VICTIM_FACTION> faction at <POSITION> killed himself! With last inflicted damage type <DAMAGE_TYPE> to the '<HIT_ZONE>' hit zone
SCRIPT : INFO: KILL TK: <VICTIM_NAME> (playerID = <VICTIM_SESSION_ID> | UUID = <VICTIM_ID>) from <VICTIM_FACTION> faction at <POSITION> was killed by <INSTIGATOR_NAME> (playerID = <INSTIGATOR_SESSION_ID> | UUID = <INSTIGATOR_ID>) from <INSTIGATOR_FACTION> faction. With last inflicted damage type <DAMAGE_TYPE> to the '<HIT_ZONE>' hit zone
SCRIPT : ServerAdminTools | Event serveradmintools_player_killed | player: <VICTIM_NAME>, instigator: <INSTIGATOR_NAME_OR_AI>, friendly: true|false
```

```text
ENGINE  : Game successfully created.
DEFAULT : [SaveGameManager] Starting new playthrough nr.<N> '' for mission '<SCENARIO_RESOURCE>'.
DEFAULT : Entered online game state.
SCRIPT  : ServerAdminTools | Event serveradmintools_game_started | no data
DEFAULT : [PERSISTENCE] Save (SHUTDOWN) started.
NETWORK : Players connected: <COUNT> / <COUNT>
DEFAULT : FPS: <FPS> ... Player: <COUNT> ... [C<CONNECTION_ID>] ... Rtt: <MS> ms
```

## Parser Feasibility

Reliably parseable now, with bounded journal/log reads:

- player connect/start-session candidates when auth/update/join lines are present;
- player name snapshots tied to stable IDs when `identityId` or roster join is available;
- aggregate player count and FPS telemetry;
- mission/server start and shutdown-save lifecycle markers;
- faction join when the full script line is present;
- combat kill/death/suicide/teamkill events when generic `INFO: KILL ...` script lines are present and the parser records the source/capability.

Parseable only heuristically:

- disconnect/end-session pairing when the stable ID is absent from the disconnect line;
- session duration when logs rotate or the journal window starts after the connect line;
- mission end from shutdown/save/service stop sequences;
- optional ServerAdminTools `player_killed` wrapper correlation, because not every checked VM emits that wrapper;
- side/team from faction labels.

Not observed as reliable log events:

- vanilla/no-mod kill, death, KD, or teamkill source;
- per-player role/loadout selection;
- kick, ban, admin command, or moderation action;
- complete crash reason/event classification.

Needs RCON or registry join:

- durable player identity should be joined to the existing reliable roster/registry model;
- disconnect correlation should use recent connect/update state plus roster snapshots where available;
- player names should be stored as snapshots/history, not treated as primary identity.

Needs a mod or another source:

- authoritative combat statistics on servers that do not emit the current `INFO: KILL ...` script lines;
- authoritative role/loadout changes;
- explicit side/team changes beyond faction labels;
- moderation event history if it is not emitted into logs by the chosen source of truth.

## Phase 4a Session Boundary Classification

Use log events as evidence, not as an automatic promise that a complete session exists. Backend authentication and network player updates with stable `identityId` values are the strongest session-open candidates. RCON roster observations with reliable IDs are reliable presence snapshots, but they only support `first observed online` when used by a future scanner; the existing manual refresh job remains registry-only. Faction joins and combat lines with stable UUIDs are presence/faction/combat evidence and can open only inferred sessions when no better connect event is available. A2S count and FPS `Player: N` telemetry never identify players and must not open identified sessions.

Session close is reliable only when a disconnect event can be tied to the same reliable ID. Disconnects that carry only `rplIdentity`, connection ID, BE slot, or display name are heuristic unless the scanner has a single matching active session. Server shutdown/save/service-stop markers can close active sessions as server-boundary events with an explicit reason. RCON disappearance requires repeated successful fresh snapshots plus a grace period; failed RCON queries mean unknown state. A2S count drops can support low-confidence stale/empty decisions but cannot decide which player left.

Manual imported logs may be old or partial. Sessionization must use event `observed_at`, source refs, confidence, and scanner checkpoints so historical imports do not overwrite newer live online/offline state. Store only parsed fields and sanitized source refs; do not store player network addresses, raw log lines, raw absolute paths, or secrets.

## Proposed DB/Event Model Impact

`player_sessions` should support:

- `player_id` from the reliable registry path;
- `name_snapshot` at connect and last observation;
- `open_observed_at`, optional `close_observed_at`, and `last_seen_at`, labelled as observed/inferred rather than exact joined time unless backed by explicit connect evidence;
- open/last/close source labels, source refs, and confidence;
- correlation fields such as `rpl_identity`, `connection_id`, `session_player_id`, and `be_slot`;
- optional external GUID/hash fields only if product/privacy review decides they are needed;
- optional `faction` and `side_label` snapshots;
- `end_reason` for disconnect/shutdown/unknown;
- `source_ref` such as a sanitized journal cursor, file marker, or line marker, without storing raw absolute paths or raw lines.

`player_events` should support:

- `event_type` such as `connect`, `disconnect`, `faction_join`, `player_count_sample`, `fps_sample`, `mission_start`, `shutdown_save`, `kill`, `death`, `teamkill`, `role_change`, `moderation_action`, or `crash`;
- `observed_at`, `source`, and `confidence`;
- nullable `player_id` and `session_id`;
- nullable actor/target player IDs for future combat or moderation events;
- bounded `metadata_json` for parsed fields only;
- `source_ref`, not raw log text or raw absolute paths.

Safe to write in an initial sessions/history slice:

- connect/session-open candidates with reliable IDs;
- disconnect/session-close candidates with confidence levels;
- player name snapshots/history already aligned with the registry model;
- aggregate count/FPS samples if useful for history charts;
- mission start and shutdown-save events.

Keep nullable or future-only:

- kill/death/teamkill/KD;
- role/loadout;
- side/team separate from faction;
- moderation actions;
- crash reason/detail;
- external GUID/hash fields unless clearly needed.

## Privacy/Safety Rules

- Do not store player network addresses by default.
- Do not store credentials or raw private paths in player history tables, events, audit details, or public docs.
- Treat player names, stable player IDs, BE hashes, and external GUIDs as moderation data.
- Store parsed, bounded fields and source references instead of raw log lines.
- Keep public status count-only unless a separate public-data review expands it.
- Do not copy raw large logs into the repo.
- Redact player names/IDs in docs unless the value is already intentionally public and needed for operator-facing behavior.

## Recommended Next Slice

- Start sessions/history without a gameplay mod only for connect/disconnect, last seen, names, reliable IDs, faction snapshots, mission lifecycle, and aggregate telemetry.
- Add one more live observation pass during real player activity before finalizing disconnect-pairing confidence rules and log-retention assumptions.
- Do not ship kill/death/KD claims as vanilla/no-mod functionality. Treat current kill/death parsing as mod-source dependent and experimental until the event source is explicitly chosen.
- Keep the next implementation docs/code slice focused on a journal/log/RCON registry join with no player network-address storage by default.
