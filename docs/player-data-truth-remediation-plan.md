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

Goal: document and preserve the truth boundary for authenticated `/players` current-player columns. Slices B-E implement parser proof, checkpoint/freshness metadata, reconnect-aware play-session windows, and read-only scoped aggregation. Slice F2-a adds the local explicit systemd oneshot/timer foundation for the shared F1 runner; installation does not enable or start the timer, and activation remains explicit. Discord/public enrichment, materialized counters, role truth, K/D, production enablement, and player-session scheduler enablement remain outside this work.

#### Field Verdicts

| Field | Existing source/type | Reliable ID and current-player aggregation | Edge cases | Verdict |
| --- | --- | --- | --- | --- |
| `Kills` | Stable stored `kill` events from script combat evidence; `teamkill` and optional ServerAdminTools hint rows are separate. | Slice E counts only exact/derived-time `kill` rows inside the proven fresh play-session window where reliable non-AI `instigator_id` equals the current roster reliable ID. The current-roster cache and global `PlayerSummary` totals are not used. | Teamkills, suicides, AI/unknown/name-only instigators, ambiguous timestamps, and events outside the session/freshness window do not count. Duplicate ingest is neutralized by stored event dedupe. | `implemented for authenticated current UI with nullable proof gate` |
| `Deaths` | Deaths are derived from stable victim fields on `kill`, `teamkill`, `suicide`, and `other_death`. | Slice E counts only rows inside the proven fresh play-session window where reliable `victim_id` equals the current player reliable ID. | Killer identity is irrelevant to victim counting. Unknown victims, unstable patterns, ambiguous timestamps, and events outside the window do not count. Suicide counts once as a death. | `implemented for authenticated current UI with nullable proof gate` |
| `TK` | Stable `teamkill` event type plus the stored teamkill flag from script combat evidence. | Slice E counts only exact/derived-time teamkill rows inside the proven fresh play-session window where reliable non-AI `instigator_id` equals the current player reliable ID. | Do not infer TK from equal faction labels or hint-only wrappers. Unknown/AI instigators do not count, and TK never increments `Kills`. | `implemented for authenticated current UI with nullable proof gate` |
| `Faction` | `faction_join` and stable combat rows carry structured player/victim/instigator faction labels. | Slice E returns the last structured faction evidence for the current reliable player inside the proven fresh play-session window. | It is labelled last-known session evidence, not guaranteed current truth. Missing reliable ID, session proof, fresh coverage, or structured in-window evidence renders `—`. | `implemented as last-known authenticated session evidence` |
| `Role` | No reliable per-player role/loadout event source is parsed or stored. | There is no existing source to join to current players. | Do not infer role from faction, name, loadout component log spam, Discord data, or current roster. | `blocked` |
| `Session first observed` | `player_sessions.open_observed_at` exists. For auth/update it can be first connect/update evidence; for live scanner it is first observed in a reliable roster; for faction/combat it can be inferred presence evidence. | The authenticated `/players` slice shows `Session first observed` for an open stored session matched to the current reliable roster ID. It is not exact join time by default. | After restart/lifecycle close, stale-close, imported old logs, missing close evidence, or inferred combat/faction openings, show `—` or a truth-labelled first-observed value with confidence/source. Do not show exact `Joined time` unless the source is an explicit connect/auth event and the UI says so. | `implemented as first-observed authenticated web-only evidence` |

#### Source-Of-Truth Matrix

| Source | Existing storage | Safe future use for current players | Not safe for |
| --- | --- | --- | --- |
| Current roster cache (`web_current_roster_cache*`) | Sanitized display name, reliable ID when present, source, count/source/freshness flags. | Select the currently observed reliable IDs eligible for read-only enrichment. | Combat/faction/session truth by itself, synthetic rows from A2S count, role, or exact joined time. |
| `player_log_events` | Sanitized auth/update/faction/combat/disconnect/lifecycle events with source refs, confidence, stable IDs where present, combat booleans, dedupe keys, and trusted occurrence-time metadata. | Slice E read-only Kills/Deaths/TK/Faction evidence when bounded by reliable ID, stable event rules, the proven play-session window, and fresh coverage cutoff. | Live online truth, public player IDs, raw log lines/paths, or Discord enrichment. |
| `player_sessions` plus lifecycle boundaries | One-open-session-per-reliable-ID rows with `play_session_id`, `server_run_key`, reconnect metadata, open/close evidence, and lifecycle boundary storage. | Prove the current aggregation window, preserve reconnects within grace, split after grace/lifecycle, and label Session first observed. | Exact joined/disconnect time, role, or complete online truth. |
| `PlayerSummary` / `list_player_summaries*` | Compact global counters derived from all stored events for known players. | Useful for a future known-player/history summary page if labelled as stored-event totals. | Current-player current-session columns. |
| `public_stats` / Discord | Safe public roster names/counts and status text. | No change for the web stats slice. | Any combat/session/faction/role enrichment before the authenticated web truth slice is implemented and tested. |

#### Allowed And Blocked Implementation Slices

The initial authenticated web-only enrichment proved that a loose open-session window was insufficient and was guarded by Slice A. The durable source of truth is [player-session-stats-contract.md](player-session-stats-contract.md).

Slice B parser fixture audit is implemented. It defines stable auth/update/faction/combat/disconnect/lifecycle shapes, trusted event-time rules, and diagnostic-only patterns.

Slice C automatic log ingest foundation is implemented. The explicit allowlisted player-log job records checkpoint and freshness metadata, skips unchanged logs, handles controlled source failures, and emits counts-only sanitized output. GET pages do not start ingest or create `players.db`; no daemon, timer, or broad scheduler is enabled.

Slice D play-session/reconnect modeling is implemented. `player_sessions` carries the durable play-window key, server-run boundary key, reconnect metadata, and lifecycle split proof. Reconnect merge requires same reliable ID/server run, compatible close reason, grace compliance, no intervening lifecycle boundary, and no identity conflict.

Slice E session-scoped current stats aggregation is implemented. `/players` and `/players/current.json` use normalized current-roster reliable IDs to read existing `players.db` in query-only mode. Stats are available only when an open Slice D session is proven in the same server run, no lifecycle boundary crosses the window, Slice C status is `fresh`, `last_success_at` is no more than five minutes old, at least one scanned checkpoint exists, and coverage reaches the session start. The inclusive query window is play-session open evidence through `last_success_at`.

Implemented event rules:

- Kills: stable `kill`, exact/derived occurrence time, reliable non-AI instigator equal to the current player, teamkill excluded.
- Deaths: stable `kill`, `teamkill`, `suicide`, or `other_death` where reliable victim equals the current player.
- TK: stable `teamkill` where reliable non-AI instigator equals the current player.
- Faction: last structured faction evidence for that player inside the window, labelled last-known session evidence rather than current truth.
- Reconnect within grace keeps the original stats window; reconnect after grace or lifecycle boundary starts a new one.

GET handling remains non-mutating. The stats service does not observe/close sessions, run ingest, scanner, sessionizer, or maintenance, enqueue jobs, create `players.db`, or persist counters. Missing reliable ID/database/schema/session/server-run proof/checkpoint/freshness returns nullable fields with a controlled reason; UI renders `—`. A true `0` is rendered only after the complete fresh scoped query proves no matching events.

The authenticated details row/API may expose only safe source/freshness/window timestamps and reconnect-merge status. It does not expose raw source refs, raw log lines, raw paths, IPs, secrets, raw correlation IDs, internal server-run/play-session keys, or public player IDs.

Blocked until a later explicit slice: production player-log timer enablement/observation, automatic player-session scheduler enablement, materialized counters, current-roster cache stat persistence, Discord/public combat or faction enrichment, role/loadout display, K/D, ban/kick coupling, public player IDs, and any source that stores or renders raw log lines, raw paths, RCON rows, IPs, or secrets. Role remains placeholder-only.

Slice E acceptance is covered by focused tests for real scoped values, teamkill separation, victim-only deaths, out-of-window exclusion, reconnect merge/split, lifecycle boundaries, stale/missing freshness, missing database/session/reliable ID, GET read-only behavior, and sensitive-output redaction.

#### Slice F1: Reusable Foreground Player-Log Ingest Foundation — implemented locally

The manual-only behavior was caused by one-shot orchestration living inside the `players:collect-log-events` web job handler. The web path persisted a job and dispatched it on a daemon thread, which is appropriate only while the web process remains alive. A short-lived CLI cannot safely enqueue that daemon-thread job and exit, because the interpreter may terminate before the worker completes.

The implemented contract is:

```text
manual web job ─┐
                ├─> shared synchronous one-shot ingest service
foreground CLI ─┘          │
                           ├─ existing allowlisted log discovery
                           ├─ existing collector/parser
                           ├─ existing checkpoint planner
                           ├─ existing registry ingest
                           └─ existing freshness storage
```

Audit/design decisions:

1. One-shot orchestration now belongs to `web/services/player_log_ingest.py`; it resolves the fixed instance allowlist, takes the shared scope lock, plans checkpoint work, invokes the existing collector, persists checkpoints/freshness through existing registry helpers, waits for completion, and returns a typed counts-only result.
2. The web job is a thin adapter. It owns web-job progress/result formatting and the existing manual intent/outcome audit semantics, while the shared service contains no route or CLI formatting.
3. The daemon-thread web job runner is not used by the foreground CLI. `armactl players log-ingest run --once` calls the shared service directly and blocks until the complete ingest result is available.
4. Existing `web_jobs` active-job dedupe remains the manual request ledger. Cross-process overlap between a manual worker and foreground CLI is prevented by one nonblocking instance/scope `flock`; the lock is released by the kernel on process death, so a leftover lock file is harmless and no second persistent dedupe ledger or process-kill path is introduced.
5. Checkpoints and freshness continue to survive restart in the existing instance `players.db` tables. Failed/partial runs cannot manufacture a fresh success; the existing last-success preservation contract remains authoritative.
6. Manual web requests retain one intent record and one terminal outcome record. Foreground one-shot runs write one sanitized counts-only outcome record. Read-only status and individual files/lines do not generate audit records, avoiding audit spam.
7. `player_log_collector.py`, `player_log_events.py`, and `player_registry.ingest_player_log_events` remain the only parser/collector/storage source of truth. No parser, SQL event pipeline, checkpoint pipeline, freshness pipeline, materialized K/D counters, or session/reconnect semantics were duplicated or changed.

The explicit CLI foundation is `armactl players log-ingest run --once [--data-root ...]` plus read-only `armactl players log-ingest status [--data-root ...]`. Status opens only an existing `players.db` through the registry read-only path, does not create or migrate state, and returns controlled empty/unavailable summaries.

Slice F2-a now implements the separate local service foundation: generated `armactl-player-log-ingest.service` and `armactl-player-log-ingest.timer` units, explicit install/enable/disable/status commands, a 120-second completion-relative cadence, direct project `.venv` execution, owner/private-permission controls, a 360-second bounded failure guard, restrained CPU/I/O priority, controlled scheduled lock skips, counts-only journald output, and bounded failure/freshness-transition/recovery audit. Installation never enables or starts the timer and preserves an existing timer's enablement state. No production, SSH, deploy, restart, or actual systemd action belongs to this local implementation.

The acceptance criterion “current stats become fresh without pressing the manual button” remains open for F2-b. F2-b must deploy Serhiivka first, explicitly install and enable the timer, observe repeated non-overlapping cycles and fresh stored ingest metadata, confirm no `armareforger.service` restart/mutation, and keep the separate player-session scheduler disabled. Stats still require a proven open play session. Chervonopilya follows only after Serhiivka evidence and explicit approval.

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
