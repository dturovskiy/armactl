# Player Session Supervised Pipeline Contract

Status: **F3-b implementation complete locally and validated.** F3-c production enablement and acceptance remain pending.

This document is the source of truth for the supervised automatic player-session pipeline. It records the F3-a call-graph audit and the implemented F3-b correction for the enqueue-versus-execution gap, including ownership boundaries, durable state, failure behavior, generated units, and explicit lifecycle controls. The code change does not install or enable the timer, restart the game or web service, mutate a live instance, or claim F3-c production acceptance.

## Decision Summary

F3-a proved that the former command:

```text
armactl players sessions scheduler run --once
```

persisted or reused queued `web_jobs` rows and returned without proving execution or completion. F3-b replaces that automatic path with one synchronous ordered orchestrator that calls the shared typed mutation services directly, does not load the web job registry, does not start a web worker thread, and records success only after terminal durable work.

The manual POST action wrappers remain asynchronous web jobs for operator UX, but their handlers now call the same mutation services and contend on the same per-instance nonblocking `flock` as the automatic orchestrator. The legacy scheduler table remains read-only compatibility diagnostics and its enqueue timestamps are never imported as execution truth.

The generated oneshot/timer and explicit install/enable/disable/status commands exist, but the timer remains disabled and inactive after first install and is not installed or enabled by this implementation change. F3-c must still prove the production installation, enablement, observation window, and rollback path.

## Scope And Non-Negotiable Truth Rules

The supervised pipeline must preserve all existing player-truth rules:

- only normalized reliable player IDs can create or update sessions;
- A2S remains count-only and cannot create identified roster rows or sessions;
- name-only, mixed-unreliable, duplicate, or otherwise unreliable roster rows cannot create sessions or advance absence truth;
- live source failure, roster unavailability, or A2S-only fallback cannot close sessions or advance absence windows;
- inferred absence close requires repeated successful reliable RCON roster scans;
- accepted lifecycle/server-boundary evidence always splits sessions and blocks reconnect merge;
- reconnect within 10 minutes may merge only when all existing identity, close-reason, correlation, timing, and same-server-run gates pass;
- reconnect after the grace window creates a new play session;
- first evidence is not presented as exact joined time;
- missing or incomplete proof never becomes a fake zero;
- stats never cross play-session or server-run boundaries;
- `GET` routes never ingest logs, sessionize events, scan live roster truth, enqueue session work, run maintenance, or mutate `players.db`;
- no second parser, collector, scanner, sessionizer, SQL event pipeline, or stats pipeline is introduced.

## Current Architecture Map

```text
Existing automatic log path

systemd timer (120 s, completion-relative)
  -> armactl-player-log-ingest.service (Type=oneshot)
     -> armactl players log-ingest run --once --scheduled
        -> run_scheduled_player_log_ingest_once(...)
           -> run_player_log_ingest_once(...), synchronously
              -> shared instance flock
              -> allowlisted console-log discovery
              -> existing collector/parser
              -> player_registry ingest/checkpoints/freshness
              -> terminal typed result before process exit

Current F3-b session scheduler path

operator or generated systemd oneshot
  -> armactl players sessions scheduler run --once [--scheduled]
     -> run_player_session_scheduler_once(...), synchronously
        -> acquire shared per-instance nonblocking flock
        -> prove newer completed fresh ingest generation/high-water
        -> bounded event_id sessionization with monotonic trusted-time validation
        -> one exact reliable live RCON scan
        -> maintenance only when due
        -> durable terminal pipeline state
     -> process exits only after terminal result

Current manual web session action path

POST + auth/permission/CSRF
  -> request_player_*_job_and_start(...)
     -> audit intent
     -> ensure_player_*_job(...)
     -> if row was newly created: start_player_*_worker(...)
        -> threading.Thread(..., daemon=True)
           -> dispatch_job(...), synchronously inside that thread
              -> queued -> running + in-process lease heartbeat
              -> existing session handler/service
              -> succeeded/failed terminal row
     -> HTTP redirect

Read-only current stats path

GET /players or /players/current.json
  -> existing current-roster read model
  -> load_current_player_enrichment(...)
     -> open existing players.db in SQLite read-only/query-only mode
     -> prove reliable ID + open play session + server-run boundary
     -> prove fresh ingest checkpoint coverage from session opening
     -> aggregate stored stable events inside the proven window
     -> nullable result; no writes and no enqueue
```

## Call Graph Audit

The former scheduler subsection records the F3-a defect that F3-b replaced. The manual jobs, stored-log sessionizer, live scanner, maintenance, and read-only stats subsections describe the reusable paths retained by F3-b.

### Former Scheduler CLI (F3-a, Resolved)

```text
cli.py
  players sessions scheduler run --once
    -> player_session_scheduler_runner.run_player_session_scheduler_once(web.db)
       -> initialize_web_db(web.db)
       -> for each due policy:
          -> _ENQUEUE_BY_KIND[job_kind](web.db)
             -> ensure_player_live_session_scan_job(...)
             -> ensure_player_log_sessionization_job(...)
             -> ensure_player_session_maintenance_job(...)
          -> _record_success(...)
       -> return PlayerSessionSchedulerRunResult
```

There is no call from this graph to:

- `dispatch_job`;
- `start_player_live_session_scan_worker`;
- `start_player_log_sessionization_worker`;
- `start_player_session_maintenance_worker`;
- any handler;
- any terminal job-state wait;
- any stage-result read.

### Manual Session Jobs

```text
request_player_*_job_and_start(...)
  -> append intent audit
  -> ensure_player_*_job(...)
     -> get_or_create_active_job(...)
  -> only when created=True:
     -> start_player_*_worker(...)
        -> daemon thread
           -> dispatch_job(...)
              -> mark_job_running + worker token/lease
              -> handler(context)
              -> mark_job_succeeded / mark_job_failed
```

The manual wrappers are therefore request-and-start adapters, not a durable worker fleet. The persisted queue row alone has no executor guarantee.

### Stored-Log Sessionization

```text
handle_player_log_sessionization(...)
  -> player_sessionizer.sessionize_stored_player_log_events(players.db)
     -> list_player_log_events_for_sessionization(...)
     -> derive reliable observations / correlated closes / lifecycle boundaries
     -> player_registry.observe_player_session(...)
     -> player_registry.close_player_session(...)
     -> record lifecycle boundary and per-session checkpoint evidence
```

This path reads stored sanitized events only. It does not read live logs, RCON, A2S, or current stats.

### Live Roster Observation And Repeated Absence

```text
handle_player_live_session_scan(...)
  -> player_live_session_scanner.scan_live_player_sessions_once(...)
     -> player_sources.load_current_player_roster(...)
     -> retain only normalized reliable RCON rows
     -> observe_player_session(...) for present reliable IDs
     -> clear their pending absence windows
     -> only for a fully reliable successful RCON scan:
        -> record distinct absence scan for omitted open sessions
        -> after repeated confirmation:
           -> close_player_session(..., stale_absence)
```

A2S-only state, failed RCON, unavailable roster, unreliable rows, duplicate rows, and mismatched row/count evidence do not advance absence windows or close sessions.

### Maintenance

```text
handle_player_session_maintenance(...)
  -> close_stale_open_player_sessions(..., explicit 24 h cutoff)
  -> cleanup_player_sessions_by_retention(..., explicit 90 d cutoff)
```

Maintenance does not query live sources. It must therefore run only after the pipeline has proved that the current cycle's upstream evidence processing and live observation succeeded.

### Read-Only Current Stats

```text
load_current_player_enrichment(...)
  -> open players.db read-only + PRAGMA query_only
  -> require reliable current-roster ID
  -> require one proven open play session
  -> require matching server_run_key and no later lifecycle boundary
  -> require fresh ingest + checkpoint + coverage from session opening
  -> aggregate exact/derived-time events through freshness cutoff
```

The aggregator is not a pipeline mutation stage. It stays request-time, read-only, and nullable.

## Source-Of-Truth Ownership

| State or evidence | Current owner | Database/runtime | Contract |
| --- | --- | --- | --- |
| Log-ingest timer enablement, activation, and process result | systemd | generated ingest `.service`/`.timer` | Authoritative for whether supervised ingest is installed, enabled, running, or failed. |
| Log ingest execution | synchronous ingest service | oneshot process | Completes or fails before the service exits. |
| Parsed player-log evidence | player registry | instance `players.db` / `player_log_events` | Durable sanitized event evidence; no raw lines or raw paths. |
| Log checkpoints and freshness | player registry ingest service | instance `players.db` | Authoritative coverage and freshness truth. Time elapsed is never success truth. |
| Player identities, sessions, lifecycle boundaries, and live absence windows | player registry/session services | instance `players.db` | Authoritative player/session evidence. |
| Reconnect merge | `player_registry.observe_player_session` | instance `players.db` transaction | Existing 10-minute and identity/server-run gates remain authoritative. |
| Current stats values | read-only enrichment query | no materialized counters | Derived only from existing proven session and event evidence. |
| Manual session requests, progress, lease, and dedupe | web job store | `web.db` / `web_jobs` | Manual operator workflow ledger only; it is not automatic pipeline execution truth. |
| Legacy session scheduler due/backoff metadata | compatibility reader | `web.db` / scheduler state table | Historical enqueue outcomes only; F3-b never reads them as execution truth or updates them. |
| F3-b automatic pipeline state and sessionization cursor | synchronous orchestrator | instance `players.db` / one `player_session_pipeline_state` row | Selected sole automatic-control ledger. It advances only from completed synchronous stages and is independent of `web_jobs` and `armactl-web.service`. |
| Current worker liveness hint | in-process token plus `web_jobs` lease | web process memory + `web.db` | A fresh lease is only recent heartbeat metadata. It is not an OS-level durable worker guarantee. |
| Audit trail | audit service | append-only audit file | Supplemental operator evidence; not a substitute for DB/service completion state. |
| Journald output | systemd/journald | journal | Counts-only observability; not source-of-truth success. |
| Live roster identity truth | RCON roster path | live query | Reliable IDs only. A2S is count-only. |

### Competing Ledgers

F3-a found two independent ledgers for automatic intent: scheduler due/backoff rows and active `web_jobs` rows. Neither proved completed work, and an orphaned row could suppress later enqueue attempts.

F3-b resolves that conflict: `player_session_pipeline_state` is the only automatic-control ledger, `web_jobs` is manual workflow metadata only, and the legacy scheduler table is read-only diagnostics.

The selected F3-b ledger lives in the instance `players.db`, next to the event,
freshness, lifecycle, session, and absence evidence it coordinates. The legacy
`web_player_session_scheduler_state` rows remain read-only compatibility
diagnostics only: F3-b does not consult or update them, does not copy their
enqueue-based success timestamps into the new state, and must label them as
legacy enqueue metadata until a later explicit removal slice.

## F3-a Historical Findings

The following pre-fix findings explain the F3-b design. They are resolved by the synchronous orchestrator unless an item explicitly remains a later operational concern.

### P0 Implementation Gates

#### P0-1: Scheduler success is enqueue success, not execution success

The scheduler calls `ensure_*_job`, then immediately records scheduler success and a future due time. It never dispatches, starts, waits for, or verifies a worker. The command can exit zero with all work still queued.

#### P0-2: No component guarantees eventual pickup of scheduler-created queued jobs

There is no generic queue-draining worker in the web app, no app-start recovery loop, and no systemd worker. Explicit web POST wrappers start workers only for jobs they have just created. A scheduler-created queued row is not subsequently discovered and started by those wrappers because dedupe returns `created=False`.

#### P0-3: An orphaned queued row can permanently block its stage

Queued rows are considered active by dedupe. They have no worker lease, no age-based expiry, and no stale-queue recovery action. Duplicate maintenance keeps the oldest queued row. One orphaned queued row can therefore suppress all future scheduled and manual creation for that kind/instance until direct operator or code intervention changes it.

#### P0-4: Web-process exit can interrupt work without durable recovery

Session workers are daemon threads inside `armactl-web.service`. A stop, restart, crash, or process replacement can terminate them. A row may remain running until its lease expires, but expired-running recovery is manual metadata abandonment only. Restarting the web service does not resume or replay the job.

#### P0-5: Current asynchronous jobs do not enforce pipeline ordering

Sessionization, live scan, and maintenance are independent jobs. The scheduler can enqueue all due stages without waiting for any prior stage. Live scan or maintenance can therefore run despite failed or incomplete stored-log sessionization. That is unsafe when unprocessed lifecycle evidence could change server-run boundaries or when unprocessed disconnect evidence could change close/reconnect behavior.

### P1 Findings

#### P1-1: Scheduler backoff and freshness fields are based on queue insertion

`last_success_at`, `failure_count`, and `next_due_at` describe enqueue API behavior. They cannot tell an operator whether sessions were updated, whether the live source was reliable, or whether maintenance completed.

#### P1-2: Stored-log sessionization has no bounded global workload cursor

The current sessionizer reads all stored `player_log_events` in chronological order on every pass. Per-session checkpoint evidence makes writes idempotent, but it does not bound scan cost as event history grows. A supervised 120-second pipeline needs one restart-safe, bounded consumption cursor for newly ingested event IDs, with replay from a safe prior point when state is missing. Historical oversized-log backfill remains outside F3.

#### P1-3: Maintenance can make an inference without same-cycle upstream proof

The maintenance helper correctly uses explicit cutoffs and reliable last-seen evidence, but the current job topology allows it to run independently. In the supervised contract, stale close and retention must not run after sessionization failure or unreliable live-source failure in the same cycle.

#### P1-4: Existing five-minute sessionization cadence can lag the five-minute stats freshness gate

The current planning policy allows stored-log sessionization every five minutes while current stats reject ingest freshness older than five minutes. Normal scheduling jitter or one failed cycle can produce avoidable unavailable periods. F3-b should process stored-log and live stages on each eligible 120-second ingest generation.

### P2 Findings

#### P2-1: CLI and status terminology can imply stronger guarantees than exist

“Scheduler success,” “enqueued,” and “active” do not communicate that execution is unowned. Status documentation must explicitly distinguish enqueue metadata from completed stage execution until F3-b replaces the behavior.

#### P2-2: Current plans overstate active-job dedupe as a scheduler safety guarantee

Dedupe prevents duplicate active rows, but it does not ensure execution, completion, recovery, or ordering. Documentation must not present it as an execution guarantee.

## F3-a Pre-Fix Execution-Guarantee Answers

1. **Does the current scheduler execute work synchronously?** No.
2. **Does it start a worker?** No.
3. **Does it only persist queued metadata?** Yes, or reuse an already active queued/running row.
4. **Can the CLI/systemd caller exit while jobs remain queued?** Yes; this is the normal scheduler behavior.
5. **Who guarantees eventual pickup?** No current component.
6. **What if `armactl-web.service` is stopped?** Scheduler-created queued rows remain queued. They are not executed.
7. **What if the web service is restarted during a manual worker?** The daemon thread can terminate. The running row may later show an expired lease, but restart does not resume it.
8. **Can stale metadata block future work?** Yes. Running rows block until terminalized; expired running metadata requires an explicit operator POST to mark abandoned. Queued rows can block indefinitely and have no stale recovery path.
9. **Can elapsed time prove success?** No. Timeouts and cadence are bounded failure/trigger controls only.

## Required Stage Ordering

The automatic pipeline order is fixed:

```text
0. prove a completed fresh automatic log-ingest generation
1. sessionize all stored events belonging to that generation
2. perform one reliable live-roster observation
3. run maintenance only when due
4. publish terminal pipeline status
```

Dependencies:

- Stage 1 may run only after Stage 0 proves a completed fresh ingest generation.
- Stage 2 may run only after Stage 1 reached the generation high-water successfully.
- Stage 3 may run only after Stages 1 and 2 succeeded in the same cycle.
- Current stats aggregation is not run by this pipeline. It remains a read-only consumer after the stored evidence is committed.

For downstream gating, `LivePlayerSessionScanSummary.success` alone is not
sufficient: the existing scanner can safely return `success=True` for A2S-only
or mixed/unreliable observations because it performed no unsafe close. F3-b must
centralize an explicit `reliable_roster_scan` verdict. The live stage is fully
reliable only when all of these hold:

- `success` is true;
- `source_failures == 0` and `roster_unavailable == 0`;
- `scans_considered == 1`;
- `unreliable_rows_ignored == 0`;
- `duplicate_rows_ignored == 0`.

A fully reliable empty RCON roster may satisfy this predicate. A2S count-only,
mixed/unreliable rows, duplicate rows, count mismatch, unavailable RCON, or
source failure may still produce a controlled scanner summary, but they do not
permit maintenance or count as full-cycle success. The verdict must be one
shared service helper used by the orchestrator and tests, not a duplicated CLI
heuristic.

Reasoning:

- stored lifecycle or disconnect evidence must be applied before a live observation can decide whether to update, reopen, or create a session;
- a live reliable observation must precede stale maintenance so maintenance does not close a player whose current presence was not checked successfully;
- retention follows mutation stages so it cannot remove closed rows while an earlier stage is still resolving their current state;
- no stage infers prior-stage success merely because an interval passed.

## Exactly One Recommended F3-b Architecture

### Topology

Add one generated, instance-scoped supervised pair:

```text
armactl-player-session-pipeline.service
armactl-player-session-pipeline.timer
```

For non-default instances, follow the existing generated unit naming pattern.

The service is `Type=oneshot` and calls:

```text
armactl players sessions scheduler run --once --scheduled
```

F3-b changes that runner from enqueue-only behavior into a synchronous supervised orchestrator. The CLI does not create `web_jobs`, start a web thread, or depend on `armactl-web.service`.

The orchestrator directly reuses the existing service functions:

- log-ingest status/freshness reader;
- stored-log sessionizer;
- live session scanner;
- maintenance helpers;
- player registry transactions and reconnect logic;
- existing counts-only formatting and audit sanitization patterns.

No parser, scanner, sessionizer, SQL event model, reconnect implementation, or stats aggregator is duplicated.

The current maintenance mutation helper is private job-layer code, and the
current sessionizer has no bounded page contract. F3-b must first extract a
public typed maintenance service and extend the existing sessionizer/registry
query APIs with bounded inputs. Both the manual job handlers and the automatic
orchestrator then call those same public services. The automatic path must not
call private job handlers, construct fake `JobContext` objects, or copy their
mutation logic.

### Automatic State Placement And Migration

F3-b applies one idempotent next-version `players.db` migration: add the
successful-ingest `generation_max_event_id` metadata and add one
`player_session_pipeline_state` table. That table is the only automatic pipeline
ledger and contains one bounded row for the instance/scope. The systemd unit
state remains authoritative only for process supervision; `web_jobs` remains
authoritative only for manual job UX.

Migration rules:

- mutating pipeline execution may apply the idempotent `players.db` migration;
- read-only status and every `GET` path must not create or migrate the database;
- missing state means conservative catch-up from stored evidence, never
  “already consumed”;
- legacy `web_player_session_scheduler_state` enqueue timestamps are never
  promoted to completion truth;
- deleting or recreating `web.db` cannot reset or advance automatic session
  consumption;
- loss or corruption of the pipeline row fails closed and requires
  conservative replay or operator-visible recovery.

### Why Existing Jobs Must Not Remain The Automatic Execution Path

The existing job store and worker runner are designed for explicit web actions and daemon-thread execution. Making the automatic pipeline depend on them would require a durable queue consumer, worker ownership, shutdown/restart recovery, stale-queue policy, ordering barriers, and terminal-result coordination. That is materially broader than the required correction.

The narrowest reuse-based fix is synchronous orchestration. Existing jobs remain available for manual operator actions, use their existing audit/job UI, and share the same pipeline lock to prevent overlap.

### Timer Cadence And Offset

- Keep the existing log-ingest timer unchanged at 120 seconds, completion-relative.
- Configure the player-session pipeline timer with an initial 30-second offset after the nominal ingest trigger, then a 120-second completion-relative interval.
- A concrete generated timer contract is:

```text
OnActiveSec=150s
OnUnitInactiveSec=120s
AccuracySec=1s
RandomizedDelaySec=0
```

The offset is only load smoothing. Independent completion-relative timers can drift, so correctness never depends on the 30 seconds.

The real dependency gate is the stored ingest generation:

- ingest status is `fresh`;
- a scanned checkpoint exists;
- `last_success_at` and coverage metadata are valid;
- the current ingest success token is newer than the pipeline's last fully consumed token;
- that successful freshness row contains its persisted event-ID high-water;
- the sessionizer reaches the current generation high-water.

The service performs one readiness read per invocation. No new completed ingest generation means a controlled `not_due`/`waiting_for_ingest` result with no session mutation; it does not sleep, poll, or infer success from elapsed time. A currently running ingest service is observed only through explicit systemd/freshness state and is retried on the next timer cycle.

### Bounded Work And Runtime Guards

The first F3-b contract uses:

- systemd `TimeoutStartSec=240s` for the session pipeline oneshot;
- one immediate ingest-readiness evaluation per invocation, with no sleep/poll loop;
- bounded stored-event batches, initially at most 5,000 newly ingested event rows per pass;
- existing bounded RCON/source query timeouts for the live scan;
- existing bounded maintenance batches;
- restrained CPU/I/O priority and `UMask=0077`, matching the established ingest service pattern.

The hard service timeout is the outer failure guard, not success truth. F3-b does not add independent wall-clock success timers around Python mutation stages; stage order, bounded event counts, source query timeouts, and terminal results determine progress.

A batch that does not reach the current ingest generation high-water records `backlog_remaining=true`, does not advance the consumed-generation token, and skips live scan and maintenance. The next cycle resumes from the single persisted sessionization cursor. This prevents an unprocessed lifecycle boundary from being overtaken by live or maintenance work.

The cursor is automatic pipeline control state, not a second event truth source.
`player_log_events` remains the evidence source, and existing registry/session
idempotence remains the replay guard. If cursor state is missing or invalid, the
pipeline replays conservatively from stored event ID zero rather than guessing
success or initializing at the current maximum.

F3-b extends successful ingest freshness metadata with
`generation_max_event_id`. The ingest service computes and stores that value in
the same final transaction that publishes the successful freshness generation,
after all event writes for the run are complete. A partial/failed run does not
publish a new successful generation high-water.

At readiness, the orchestrator reads one persisted generation tuple from
`players.db`: ingest `last_success_at` plus its stored
`generation_max_event_id`. It must not combine a previously stored success time
with a separately queried current `MAX(player_log_events.event_id)`: a later
ingest may already have committed new events while its final freshness result is
still pending. The persisted tuple makes those events part of the next
generation instead of leaking them into the old one.

A generation owns the fixed event-ID range after the previously consumed
generation maximum and through its persisted generation maximum.

Before mutating a bounded page, F3-b must validate that trusted event occurrence
times are nondecreasing in `event_id` order, both against the persisted last
sessionized occurrence time and within the complete page. Only a validated page
is processed in ascending `event_id` order; after this gate, that order is
equivalent to the existing stable occurrence-time order, with event ID as the
tie-breaker. The page cursor advances only after the complete page commits.

The state therefore stores the current generation maximum, the last committed
sessionized event ID, the last committed trusted occurrence time, and the last
fully consumed generation maximum. A late/historical event whose trusted time
would move backward stops before applying the offending page with bounded
`historical_backfill_required` state. It is never silently skipped, reordered
across an already committed page, or treated as consumed. Historical oversized
log backfill and any explicit replay workflow remain outside F3.

### Locking And Dedupe Ownership

Introduce one per-instance process lock, for example:

```text
.player-session-pipeline.lock
```

Contract:

- nonblocking exclusive `flock`;
- held for the complete ordered pipeline cycle;
- shared by the automatic orchestrator and all manual session job handlers;
- released by the kernel on process death;
- a leftover lock file has no ownership meaning;
- lock contention records a controlled busy/not-run result and does not advance success or due state.

Ownership after F3-b:

- the flock owns cross-process overlap prevention;
- the systemd oneshot/timer owns automatic instance scheduling and self-overlap prevention;
- the synchronous orchestrator owns stage order and completion;
- `web_jobs` active dedupe remains manual request dedupe only;
- player-registry transactions, unique constraints, event dedupe, session checkpoints, and absence timestamp checks own data-level idempotence.

The automatic pipeline never waits on, clears, abandons, or treats a `web_jobs` row as completion truth. Thus stale queued/running manual metadata cannot block automatic session truth.

### Failure Semantics

- Ingest readiness failure: run no session stage.
- Sessionization exception, timeout, DB lock, or incomplete backlog: skip live scan and maintenance.
- Live source failure, roster unavailable, unreliable/mixed roster, or timeout: preserve the scanner's no-close semantics and skip maintenance.
- Maintenance failure: keep already committed sessionization/live observations; keep maintenance due and retry later.
- Audit/status persistence failure after a data-stage commit: report nonzero/unknown completion and replay idempotently. Do not manufacture success.
- Process crash or service timeout: kernel releases the lock; status records or infers interruption; next cycle retries from durable evidence/checkpoints.
- Partial stage commits are not rolled back across service boundaries. Existing transactions and idempotence make completed writes replayable.

### Scheduler State Semantics

F3-b must redefine automatic scheduler/pipeline state so fields mean actual execution:

- `last_attempt_at`: orchestrator acquired the pipeline lock and began readiness evaluation;
- `last_started_at`: first mutation stage began;
- `last_completed_at`: all required stages for the cycle returned a terminal result;
- `last_success_at`: all required stages succeeded and the ingest generation was fully consumed;
- `last_failure_at`: a required stage failed, timed out, or status/audit completion could not be persisted;
- `last_failure_stage`: readiness, sessionization, live_scan, maintenance, status, or audit;
- `last_failure_code`: bounded safe code only;
- `consecutive_failures`: actual failed cycles, not enqueue failures;
- `last_consumed_ingest_success_at`: exact stored ingest generation token consumed only after full cycle success;
- `last_consumed_ingest_generation_max_event_id`: matching persisted ingest high-water;
- `current_generation_max_event_id`: fixed high-water for the in-progress generation;
- `last_sessionized_event_id`: last completely committed bounded page cursor;
- `last_sessionized_event_time`: trusted occurrence-time monotonicity gate;
- `next_maintenance_due_at`: maintenance policy state;
- `interrupted`: last start has no matching terminal completion.

No field can be updated to success merely because a job row was created or time elapsed.

### Readiness And Status Surface

`players sessions scheduler status` remains read-only and must not create/migrate `players.db`, enqueue work, start a worker, or repair metadata.

The F3-b status exposes:

- service/timer installed, enabled, active, and their bounded systemd status metadata;
- configured cadence, initial offset, and runtime guard;
- lock state as idle/busy/unknown, without PID or path output;
- current ingest freshness status and completed generation high-water;
- last attempt/start/completion/success/failure timestamps;
- interrupted state;
- consecutive failure count;
- last failure stage and bounded reason code;
- last consumed ingest generation timestamp;
- current and consumed sessionization high-water;
- next maintenance due;
- overall pipeline state: empty, waiting_for_ingest, available, busy, degraded, failed, or healthy.

Status must not claim that current-player stats are available. Stats readiness remains per-player and read-only because coverage can differ from each session opening.

### Journald And Audit Output

Each scheduled run emits one bounded summary plus optional per-stage counts:

- terminal outcome, failure stage, and bounded reason code;
- event/session/roster/absence/retention counts;
- generation, backlog, lock-busy, source reliability, and timeout outcome codes.

Output must not contain:

- player names;
- reliable IDs or other player IDs;
- raw RCON rows;
- raw log lines;
- raw paths or source references;
- IP addresses;
- secrets, tokens, cookies, webhook URLs, or credentials.

Routine successful unchanged/not-due cycles do not write durable audit entries. F3-b journald output is counts-only; any future durable transition-audit integration must preserve the same bounded privacy contract.

### Install, Enable, Disable, And Status Contract

Install:

- generate and install only the session pipeline service/timer through the existing service-manager/template ownership model;
- run daemon reload as required;
- leave the timer disabled and inactive by default;
- preserve existing enablement on reinstall;
- do not start the game service, web service, ingest service, or session service;
- do not create session work from install.

Enable:

- explicit operator action only;
- verify that the existing player-log ingest timer is installed and enabled and that stored ingest status is not failed;
- fail closed with safe guidance if ingest supervision is not ready;
- do not silently enable the ingest timer;
- enable/start only the player-session pipeline timer;
- no game or web restart.

Disable:

- stop and disable only the session pipeline timer;
- do not stop ingest, game, or web services;
- do not delete sessions, jobs, status, checkpoints, or audit history.

Status:

- read-only and controlled for missing units or missing `players.db`; legacy
  `web.db` scheduler metadata is optional compatibility diagnostics only;
- no hidden repair, enqueue, migration, or trigger.

### Forbidden Triggers

The automatic pipeline must not be started by:

- app startup;
- a web background thread;
- browser load or polling;
- JavaScript;
- any `GET` route;
- current stats aggregation;
- current-roster cache refresh;
- an implicit side effect of install;
- a game or web service restart.

## Concurrency And Recovery Contract

### One Instance

Only one session pipeline cycle per instance may hold the pipeline lock. Different instances may run independently because their databases, units, and lock scopes are instance-specific.

### Manual Versus Automatic Work

Manual session jobs remain operator-visible `web_jobs`, but their handlers must acquire the same pipeline lock before mutation. If the automatic cycle owns it, a manual job terminates with a safe busy/not-run result. If a manual handler owns it, the automatic cycle records busy and retries on its next timer cycle.

Manual web job leases do not own the automatic lock and cannot prove automatic liveness.

### Crash Recovery

- flock ownership ends on process death;
- SQLite transactions either commit or roll back at their transaction boundary;
- event dedupe and session checkpoints make stored-log replay idempotent;
- one-open-session constraints prevent duplicate open rows;
- lifecycle boundary inserts are idempotent;
- repeated absence timestamps do not advance the absence counter twice;
- maintenance operates in bounded batches and retries remaining eligible rows;
- automatic state records interruption and never converts it to success based on age;
- operator-visible stale manual job metadata is retained and recovered only through explicit manual controls.

### Retryability

Retryable:

- temporary DB lock/contention;
- unavailable or failed live source;
- incomplete sessionization batch/backlog;
- audit/status write failure;
- service interruption or timeout;
- transient filesystem/systemd status failure.

Operator-visible and not silently repaired:

- repeated service timeout;
- malformed/missing pipeline state;
- persistent ingest partial/failed state;
- recurring source unreliability;
- corrupt databases or schema mismatch;
- stale manual queued/running job metadata;
- any privacy/redaction violation;
- event backlog that cannot reach the generation high-water within repeated bounded cycles.

## Failure And Recovery Matrix

| Condition | Current behavior | Required F3-b behavior | Later stages | Recovery |
| --- | --- | --- | --- | --- |
| No newer completed ingest generation | Scheduler may enqueue based on time | Controlled waiting/not-due; no mutation | None | Next timer cycle after explicit ingest completion. |
| Ingest failed, partial, malformed, or checkpoint-free | Session jobs can still be enqueued | Fail readiness gate | None | Ingest service recovers and records a new fresh generation. |
| Sessionization exception/DB error | Independent jobs may still run | Fail cycle and preserve bounded reason | Skip live and maintenance | Retry idempotently next cycle. |
| Sessionization backlog remains | Not represented | Record backlog; do not consume generation | Skip live and maintenance | Resume bounded cursor next cycle. |
| Lifecycle boundary committed, then process crashes | Later jobs may or may not run | Mark interrupted/nonzero | No guaranteed later stage | Replay; boundary insert and closes remain idempotent. |
| RCON/source unavailable | Scanner returns safe failure, but maintenance can run separately | Preserve no-close scanner behavior; fail/degrade cycle | Skip maintenance | Retry live stage next eligible cycle. |
| A2S count only | Cannot form reliable roster | Counts-only observation; no session mutation | Skip maintenance for this cycle | Wait for reliable RCON roster. |
| Mixed unreliable or duplicate RCON rows | Scanner refuses reliable absence semantics | No absence advancement or close | Skip maintenance | Retry after clean reliable scan. |
| First reliable absence | Absence window count advances | Same | Maintenance may run only if full live stage succeeded | Second distinct reliable absence required. |
| Second reliable absence | May close `stale_absence` | Same conservative close | Maintenance may follow | Reconnect gates decide merge/new session later. |
| Maintenance failure | Independent job failure | Keep earlier committed stages; cycle fails | No further mutation | Keep maintenance due and retry. |
| Pipeline lock busy | No shared session-pipeline lock today | Controlled busy/not-run, no success advance | None | Next timer/manual retry. |
| Process crash/service timeout | Queue/thread metadata may remain | Kernel releases lock; mark/infer interrupted | None | Restart-safe replay. |
| Web service stopped/restarted | Queued jobs not consumed; workers can die | Automatic pipeline unaffected | Ordered oneshot continues independently | Manual job metadata remains operator-visible. |
| Orphaned queued manual job | Can block all later jobs of same kind | May block only manual job creation, never automatic pipeline | Automatic stages continue | Separate explicit manual metadata recovery design. |
| Expired running manual job | Manual mark-abandoned available | Same manual-only recovery | Automatic stages continue | Explicit audited POST; no fake cancellation. |
| Audit/status write fails after data commit | Current handlers may report failure | Nonzero/unknown terminal state; do not claim success | Stop cycle | Replay idempotently and recover observability. |
| Raw/sensitive output detected | Contract violation | Stop rollout | None | Disable timer and remediate before resuming. |

## Cadence And Ordering Contract

| Stage | Trigger/cadence | Dependency | Completion truth | Failure effect |
| --- | --- | --- | --- | --- |
| Automatic player-log ingest | Existing 120 s completion-relative timer | None within F3 | Synchronous ingest result plus persisted fresh checkpoint generation | No session pipeline mutation until a newer fresh generation exists. |
| Stored-log sessionization | Every newly completed ingest generation, nominally every 120 s | Fresh ingest gate and pipeline lock | Sessionizer reaches generation event high-water within bounded batch policy | Skip live scan and maintenance. |
| Reliable live-roster scan | After successful sessionization in same cycle | Sessionization complete | Scanner returns successful reliable semantics; source failures are not success | Skip maintenance; no absence close from failure. |
| Repeated-absence close | During successful live scans | Two distinct reliable RCON absence observations by default | Confirmed absence window and committed session close | No close on failed/unreliable scans. |
| Maintenance | Hourly when due, after successful live scan | Same-cycle sessionization and live success | Bounded stale-close and retention summaries committed | Keep due and retry; prior stages remain. |
| Current-player stats aggregation | Authenticated read request only | Existing proven session + fresh coverage | Read-only scoped query result | Nullable/unavailable; never triggers mutation. |

## Security And Privacy Constraints

- Run as the resolved armactl instance owner, not root.
- Use direct project `.venv` execution and the existing project/runtime containment model.
- Use `UMask=0077`, `NoNewPrivileges=true`, private temporary storage, and restrained CPU/I/O priority consistent with the ingest service.
- Keep database and lock files inside the established instance data root.
- Never store or render IP addresses, raw RCON output, raw log lines, raw paths, secrets, public player IDs, or unbounded exception text.
- Sanitize all reason codes and output fields.
- Journal and audit payloads are counts-only.
- `GET` remains read-only.
- No browser, JS, app-start, or hidden-thread trigger.
- No game or web restart requirement.
- No automatic destructive recovery.

## F3-b Implementation Acceptance

Before any production rollout, focused tests must prove at least:

- `scheduler run --once` executes the ordered services synchronously and creates
  no automatic `web_jobs` rows;
- the process exits success only after every required stage and state write
  completes;
- legacy `web_player_session_scheduler_state` enqueue timestamps are ignored as
  execution truth;
- automatic state/cursor ownership is solely the instance
  `player_session_pipeline_state` row;
- a successful ingest freshness generation persists its event high-water, and a
  concurrently starting later ingest cannot leak events into the prior
  generation;
- read-only status with missing `players.db` or legacy/missing `web.db` creates
  and migrates nothing;
- one shared flock prevents automatic/manual overlap and releases on process
  death;
- bounded backlog resumes from the last committed page and blocks live scan and
  maintenance until the generation high-water is reached;
- out-of-order/late trusted evidence stops before the offending page and does
  not advance the cursor or publish success;
- A2S-only, count mismatch, duplicate, mixed/unreliable, unavailable, and failed
  roster summaries do not satisfy `reliable_roster_scan` and skip maintenance;
- a fully reliable empty RCON roster can satisfy the live-stage gate without
  fabricating players;
- maintenance remains due after failure and prior committed stages replay
  idempotently;
- install is idempotent, disabled by default, preserves existing enablement, and
  does not restart or start game/web/session work;
- status, CLI, and journald output remain counts-only and sanitized;
- existing manual POST jobs still use the same mutation services and shared lock;
- full project tests pass because scheduler semantics, registry schema, manual
  jobs, CLI, generated units, and read-only player routes are shared behavior.

## F3-c Production Rollout Plan

F3-c is not complete until the implemented F3-b contract passes staged production observation.

### Stage 1: Serhiivka Only

1. Confirm F3-b tests and local service rendering/status checks pass.
2. Fast-forward/deploy only with separate explicit approval.
3. Install the player-session service/timer disabled.
4. Verify install did not enable or start it and did not change game/web processes.
5. Confirm the existing automatic player-log ingest timer is enabled, healthy, and producing fresh generations.
6. Explicitly enable only the player-session timer.
7. Observe multiple automatic cycles with counts-only journal/status output.
8. Run the real-player acceptance scenarios below without using manual log or session job buttons.
9. Disable and stop rollout immediately on a stop condition.

Do not restart a game server merely to manufacture a lifecycle boundary while players are present. Use a naturally occurring approved lifecycle boundary or a separate maintenance window with no player-impact conflict.

### Stage 2: Chervonopilya Only After Approval

Proceed only after Serhiivka has passed every required scenario, evidence has been reviewed, and explicit approval is given. Repeat install-disabled verification, explicit enablement, cycle observation, privacy review, and all applicable acceptance scenarios. Do not infer acceptance from Serhiivka alone.

## Explicit Production Acceptance Checklist

### Supervision And Execution

- [ ] Session service/timer install is disabled by default.
- [ ] Explicit enable starts only the player-session timer.
- [ ] No game or web restart is required.
- [ ] A scheduled invocation executes session stages synchronously and exits only after a terminal result.
- [ ] `last_success_at` corresponds to completed stage work, not enqueue.
- [ ] Stopping/restarting `armactl-web.service` does not prevent or interrupt the automatic pipeline.
- [ ] Stale manual queued/running metadata does not block automatic execution.
- [ ] No overlapping automatic/manual session mutation occurs.

### Player And Session Truth

- [ ] A reliable player entering is observed automatically and creates/updates the correct session.
- [ ] Name-only or unreliable rows create no session.
- [ ] A2S remains count-only.
- [ ] Faction selection appears automatically as last-known in-session evidence.
- [ ] Kill, death, and teamkill evidence appears automatically within the proven current play-session window.
- [ ] Disconnect evidence closes the connection span when correlation is unambiguous.
- [ ] Source failure does not close a session or advance an absence window.
- [ ] One reliable absence scan does not close the session.
- [ ] Repeated reliable RCON absence closes the connection span conservatively.
- [ ] Reconnect within 10 minutes continues the same play session only when all existing gates pass.
- [ ] Reconnect after the grace window creates a new play session.
- [ ] A lifecycle boundary always creates a new play session and prevents cross-boundary merge.
- [ ] Stats reset or continue exactly with the resulting play-session/server-run boundary.
- [ ] No manual “Update events from logs,” “Sessionize logs,” “Scan live sessions,” or “Session maintenance” button is required.

### Freshness, Idempotence, And Privacy

- [ ] Missing, stale, partial, failed, or incomplete coverage produces nullable values, not fake zeroes.
- [ ] Coverage that starts after session opening does not claim current stats.
- [ ] Repeated cycles create no duplicate events, sessions, reconnect merges, absence confirmations, or stat counts.
- [ ] Sessionization backlog blocks downstream stages until the generation is fully processed.
- [ ] Journald, CLI, status, and audit output contain counts and safe reason codes only.
- [ ] No raw paths, raw lines, raw RCON rows, names, IDs, IPs, source refs, or secrets appear in scheduled output.
- [ ] `GET` routes remain read-only and do not trigger any pipeline stage.

### Rollout Sequence

- [ ] Serhiivka passes first.
- [ ] Serhiivka evidence is reviewed before Chervonopilya approval.
- [ ] Chervonopilya is enabled only after explicit approval.
- [ ] No game server with active players is restarted merely for acceptance.

## Stop Conditions

Stop F3-b implementation or F3-c rollout and leave/return the timer disabled when any of these occurs:

- the automatic path still only enqueues `web_jobs` or depends on an in-process web worker;
- a timer/service reports success before all required stages complete;
- a timeout, age, or cadence interval is treated as success truth;
- stage ordering can be bypassed;
- sessionization backlog is overtaken by live scan or maintenance;
- source failure, A2S-only state, or unreliable roster evidence advances absence or closes a session;
- lifecycle evidence is crossed by reconnect merge or stats;
- duplicate events, sessions, merges, closes, or stats appear;
- unavailable proof renders zero;
- manual and automatic mutation overlap;
- stale manual job metadata blocks automatic work;
- scheduled output leaks a raw path, line, RCON row, name, ID, IP, source ref, secret, or token;
- a `GET`, browser poll, JavaScript path, app startup, or background thread starts mutation;
- installation enables the timer implicitly;
- game or web restart is required;
- repeated service timeouts or unbounded event backlog occur;
- either production host shows player-impacting behavior or unexplained session/stat changes.

## Out Of Scope

- F3-c production installation, enablement, observation, or acceptance;
- Discord or public combat-stat enrichment;
- historical oversized-log backfill;
- role truth or loadout parsing;
- K/D column;
- ban, kick, or banlist behavior;
- public player IDs;
- IP, raw log, raw RCON, raw source-reference, or raw path storage/output;
- automatic destructive recovery;
- live worker cancellation or process termination;
- game or web service restart;
- production changes;
- richer session detail/UI/API unrelated to supervision acceptance.

## Work Status

- [x] **F3-a:** trace real execution paths, answer enqueue-versus-execution, assign source-of-truth ownership, define ordering/recovery, and select one supervised architecture.
- [x] **F3-b:** implement and locally validate the synchronous supervised player-session service/timer, atomic ingest generations, bounded cursor, exact live-roster gate, shared mutation lock, and disabled-by-default lifecycle controls.
- [ ] **F3-c:** complete staged Serhiivka-first, approval-gated Chervonopilya production acceptance.
