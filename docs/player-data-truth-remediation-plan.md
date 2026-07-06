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

- `Stored open` means armactl has stored session evidence that started/updated a session and has not yet stored reliable close evidence. It does not guarantee the player is online right now.
- `Stored closed` means armactl stored close evidence or inferred a close from a controlled rule such as lifecycle/server boundary, reliable disconnect evidence, repeated reliable roster absence, or stale timeout.
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

Target contract:

- Final smoke should pass with the normal wrapper, or the operator docs should define the supported recovery path.
- Do not hand-edit stamp files in production.
- Prefer supported bootstrap/repair flow or a narrow wrapper status/repair improvement if the current flow cannot be safely run non-interactively.

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

### Slice 1: Timestamp Contract, Parser, And Storage

Goal: store and expose truthful event time separately from ingestion/row time.

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

### Slice 2: Player History Noise And Details UX

Goal: make player history useful as an operator journal.

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

### Slice 3: Session Semantics And Job UX

Goal: remove ambiguity from sessions and background job controls.

Tasks:

- Rename or clarify labels for stored open/closed sessions and evidence timestamps.
- Add concise operator-facing explanations in docs and compact UI helper text where it prevents ambiguous job actions.
- Make source/confidence/end-reason labels consistent: reliable roster evidence, log evidence, lifecycle/server boundary, stale absence / stale timeout, and high/medium/low confidence.
- Ensure stored open sessions are not presented as guaranteed online truth.
- Ensure close reasons clearly identify close evidence, inferred/stale absence, stale timeout, or server boundary without expanding session truth.
- Do not add an automatic scheduler, timer, service, poller, daemon, or background GET-side mutation.

Acceptance criteria:

- Operators can explain what each session job does before pressing it.
- Operators understand that `Stored open` means "not closed by stored evidence yet", not "definitely online".
- Existing session rows remain readable without claiming false precision.
- Slice 3 does not add online/playtime/K-D/role/current faction truth and does not enable automatic session scheduling.

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

### Slice 5: Wrapper/Bootstrap Drift Recovery

Goal: make final smoke pass without relying on `ARMACTL_PYTHON` overrides.

Tasks:

- Audit the wrapper stale-stamp path and supported bootstrap flow.
- Decide whether production should run a supported interactive bootstrap, a service-safe repair command, or a narrow wrapper improvement.
- Add docs/tests if wrapper behavior changes.
- Do not hand-edit stamp files.

Acceptance criteria:

- Normal `./armactl --version` and read-only smoke commands pass on production VMs.
- If a VM cannot run normal wrapper non-interactively, the supported recovery path is documented and tested.

### Slice 6: Final VM Smoke Re-Run

Goal: confirm the fixes on current code.

Tasks:

- Fast-forward production checkouts only when explicitly approved.
- Restart only `armactl-web.service` unless a slice explicitly requires game service changes.
- Verify health, public status, service states, restart timer, jobs page state, players pages, and recent journals.
- Keep private hostnames and IPs out of public docs.

Acceptance criteria:

- Both production VMs are on current `feat/web-interface`.
- Web health/public status are stable.
- Player history timestamps are truthful.
- Session labels/jobs are understandable.
- No new tracebacks or warning spikes appear in recent web journals.

## Out Of Scope For This Plan

- Ban/kick/banlist mutations.
- Player IP storage.
- Current-session K/D, role, exact joined time, or exact faction truth unless a reliable source is explicitly added.
- Automatic player-session daemon/timer enablement.
- Discord player enrichment beyond existing safe roster/status publishing.
- Arbitrary file manager behavior, recursive deletes, raw log readers, or raw path/log-line exposure.
