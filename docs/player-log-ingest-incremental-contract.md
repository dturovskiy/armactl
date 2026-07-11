# Player Log Incremental Ingest Contract

This document defines Slice F2-c, the bounded incremental-ingest contract needed
before the supervised player-log timer can be accepted on a busy server.

## Production Finding

F2-b validation proved the systemd oneshot/timer foundation on the quiet VM. The
busy VM exposed a separate data-path blocker before timer installation: the
newest live `console.log` was already larger than the collector's 8 MiB
whole-file limit. The previous behavior skipped that active file, left ingest
freshness `partial`, and therefore correctly blocked session-scoped statistics.
Raising the whole-file limit would only postpone the failure and increase I/O.

## Ownership And Reuse

- `player_log_collector.py` remains the only line reader and event parser.
- `player_log_ingest.py` owns allowlisted discovery, active-log selection,
  checkpoints, bounded scan planning, freshness, and the process lock.
- `player_registry.py` owns checkpoint/freshness persistence and migration.
- `player_current_enrichment.py` remains read-only and may expose statistics only
  when stored coverage spans the proven play-session opening.
- Manual job, foreground CLI, and systemd timer continue to call the same
  synchronous ingest service. No second parser, SQL pipeline, or daemon is added.

## Scan Contract

1. Logs remain fixed to allowlisted instance `config/logs/*/console.log` files.
2. The newest regular allowlisted log is the active source.
3. A small new file is scanned from byte zero.
4. An oversized active file bootstraps from at most the final 8 MiB. The first
   partial line is discarded and source references use sanitized absolute byte
   offsets so overlapping reads deduplicate consistently.
5. Later passes start from the persisted next byte offset and read only appended
   complete lines. An incomplete final line is retained for the next pass.
6. If append growth exceeds the byte bound, coverage is reset to the bounded
   tail rather than pretending the skipped gap was observed.
7. File identity, shrinkage, and content replacement detect rotation/truncation
   before choosing an offset. A larger replacement must not be treated as an
   append.
8. Oversized historical logs are counted and skipped for current freshness.
   They do not make the current active source partial. Historical backfill is a
   separate explicit future operation.
9. The line limit remains a partial state until subsequent passes catch up.
10. Raw paths, raw lines, IPs, tokens, and secrets do not enter status, job
    output, audit details, or browser DTOs.

## Time And Coverage

- Checkpoints persist the next offset, parser date/time rollover state, opaque
  file identity, and earliest continuously covered event timestamp.
- Tail bootstrap derives its date from absolute evidence when present; otherwise
  it combines bounded time-of-day evidence with UTC file observation time and
  carries the resulting rollover state into later append scans.
- `player_log_ingest_freshness.coverage_started_at` describes the earliest time
  continuously covered by the active source.
- `fresh` means the active source reached its current bounded end without an
  unprocessed line-limit gap and has trustworthy coverage time. It does not mean
  historical logs were fully backfilled.
- Current-player statistics remain unavailable when a play session opened before
  `coverage_started_at`. A true zero is allowed only after coverage starts at or
  before the proven session opening and reaches the fresh cutoff.

## Failure And Recovery

- Missing active source, invalid timestamp coverage, decode/binary/read failure,
  unresolved line backlog, or storage failure cannot produce fresh statistics.
- A held incomplete final line is not an error and is retried from the same byte
  offset.
- Rotation, truncation, and an oversized append gap reset parser/coverage state.
- Existing v9/v10 metadata remains readable without mutation from status/GET
  paths. The next mutating ingest performs the idempotent v11 migration.
- Lock contention remains a controlled skipped cycle; no overlapping writer is
  introduced.

## Acceptance Checklist

- [x] Bounded active-log tail bootstrap without raising the whole-file cap.
- [x] Incremental append reads with stable byte references and no duplicate count.
- [x] Incomplete-line continuation and line-limit catch-up behavior.
- [x] Rotation/truncation detection including a larger replacement file.
- [x] Non-blocking counts for oversized historical logs.
- [x] Persisted v11 offset, parser-state, file-identity, and coverage metadata.
- [x] Read-only compatibility with legacy ingest metadata.
- [x] Session-stat coverage gate prevents partial-window zeroes.
- [ ] Busy-VM deployment: run one explicit foreground pass, verify bounded output
  and coverage, install disabled timer, then explicitly enable it.
- [ ] Observe repeated successful timer cycles, advancing freshness, no duplicate
  growth, no raw-data journal output, and unchanged game/web process state.

## Explicitly Out Of Scope

- Raising the collector to unbounded whole-file reads.
- Automatic historical backfill of every oversized log.
- Game or web restart as part of ingest installation/activation.
- Browser/GET/app-start polling, hidden threads, or session scheduler enablement.
- Discord/public combat-stat enrichment.
