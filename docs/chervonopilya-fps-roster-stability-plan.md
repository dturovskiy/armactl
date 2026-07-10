# Chervonopilya FPS And Current-Roster Stability Plan

Status: draft diagnostic/remediation plan
Scope: Chervonopilya production VM only until the root causes are confirmed
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

- [ ] Record current mod list and ACE-related mod IDs/names from `config.json`.
- [ ] Record disabled-mod sidecar state and verify no disabled ACE-related mod remains active in `game.mods`.
- [ ] Capture bounded tails from `console.log`, `error.log`, and `script.log` without scanning entire 400+ MiB files.
- [ ] Estimate exception rate from a bounded recent log window.
- [ ] Capture current player count and FPS from public status at least 3 times over 2-5 minutes.
- [ ] Check whether the exception stops after affected players leave or remains continuous.
- [ ] Check profile/settings files for ACE Medical or stale module references.
- [ ] Document exact ACE Medical stack frames and class/function names.

Acceptance criteria:

- We know whether the spam is continuous or bursty.
- We know whether it correlates with player count, a specific time window, or a persistent server state.
- We know whether active config/profile files contain ACE Medical settings that can be safely adjusted.

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

- [ ] Restart game server through the supported safe restart path.
- [ ] Confirm `-maxFPS 120` remains in the generated start script.
- [ ] Confirm public status returns `ready` and telemetry is fresh.
- [ ] Confirm error/script/console tails no longer spam `ACE_Medical_StableState.UpdatePerfusion`.
- [ ] Confirm FPS stabilizes close to expected values for current player count.
- [ ] Keep rollback instructions ready if the server fails to become ready or spam continues.

## 4. Investigation Plan: Current Roster Flicker

### Slice R1: Confirm read issue vs real disconnects

Goal: prove whether players are actually leaving or whether named roster reads are intermittently unavailable.

Checklist:

- [ ] Sample public `/public/server-status.json` count every 10-15 seconds for several minutes.
- [ ] Sample named roster from the same source family, preferably Discord preview or a safe current-roster diagnostic, at the same cadence.
- [ ] Compare these states:
  - A2S/public count remains > 0 but named roster disappears: read/cache/RCON issue.
  - A2S/public count and named roster both drop: possible real disconnect or server telemetry issue.
  - Named roster changes by a few names while count changes similarly: likely real player joins/leaves.
- [ ] Check web journal around `/players/current.json` for controlled stale/unavailable states.
- [ ] Check whether `armactl players current-cache run` is deployed/running anywhere for this instance.

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

### Slice R3: Production validation

After any roster-flow change:

- [ ] Deploy to Serhiivka first if possible.
- [ ] Deploy to Chervonopilya web only; do not restart game.
- [ ] Watch `/players`, `/players/current.json`, public status, and Discord preview for at least 5-10 minutes.
- [ ] Confirm no web traceback/500 in recent journal.
- [ ] Confirm roster flicker is replaced by either stable cached rows or explicit stale/unavailable status.

## 5. Web/armactl Hardening Follow-ups

- Add an operator-visible log-spam warning for very large active `console/error/script` logs.
- Ensure player-log ingest never scans huge active logs unbounded; keep skip reasons counts-only and sanitized.
- Consider a safe diagnostic command for current roster cache state so operators do not need direct SQLite access.
- Document that A2S count, RCON roster rows, current-roster cache, Discord stats, and web current player table are related but not identical truth surfaces.

## 6. Immediate Recommendation

Do not restart Chervonopilya while players are online just to chase FPS. The likely FPS cause is ACE Medical exception spam, not max-FPS configuration or web deployment.

For the next safe action:

1. Keep the current web deploy.
2. Schedule a maintenance window or wait until no players are online.
3. During the window, capture bounded pre-restart log evidence, restart once, then check whether ACE Medical spam returns.
4. If it returns, proceed with ACE Medical config/mod-version investigation before disabling/removing anything.

For roster flicker, treat it as a current-roster source/freshness problem until proven otherwise. The next implementation slice should add explicit diagnostics and/or enable the existing safe cache updater rather than inventing another roster source.

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
