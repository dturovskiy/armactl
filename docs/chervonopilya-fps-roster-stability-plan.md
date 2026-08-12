# Chervonopilya FPS And Current-Roster Stability Plan

Status: infrastructure investigation and production validation complete on 2026-08-12; mod-level remediation remains out of scope unless the exception spam recurs
Scope: Chervonopilya production infrastructure validation; exact mod-level root cause remains outside this plan unless the signature recurs
Safety rule: do not restart or mutate the game server while players are online unless an operator explicitly approves a maintenance action

## 1. Observed Symptoms

### FPS drops

Observed on 2026-07-10 during live read-only checks:

- The game server was running with players online.
- `runtime-settings.json` had `max_fps = 120`.
- `start-armareforger.sh` contained `-maxFPS 120`.
- Public status reported fresh telemetry with FPS values around `66.0`, `81.5`, `85.9`, `92.7`, and `107.0` while player count was around 7-9.
- Active game logs under `armactl-data/default/config/logs/logs_2026-07-10_06-00-19/` were very large:
  - `console.log`: about 444 MiB
  - `error.log`: about 440 MiB
  - `script.log`: about 404 MiB
- Recent log tail repeatedly showed:
  - `Virtual Machine Exception`
  - `Reason: Division by zero`
  - `Class: ACE_Medical_StableState`
  - `Function: UpdatePerfusion`
  - stack through `ACE_Medical_Breathing`, `ACE_Medical_Circulation`, and `ACE_FrameJobSystem`

Initial conclusion: the FPS drop is not explained by armactl web code or by the max-FPS profile. The strongest current signal is ACE Medical script exception spam inside the game update loop.

### Current roster flicker

Observed/confirmed during live read-only checks:

- Public status was healthy and reported 8-9 players.
- Discord stats preview rendered a stable named roster for the same moment.
- The VM had no separate `players current-cache run` process/service running.
- Running processes were:
  - `armactl-discord-stats.service`
  - `armactl-web.service`
  - `ArmaReforgerServer`
- Current roster cache code uses:
  - memory/persistent fresh TTL: 10 seconds
  - stale RCON roster fallback for RCON-unavailable live result: 120 seconds
  - live RCON fallback when cache is stale and no updater keeps it fresh

Initial conclusion: when the web roster briefly disappears while public player count remains nonzero, it is more likely a current-roster read/cache/RCON availability problem than real simultaneous player disconnects.

## 2. Non-Goals

- Do not restart the game server while players are online without explicit approval.
- Do not delete addon directories or profile data as a first response.
- Do not disable ACE Medical blindly.
- Do not treat A2S count-only data as named roster truth.
- Do not create synthetic player rows.
- Do not hide roster-source/freshness problems by showing fake stable data.
- Do not expand current-player stats or Discord enrichment in this plan.

## 3. Investigation Plan: ACE Medical FPS/Log Spam

### Slice A: Read-only evidence capture

Goal: capture enough evidence to distinguish a transient broken medical state from a persistent mod/config/version issue.

Checklist:

- [x] Record current mod list and ACE-related mod IDs/names from `config.json`.
- [x] Record disabled-mod sidecar state and verify no disabled ACE-related mod remains active in `game.mods`.
- [x] Capture bounded tails from `console.log`, `error.log`, and `script.log` without scanning entire 400+ MiB files.
- [x] Estimate exception rate from a bounded recent log window.
- [x] Capture current player count and FPS from public status at least 3 times over 2-5 minutes.
- [x] Determine persistence: the exception did not remain continuous after the supported restart, and no recurrence was present in the current logs.
- [x] Check profile/settings files for ACE Medical or stale module references.
- [x] Document exact ACE Medical stack frames and class/function names.

Acceptance criteria:

- We know whether the spam is continuous or bursty.
- We know whether it correlates with player count, a specific time window, or a persistent server state.
- We know whether active config/profile files contain ACE Medical settings that can be safely adjusted.

Closure evidence from 2026-08-12:

- The active config contains 101 mods and 14 ACE/Medical-named entries. The disabled sidecar contains `ACE Medical Breathing Dev` and `LVOAC Assault Rifle`; neither is present in active `game.mods`.
- Read-only diagnostics found zero active/disabled overlap, zero stale disabled-mod profile references, and no ACE Medical setting file under the bounded config/profile scan.
- The newest `console.log`, `error.log`, and `script.log` were each below 1.5 MiB. Full bounded reads of those active files found zero `ACE_Medical_StableState.UpdatePerfusion`, `Division by zero`, and `Virtual Machine Exception` occurrences.
- Twenty-one samples over five minutes reported fresh `ready` telemetry and FPS `120.0`; no exception recurrence was observed.
- Operational classification: the July failure was a transient ACE runtime/mod state cleared by the supported restart. A contribution from the now-disabled ACE Medical component cannot be isolated retrospectively. If the signature recurs, the fallback classification is an ACE/scenario integration bug requiring mod-owner investigation.

### Slice B: Root-cause classification

Classify into one of these buckets:

- **Transient runtime state:** one medical entity/player state is invalid and may clear on player death/disconnect/restart.
- **Profile/config residue:** stale or invalid profile setting causes the ACE subsystem to enter bad state.
- **Mod version bug:** ACE Medical version has a known division-by-zero issue under the current scenario/mod mix.
- **Scenario/mod integration bug:** mission or another mod feeds invalid values into ACE Medical.
- **Unknown:** not enough evidence; keep only mitigations that are reversible.

Acceptance criteria:

- The plan names one primary hypothesis and one fallback hypothesis.
- Any proposed live mitigation is reversible and has a rollback.

### Slice C: Safe mitigation design

Possible mitigations, ordered from least invasive to most invasive:

1. Wait for low/no-player window and restart once to clear transient state.
2. If stale profile setting is confirmed, use the existing profile cleanup pattern: backup first, edit only allowlisted settings, audit, mark restart pending.
3. If a mod update is available and safe, update ACE-related mods in the normal mod-update workflow.
4. If a specific ACE Medical feature can be disabled through a safe config/profile setting, prepare a reversible setting change.
5. Only as a last resort, temporarily remove/disable the problematic mod path during a planned maintenance window.

Acceptance criteria:

- Every mitigation has a backup or rollback.
- No destructive action is done while players are online.
- No broad profile/addon deletion is proposed.

### Slice D: Maintenance-window validation

After applying a chosen mitigation during a safe window:

- [x] Restart game server through the supported safe restart path.
- [x] Confirm `-maxFPS 120` remains in the generated start script.
- [x] Confirm public status returns `ready` and telemetry is fresh.
- [x] Confirm error/script/console tails no longer spam `ACE_Medical_StableState.UpdatePerfusion`.
- [x] Confirm FPS stabilizes close to expected values for current player count.
- [x] Keep rollback instructions ready if the server fails to become ready or spam continues.

## 4. Investigation Plan: Current Roster Flicker

### Slice R1: Confirm read issue vs real disconnects

Goal: prove whether players are actually leaving or whether named roster reads are intermittently unavailable.

Checklist:

- [x] Sample public `/public/server-status.json` count every 10-15 seconds for several minutes.
- [x] Sample named roster from the same source family, preferably Discord preview or a safe current-roster diagnostic, at the same cadence.
- [x] Compare these states:
  - A2S/public count remains > 0 but named roster disappears: read/cache/RCON issue.
  - A2S/public count and named roster both drop: possible real disconnect or server telemetry issue.
  - Named roster changes by a few names while count changes similarly: likely real player joins/leaves.
- [x] Check web journal around `/players/current.json` for controlled stale/unavailable states.
- [x] Check whether `armactl players current-cache run` is deployed/running anywhere for this instance.

Current evidence points to read/cache/RCON availability because public count and Discord preview remained healthy while the web roster had previously flickered.

### Slice R2: Stabilize source-of-truth flow

Options, in preferred order:

1. Enable or install the existing safe current-roster cache updater for Chervonopilya at the documented 60-second interval, if operators approve a background service.
2. Make the web current-roster page prefer acceptable stale named roster rows when live RCON is unavailable and A2S count still reports players, with an explicit stale label.
3. Add a read-only current-roster diagnostic block showing:
   - source
   - cache age
   - cache status
   - roster availability
   - count source
   - last refresh error, sanitized
4. Add tests for intermittent RCON unavailable transitions so the UI does not empty the roster without showing a clear stale/unavailable reason.

Acceptance criteria:

- Operators can distinguish `players left` from `roster source unavailable`.
- If named rows are stale, the UI says stale instead of pretending they are live.
- If only count is available, the UI does not fabricate names.
- No GET route writes `players.db` or creates sessions.

### Slice R2-a result: diagnostics and stale/unavailable clarity

Implemented locally on 2026-07-10 without changing the roster source of truth:

- `/players` and `/players/current.json` now expose source, cache status, cache age, fresh/stale state, roster availability, count source, observed count, and sanitized refresh error.
- A named RCON roster cache may be shown only when it is within the existing acceptable stale window. The rows are explicitly labeled `Stale roster` and the UI states that the live roster source is unavailable and the rows are not guaranteed live.
- A positive live count-only result keeps its `count_source` and `observed_count` while acceptable stale named rows are displayed.
- An A2S zero combined with unavailable RCON does not immediately erase a recent named roster: the effective count remains explicitly stale RCON evidence until the existing stale window expires. After expiry, the live zero clears the rows.
- Count-only states show the observed count and source without creating synthetic names.
- Named cache data older than the allowed stale age is rejected instead of being presented as live or acceptable stale roster data.
- Polling and server-rendered output use the same diagnostics and warning states. A single polling transport failure keeps the previously rendered rows and marks the live roster source unavailable instead of silently clearing the table.
- GET reads do not create or mutate `players.db` or player sessions. No current-stat, Discord/public-stat, daemon, service, game-server, deploy, or restart behavior was added.

Validation for this slice covers fresh memory cache, fresh persistent cache, acceptable stale named fallback, count-only output, expired stale rejection, polling static behavior, sanitization, and read-only GET behavior.

Operational decision from 2026-08-12: do not enable a separate current-cache updater while direct RCON reads remain stable. The updater is a performance/freshness optimization rather than session truth and is not required for correctness. Revisit only if roster-source flicker recurs. Web-only production validation is complete under Slice R3.

### Slice R3: Production validation

After any roster-flow change:

- [x] Confirm the roster-stabilization code is already deployed on Serhiivka and perform the closure smoke there before Chervonopilya.
- [x] Confirm the prior Chervonopilya deployment was web-only and did not restart the game for the roster change.
- [x] Watch authenticated `/players` and `/players/current.json`, then sample their roster backend, public status, and Discord source for at least 5-10 minutes.
- [x] Confirm no web traceback/500 in recent journal.
- [x] Confirm roster flicker is replaced by stable rows/empty state or explicit stale/unavailable status.

Production validation result from 2026-08-12:

- Authenticated list/current-roster smoke succeeded before a 21-sample, five-minute synchronized backend/public/Discord observation window.
- All samples agreed on player count, reported `rcon.roster`/`rcon` as the available live source, remained non-stale, and kept telemetry age between 0 and 9 seconds.
- The observation window had no players online, so the expected stable result was an empty named roster rather than fabricated rows. Unit coverage continues to prove controlled stale/unavailable transitions under simulated RCON failures.
- Web journal review found no traceback, HTTP 500, exception, or error during the window. `systemctl --failed` was empty.

## 5. Web/armactl Hardening Follow-ups

- Add an operator-visible log-spam warning for very large active `console/error/script` logs.
- Ensure player-log ingest never scans huge active logs unbounded; keep skip reasons counts-only and sanitized.
- Consider a safe diagnostic command for current roster cache state so operators do not need direct SQLite access.
- Document that A2S count, RCON roster rows, current-roster cache, Discord stats, and web current player table are related but not identical truth surfaces.

## 6. Immediate Recommendation

No additional restart or cache-updater deployment is recommended while the current state stays healthy. Keep the current web/game deployment and existing diagnostics. If the exact ACE exception signature returns, capture bounded evidence first and hand the mod-level root cause to the relevant mod/scenario owners; do not disable or remove additional mods as an infrastructure response. If roster flicker returns, use the existing source/cache/stale diagnostics before reconsidering the optional current-cache updater.

## 7. Evidence From 2026-07-10 Read-only Check

- Web deploy to Chervonopilya was web-only; `armareforger.service` remained active.
- Public status after deploy stayed healthy with player counts around 7-9 and fresh telemetry.
- Discord preview rendered the named roster twice with 8 named players.
- `players current-cache run` was not present as a process/service.
- Current roster cache behavior in code has a 10-second fresh TTL and a 120-second stale RCON-roster fallback.
- Active game log files were hundreds of MiB and actively updated.
- Bounded tails showed repeated ACE Medical `Division by zero` exceptions in `UpdatePerfusion`.

Interpretation:

- FPS issue: likely game/mod runtime issue, currently strongest signal is ACE Medical exception spam.
- Player disappearance: likely intermittent named-roster read/cache/RCON issue when public count remains nonzero; not proven to be real player disconnects.
