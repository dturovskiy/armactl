# Player Data Truth And VM Smoke Remediation Plan

This plan tracks the player-data correctness and operator-UX issues found after the Phase 4 player/session foundation and the latest VM smoke. It is intentionally separate from feature work: first make the existing player data truthful, explainable, and stable.

Do not put private production hostnames, public IPs, provider details, or gateway rules in this public document. Production evidence belongs in private ops notes.

## Current Findings

### 1. Player Event Timestamps Are Not Truthful Enough

Observed problem: player event rows can show the time when the operator clicked `Update events from logs`, not the real time when the event happened in the server log. The same ambiguity affects known-player first/last seen fields and player-session observed/close timestamps.

Current risk:

- Operators can mistake ingestion/collection time for real event time.
- Historical player activity becomes misleading after delayed log collection.
- Session rows can look like a player joined exactly when a manual job ran.
- UI labels do not clearly distinguish exact log time, roster observation time, storage time, and inferred session time.

Target contract:

- `occurred_at`: when the event actually happened according to the source evidence, if reconstructable.
- `observed_at`: when armactl observed a player in a live source such as current roster/RCON.
- `ingested_at` or `collected_at`: when armactl stored the evidence.
- `created_at` / `updated_at`: row bookkeeping only, not player activity.
- Every UI label must say which timestamp class is being displayed.

Important nuance: Arma console lines commonly include a time-of-day without a full date. The collector may need to derive the date from the log directory/file naming and handle midnight rollover. Existing historical rows must not be silently rewritten as exact event times unless the source evidence can prove the timestamp.

### 2. Player Event History Has Too Much Noise And Weak Details

Observed problem: the history view contains many low-value auth/update/faction rows and diagnostic rows, while useful details are often missing or shown as `-`.

Current risk:

- Operators cannot quickly answer "what happened to this player?"
- Default history reads like raw ingestion/debug output rather than a player activity journal.
- Session-evidence rows and player-facing events are mixed in ways that are hard to reason about.

Target contract:

- Default `Player events` should show high-signal player events first.
- Low-level session evidence should remain available in a separate mode/filter.
- Details should be structured and safe: faction, side, correlation key, source kind, confidence, and sanitized source reference where useful.
- No raw log lines, raw paths, IPs, secrets, or full RCON rows.
- Repeated rows from the same log marker should be deduped or grouped when that preserves truth.

### 3. Player Sessions Need Clearer Meaning

Observed problem: operators do not know what session job buttons do, and the meaning of `open`, `closed`, `Observed`, `Last observed`, and `Inferred close` is unclear.

Truth contract:

- `Session not closed` means armactl has stored session evidence that started/updated a session and has not yet stored reliable close evidence. It does not guarantee the player is online right now.
- `Session closed` means armactl stored close evidence or inferred a close from a controlled rule such as lifecycle/server boundary, reliable disconnect evidence, repeated reliable roster absence, or stale timeout.
- `First evidence` means the first evidence time stored for the session, not necessarily exact join time unless the source is a reliable connect event.
- `Last evidence` means the latest evidence time in that session.
- `Close evidence` means the stored close/inferred-close time and source. It is not necessarily an exact disconnect time.
- Session labels must not make online, playtime, K-D, role, or current faction truth claims; current online truth remains the `/players` current roster surface.

Job button contract:

- `Scan live sessions`: one-shot manual current roster observation into sessions. It can open/update stored session evidence for reliable current roster IDs and can advance safe absence windows. It is not a daemon, scheduler, or poller.
- `Sessionize logs`: reads already stored `player_log_events` and converts reliable evidence into session rows. It does not read live logs directly.
- `Session maintenance`: closes stale open sessions and runs retention cleanup according to explicit rules. It is not a live scanner and does not enable automatic scheduling.

### 4. VM Smoke Finding: False `waiting_for_telemetry`

Observed problem: a production VM can report public/dashboard status `waiting_for_telemetry` while fresh FPS telemetry is available and player counts are current.

Root cause found in smoke: the latest console log can be flooded by mod/script exception spam. The FPS parser still finds fresh FPS lines in the larger file tail, but operational-status detection scans only a shorter recent-line window and may miss the latest FPS line. This makes the status heuristic claim it is waiting for telemetry even while `fps_text` and `telemetry_age` prove fresh telemetry exists.

Implemented contract:

- Fresh FPS metrics from the same latest console log prevent a fallback to `waiting_for_telemetry` when only irrelevant newer log spam pushed the FPS line outside the short operational-status window.
- Service stopped/deactivating/failure states remain authoritative in the shared dashboard/public status snapshot.
- Fresh startup failure, mod-download, mission/config-error, and starting markers still win over older FPS evidence while they are the newest relevant blocking marker.
- Stale FPS telemetry does not make a server ready.
- Operator diagnostics are bounded and redacted before they can flow through status DTOs.

### 5. VM Smoke Finding: Wrapper/Bootstrap Drift

Observed problem: normal `./armactl` can be blocked by stale dependency/bootstrap state on a production VM, while `ARMACTL_PYTHON=.venv/bin/python ./armactl ...` works.

Root cause found in smoke: the wrapper compares the current `pyproject.toml` hash and requested dependency mode with `.venv/.armactl-pyproject.sha256` before it runs the CLI. When the stamp is stale or the installed mode does not satisfy the requested command, the wrapper correctly enters the dependency/bootstrap path. In non-interactive smoke contexts, stdin is not a TTY, so the wrapper refuses to run installer work that may require sudo/apt/pip and exits with the interactive-TTY guard instead of mutating the VM.

Implemented contract:

- Normal `./armactl` still fails closed on missing/stale/mismatched dependency state; it does not silently skip dependency mismatch.
- The non-interactive wrapper error now prints the requested bootstrap mode, a read-only diagnosis command, the supported interactive recovery command, and the temporary override wording.
- `./scripts/bootstrap.sh --help` is safe and does not enter the installer path.
- `./scripts/bootstrap.sh --check [--prod|--web|--dev]` verifies the existing virtualenv, dependency stamp, mode, and importability without running apt, sudo, pip, creating a virtualenv, or editing files.
- Successful supported bootstrap remains the only normal stamp refresh path and writes the current `pyproject.toml` hash plus requested mode.
- `ARMACTL_PYTHON=.venv/bin/python ./armactl ...` remains an explicit temporary smoke escape hatch only when the venv is known good, not the production contract.

## Implementation Slices

### Slice 0: Read-Only Data Audit

Goal: prove the current timestamp/data shape before changing schema or UI.

Tasks:

- Inspect `players.db` schema and sample rows for `player_log_events`, `players`, `player_names`, and `player_sessions`.
- Compare stored event timestamps against safe source refs and available log file timestamps on a test fixture.
- Confirm whether stored history rows represent ingestion time, parsed log time, or source observation time.
- Identify which event types have enough source evidence to reconstruct `occurred_at`.
- Document legacy rows that cannot be reconstructed without raw paths/log lines.

Validation:

- Read-only queries only.
- No production writes, no jobs, no service restart.
- A short report listing timestamp fields, semantics, and migration needs.

### Slice 1: Timestamp Contract, Parser, And Storage — implemented

Goal: store and expose truthful event time separately from ingestion/row time.

Status: implemented. Player log events store `occurred_at`, `observed_at`, `collected_at`, `time_source`, and `time_confidence` so exact, derived, and ambiguous/legacy event times stay distinguishable.

Tasks:

- Add or confirm schema fields for exact/derived event occurrence time and ingestion time.
- Parse log event occurrence time from source evidence when possible.
- Derive the date from safe log context without storing raw absolute paths.
- Handle midnight rollover and timezone assumptions explicitly.
- Keep legacy rows marked as legacy/ambiguous when exact occurrence time cannot be proven.
- Add tests for delayed collection: log event happened earlier, collection happens later, UI displays event time and not collection time.

Acceptance criteria:

- Pressing `Update events from logs` later does not make historical events look like they happened at click time.
- UI can still show collection/ingestion metadata in diagnostics if useful.
- No raw paths, raw log lines, IPs, or secrets are stored/rendered.

### Slice 2: Player History Noise And Details UX — implemented

Goal: make player history useful as an operator journal.

Status: implemented. `/players/history` defaults to high-signal player events and keeps lower-level session evidence in a separate diagnostics mode with structured, sanitized details.

Tasks:

- Define event categories: player activity, session evidence, system evidence, diagnostics.
- Keep default `Player events` mode high-signal, player-focused, and operator-facing.
- Keep `Session evidence` as the diagnostics/source-evidence view for lifecycle, disconnect, and correlation-only rows.
- Add structured details for faction/side, source kind, confidence, correlation IDs, and sanitized source refs where useful.
- Add dedupe/grouping rules for repeated rows from the same source marker when safe.

Acceptance criteria:

- Default history answers "who did what and when" without drowning in ingestion evidence.
- Session evidence remains available for diagnostics without deleting stored event rows.
- Details are useful, structured, and safe.
- Sessionization still sees the stored evidence it needs; Slice 2 does not expand session truth.

### Slice 3: Session Semantics And Job UX — implemented

Goal: remove ambiguity from sessions and background job controls.

Status: implemented. `/players/sessions` uses stored-session/evidence labels and compact job explanations/links for the explicit manual session jobs without claiming online, playtime, K-D, role, or current faction truth.

Tasks:

- Rename or clarify labels for stored open/closed sessions and evidence timestamps.
- Add concise operator-facing explanations in docs and compact UI helper text where it prevents ambiguous job actions.
- Make source/confidence/end-reason labels consistent: reliable roster evidence, log evidence, lifecycle/server boundary, stale absence / stale timeout, and high/medium/low confidence.
- Ensure stored open sessions are not presented as guaranteed online truth.
- Ensure close reasons clearly identify close evidence, inferred/stale absence, stale timeout, or server boundary without expanding session truth.
- Do not add an automatic scheduler, timer, service, poller, daemon, or background GET-side mutation.

Acceptance criteria:

- Operators can explain what each session job does before pressing it.
- Operators understand that `Session not closed` means "not closed by stored evidence yet", not "definitely online".
- Existing session rows remain readable without claiming false precision.
- Slice 3 does not add online/playtime/K-D/role/current faction truth and does not enable automatic session scheduling.

### Player Stats Truth Audit

Goal: document which already-collected player stats/attributes can safely back future `/players` current-player columns. This is docs-only audit scope; it does not add backend enrichment, UI data fields, Discord enrichment, DB migrations, jobs, schedulers, or production actions.

#### Field Verdicts

| Field | Existing source/type | Reliable ID and current-player aggregation | Edge cases | Verdict |
| --- | --- | --- | --- | --- |
| `Kills` | `player_log_events` stores `kill` and `teamkill` parsed from script `INFO: KILL ENEMY` / `INFO: KILL TK`; optional ServerAdminTools wrapper is only a hint. | Killer is reliable only when the script line includes `instigator_id`/UUID. The Slice A current UI guard does not use the old open stored-session window as current stat truth; `/players` shows `—` until play-session boundaries and automatic log freshness are proven. Existing `PlayerSummary` counters remain global stored-event counters, not current-session truth. | AI kills have no killer ID and do not count for a player. Suicide is not a kill. Unknown/missing killer is not assignable. Duplicate log collection is mitigated by event keys, and current aggregation counts stored event rows only. Teamkills are intentionally excluded from `Kills` and shown in `TK`. | `blocked for current UI; placeholder guarded until play-session contract` |
| `Deaths` | No separate generic death event exists. Deaths are derived from combat event victim fields on `kill`, `teamkill`, `suicide`, and `other_death`. | Victim is reliable only when `victim_id`/UUID is present. The Slice A current UI guard shows `—` for current deaths until the play-session/log freshness contract is implemented. | AI or environment deaths can still have a player victim and count as death when victim ID is reliable. Unknown victim is not assigned. Suicide counts as a death. Missing or ambiguous event time is not treated as current-session truth. | `blocked for current UI; placeholder guarded until play-session contract` |
| `TK` | `teamkill` event type and `teamkill` boolean are stored from `INFO: KILL TK`; optional ServerAdminTools hint may confirm `friendly: true` but has no reliable IDs by itself. | Count for killer only when `instigator_id` is reliable and inside a proven play-session window. Until that window and log freshness exist, the Slice A current UI guard shows `—`. | Do not derive teamkill from matching faction labels alone. Faction/team labels are evidence, not authoritative team truth. Unknown killer, AI, or hint-only wrapper rows cannot increment a player TK counter. | `blocked for current UI; placeholder guarded until play-session contract` |
| `Faction` | `faction_join` stores `faction_resource` and `player_faction`; combat rows store victim/instigator faction labels; `player_sessions` stores optional `faction`/`side` snapshots from evidence. | Current roster rows do not carry faction. The Slice A current UI guard keeps current `Faction` as `—` until last-known faction evidence can be tied to a proven play-session window with freshness metadata. | Show `—` for unreliable roster rows, missing `players.db`, no open session/evidence, stale/closed-only evidence, or conflicting old evidence. Do not call it current faction unless a future recent reliable source is defined. | `blocked for current UI; placeholder guarded until play-session contract` |
| `Role` | No reliable per-player role/loadout event source is parsed or stored. | There is no existing source to join to current players. | Do not infer role from faction, name, loadout component log spam, Discord data, or current roster. | `blocked` |
| `Session first observed` | `player_sessions.open_observed_at` exists. For auth/update it can be first connect/update evidence; for live scanner it is first observed in a reliable roster; for faction/combat it can be inferred presence evidence. | The authenticated `/players` slice shows `Session first observed` for an open stored session matched to the current reliable roster ID. It is not exact join time by default. | After restart/lifecycle close, stale-close, imported old logs, missing close evidence, or inferred combat/faction openings, show `—` or a truth-labelled first-observed value with confidence/source. Do not show exact `Joined time` unless the source is an explicit connect/auth event and the UI says so. | `implemented as first-observed authenticated web-only evidence` |

#### Source-Of-Truth Matrix

| Source | Existing storage | Safe future use for current players | Not safe for |
| --- | --- | --- | --- |
| Current roster cache (`web_current_roster_cache*`) | Sanitized display name, reliable ID when present, source, count/source/freshness flags. | Current row identity and current roster availability only. It can select which reliable IDs to enrich. | Kills, deaths, TK, role, faction, joined time, or session truth. |
| `player_log_events` | Sanitized auth/update/faction/combat/disconnect/lifecycle events with source refs, confidence, stable IDs where present, combat booleans, and dedupe keys. | Read-only event evidence for stored-log counters and last faction evidence, bounded by reliable ID and a defined time/session window. | Live online truth, public player IDs, raw log lines/paths, or Discord enrichment. |
| `player_sessions` | One-open-session-per-reliable-ID evidence rows with `open_observed_at`, `last_seen_at`, close evidence, confidence/source, optional faction/side, and safe scanner checkpoints. | Match current roster IDs to open stored evidence; label `First observed`, `Last observed`, `Last faction evidence`, and source/confidence. | Exact joined time, exact disconnect time, role, or complete online truth. |
| `PlayerSummary` / `list_player_summaries*` | Compact global counters derived from all stored events for known players. | Useful for a future known-player/history summary page if labelled as stored-event totals. | Current-player current-session columns. |
| `public_stats` / Discord | Safe public roster names/counts and status text. | No change for the web stats slice. | Any combat/session/faction/role enrichment before the authenticated web truth slice is implemented and tested. |

#### Allowed And Blocked Implementation Slices

Initial slice: authenticated web-only current-player enrichment. It added a read-only aggregation service over existing player_log_events and open player_sessions, but that open-session window is not final accepted current-session stat truth. It did not add a DB schema migration, materialized counters, new jobs, Discord/public stats changes, or GET-time session writes.

Correction after VM/manual smoke: the follow-up source of truth is [player-session-stats-contract.md](player-session-stats-contract.md). It requires automatic log ingest freshness, stable parser fixtures, play-session boundaries, reconnect grace, session-scoped aggregation, and no fake zeroes before current-player combat/faction columns can be considered stable. Slice A current UI guard is implemented: `/players` and `/players/current.json` now return placeholders and a short unavailable reason instead of open-session-window combat/faction values. Session-scoped stats remain blocked until parser, ingest freshness, play-session, and aggregation slices are implemented.

Slice B parser fixture audit is implemented in [player-session-stats-contract.md](player-session-stats-contract.md). It confirms which existing auth/update/faction/combat/disconnect/lifecycle patterns are stable parsed evidence, which observed lines remain blocked or diagnostic-only, which fields are required before future stat aggregation, and which parser/storage/sessionization/current-player tests cover the evidence.

Slice C automatic log ingest foundation is implemented in [player-session-stats-contract.md](player-session-stats-contract.md). The existing explicit player-log collection job now reuses the current collector/parser/player-registry ingest path, records players.db checkpoint and freshness metadata, skips unchanged logs, handles missing/rotated/truncated/oversized logs with controlled counts, and exposes only safe status/counts. GET pages do not start ingest or create players.db. No daemon, timer, broad scheduler, current-player stat aggregation, current-roster cache stats, Discord/public enrichment, or fake zeroes were added.

Slice D play-session/reconnect modeling is implemented in [player-session-stats-contract.md](player-session-stats-contract.md). player_sessions now carries an explicit durable play-window key, server-run boundary key, reconnect merge count/metadata, and last gameplay evidence metadata. Reconnect merge requires the same reliable ID, same server run, compatible close reason, default 10 minute grace, no lifecycle boundary in the gap, and no reliable identity/correlation conflict. Lifecycle boundary markers are recorded even if no session is open. Current-player stats remain placeholders; Slice E aggregation is still future.

Blocked until a later explicit slice: materialized counters, automatic daemon/timer/poller enablement, current-roster cache schema expansion, Discord/public enrichment, role/loadout display, K/D, ban/kick coupling, public player IDs, and any source that stores or renders raw log lines, raw paths, RCON rows, IPs, or secrets. Role remains placeholder-only.

GET handling: no stats/session GET writes. The stats service must not call `observe_player_session`, `close_player_session`, sessionizer/scanner/maintenance helpers, enqueue jobs, create `players.db`, or persist aggregation results. It should return `—` when `players.db` is absent, the roster row lacks a reliable ID, no matching open/evidence window exists, or the query cannot prove the value. Keep current roster cache freshness separate from session truth: the roster says who is currently observed; sessions/events explain stored evidence for those IDs.

Web labels: keep `Kills`, `Deaths`, `TK`, `Faction`, and `Role` as `—` until the play-session stats contract is satisfied; show a short unavailable reason in Details. `Session first observed` may be shown only as truth-labelled stored session evidence, not exact joined time. Do not show fake zeroes. A zero is allowed only when a proven play-session window, fresh log ingest metadata, and a completed scoped query prove no matching events; otherwise show `—`.

Acceptance criteria for the next slice, Slice E session-scoped stat aggregation:

- Slice C freshness status is used as a safe gate, and missing, partial, or failed freshness cannot prove current stats.
- Slice D play-session windows are used for scope; no combat stat crosses a lifecycle boundary or failed reconnect merge.
- Combat events require reliable victim/instigator IDs, trusted occurrence times, and membership in the player play-session window.
- Teamkills stay out of Kills, AI/unknown instigators are ignored for player kills/TK, and zero is shown only after a complete scoped query proves zero.
- Current roster rows without reliable IDs keep all future stat/faction/session fields as placeholder-only.
- Missing players.db, old schema, missing events, or stale freshness degrades to placeholders without creating files or tables from GET.
- No fake zeroes, no K/D column, no Discord enrichment before web truth, no GET writes, no public player IDs, and no raw log lines/paths/IPs/secrets.

### Slice 4: Operational Status Telemetry Fix — implemented

Goal: fix false `waiting_for_telemetry` during log spam.

Implemented behavior:

- The log heuristic still checks the recent operational-status window for fresh blocking markers first: startup failure, mod download/retry, mission/config error, and starting markers.
- If no blocking marker is found there, it searches the same larger bounded console-log tail used by the FPS parser for FPS telemetry before returning `waiting_for_telemetry`.
- The dashboard snapshot resolves operational status through a shared precedence path before both dashboard JSON/page rendering and `/public/server-status.json` consume it.
- Service stopped, deactivating/stopping, and failed states are authoritative over FPS telemetry.
- Fresh FPS telemetry resolves false waiting states to `ready`/`success`; stale FPS telemetry does not.
- Status diagnostics remain bounded and redacted.

Acceptance criteria:

- Public/dashboard status reports ready when fresh FPS telemetry is available despite noisy logs.
- Real startup/config/service failure states still display correctly.
- Production VM smoke should no longer alternate between ready and waiting only because of log spam; final VM confirmation belongs to Slice 6.

### Slice 5: Wrapper/Bootstrap Drift Recovery — implemented

Goal: make final smoke pass without relying on `ARMACTL_PYTHON` overrides.

Implemented behavior:

- The wrapper dependency guard remains fail-closed: stale `pyproject.toml` hash, missing stamp, insufficient dependency mode, or failed runtime imports require supported bootstrap recovery.
- Non-interactive wrapper recovery output now names the requested mode and points operators to `./scripts/bootstrap.sh --check <mode>` for read-only diagnosis and `./scripts/bootstrap.sh <mode>` for interactive recovery.
- `scripts/bootstrap.sh --help` exits before any apt/sudo/bootstrap path.
- `scripts/bootstrap.sh --check [--prod|--web|--dev]` is a non-mutating check for venv presence, stamp hash, mode compatibility, and importability.
- Successful `scripts/bootstrap.sh [--prod|--web|--dev]` remains the supported stamp refresh path and writes `.venv/.armactl-pyproject.sha256` after dependency install succeeds.
- `ARMACTL_PYTHON=.venv/bin/python ./armactl ...` is documented only as a temporary read-only smoke workaround when the virtualenv is known good.

Acceptance criteria:

- Normal `./armactl --version` and read-only smoke commands should pass on production VMs after the supported bootstrap refresh.
- If a VM cannot run normal wrapper non-interactively before refresh, the supported recovery path is documented and tested.

### Slice 6: Final VM Smoke Re-Run

Goal: confirm the fixes on current code.

Status: public health/status, production SSH read-only ops smoke, and authenticated browser UI smoke have passed for the current `feat/web-interface` deployment baseline. Future deploys should repeat the same smoke gate before treating the web UI as deployment-ready; public `main` merge readiness still requires the separate extraction/docs-boundary gate.

Tasks:

- Fast-forward production checkouts only when explicitly approved.
- Restart only `armactl-web.service` unless a slice explicitly requires game service changes.
- Verify health, public status, service states, restart timer, jobs page state, players pages, and recent journals.
- Keep private hostnames and IPs out of public docs.
- Treat unauthenticated health/public-status checks as public smoke only; authenticated UI smoke requires a normal admin/operator browser session.

Acceptance criteria:

- Both production VMs are on current `feat/web-interface`.
- Web health/public status are stable.
- Player history timestamps are truthful.
- Session labels/jobs are understandable.
- Authenticated browser pages are checked through a normal admin/operator session, not by direct DB session creation or unauthenticated requests.
- No new tracebacks or warning spikes appear in recent web journals.

## Out Of Scope For This Plan

- Ban/kick/banlist mutations.
- Player IP storage.
- Current-session K/D, role, exact joined time, or exact faction truth unless a reliable source is explicitly added.
- Automatic player-session daemon/timer enablement.
- Discord player enrichment beyond existing safe roster/status publishing.
- Arbitrary file manager behavior, recursive deletes, raw log readers, or raw path/log-line exposure.
