# Player Data Truth Read-Only Audit

Status: historical read-only baseline. Its timestamp-truth recommendation was
implemented by the later remediation plan; production acceptance and subsequent
session/history work are recorded in the current player contracts and checklist.

Date: 2026-07-05
Branch audited: feat/web-interface
Mode: read-only code/schema/UI audit

## Scope

This audit inspected the local schema and code paths for player_log_events, players, player_names, and player_sessions, plus the parser, collector, web job, CLI import, stored-log sessionizer, live scanner, maintenance path, and current web labels.

No code, schema, tests, routes, web UI, production data, production files, SSH, deploy, restart, or raw logs were modified. The remediation plan was not edited.

## Executive Finding

The click-time/ingest-time problem is confirmed for the current manual player-log collection path.

The parser can accept caller-supplied observed_at and raw_timestamp, but the current web and CLI collectors do not extract or pass either value from log lines. When /players/history/collect-logs runs, player_registry.ingest_player_log_events() sets player_log_events.created_at to _utc_now(). The event row usually has observed_at = NULL and log_timestamp = NULL, so the history UI and sessionizer fall back to created_at. In practice, the UI Time column and any sessions created from those stored events can show the collection/job time, which is often close to the operator click time for Update events from logs.

The same contaminated timestamp can also enter players.first_seen_at, players.last_seen_at, player_names.first_seen_at, and player_names.last_seen_at, because log-event ingest records reliable player observations using row.observed_at or row.created_at.

## Current Schema And Timestamp Map

### player_log_events

| Field | Where written | Source now | Meaning now | Can equal Update events from logs click/job time? |
| --- | --- | --- | --- | --- |
| observed_at | player_registry._player_log_event_row() from PlayerLogEvent.observed_at | Caller-supplied parser argument only | Intended observation/event time, but usually absent for collector-imported rows | Not directly in the current web/CLI collector, because it is usually NULL |
| log_timestamp | player_registry._player_log_event_row() from PlayerLogEvent.raw_timestamp | Caller-supplied parser argument only | Raw log timestamp fragment, currently time-of-day if supplied by a direct parser caller | Not in current web/CLI collector; ambiguous if supplied manually |
| created_at | player_registry.ingest_player_log_events() | ingested_at argument or _utc_now() | Ingest/storage time | Yes. Web /players/history/collect-logs and CLI player-history collect --write normally use ingest time |
| event_time property | PlayerLogEventRecord.event_time | observed_at or created_at | Display fallback, not a stored column | Yes, because it falls back to created_at |

Important detail: player_sessionizer._event_observed_at() uses observed_at or log_timestamp or created_at. If a direct caller stores only a time-of-day log_timestamp, sessionization can propagate that non-absolute value into session timestamps. The current web/CLI collector does not set log_timestamp, so it falls through to created_at.

### players

| Field | Where written | Source now | Meaning now | Can equal Update events from logs click/job time? |
| --- | --- | --- | --- | --- |
| first_seen_at | _record_reliable_player_observation() on first reliable ID insert | Caller observation timestamp | First time armactl recorded this reliable ID, not guaranteed first real player event | Yes, if the first reliable observation comes from a collector-imported log event with no event time |
| last_seen_at | _record_reliable_player_observation() on every reliable ID observation | Caller observation timestamp | Last time armactl recorded this reliable ID, not guaranteed last real player event | Yes, for imported log events with no event time; also equals current-roster refresh time for refresh-current |

Writers include record_current_players_snapshot() from current roster refresh, ingest_player_log_events() from stored log events, and observe_player_session() from session writers. For current-roster refresh/live scanner, these are observation/scan times by design. For stored logs, they should be event occurrence time, but currently fall back to ingest time.

### player_names

| Field | Where written | Source now | Meaning now | Can equal Update events from logs click/job time? |
| --- | --- | --- | --- | --- |
| first_seen_at | _record_reliable_player_observation() on first reliable_id/name insert | Same observation timestamp as players | First time armactl recorded this name for the ID | Yes, via log-event ingest fallback to created_at |
| last_seen_at | _record_reliable_player_observation() on repeated name observation | Same observation timestamp as players | Last time armactl recorded this name for the ID | Yes, via log-event ingest fallback to created_at |

### player_sessions

| Field | Where written | Source now | Meaning now | Can equal Update events from logs click/job time? |
| --- | --- | --- | --- | --- |
| open_observed_at | observe_player_session() on insert | Caller observed_at or _utc_now() fallback | Session open/presence observation time, not necessarily exact join time | Indirectly yes, if stored-log sessionization uses contaminated event created_at |
| last_seen_at | observe_player_session() on insert/update | Caller observed_at or _utc_now() fallback | Last session presence observation | Indirectly yes, same stored-log path |
| close_observed_at | close_player_session() on close | Caller close_observed_at or _utc_now() fallback | Close evidence observation, not necessarily exact leave time | Indirectly yes from contaminated disconnect/lifecycle events; also can equal live scan or maintenance job time by design |
| scanner_checkpoint_at | observe_player_session() | Caller checkpoint timestamp | Checkpoint timestamp for live scanner or stored-log sessionizer | Indirectly yes from contaminated stored events; equals live scan time for live scanner |
| created_at | observe_player_session() | Same timestamp used for open observation | Overloaded: row creation/bookkeeping and observation timestamp share one value | Indirectly yes; not a pure DB write clock |
| updated_at | observe_player_session(), close_player_session(), absence confirmation | Same timestamp used for update/close observation | Overloaded: update bookkeeping and observation/close timestamp share one value | Indirectly yes; also equals scan/maintenance close time by design |

## Current Data-Flow Map

### Parser

src/armactl/player_log_events.py exposes parse_player_log_event(line, observed_at=None, raw_timestamp=None, raw_source_ref=None).

The parser recognizes auth, update, faction join, disconnect, lifecycle, kill, suicide, teamkill, other-death, and ServerAdminTools combat-hint lines. It passes through observed_at and raw_timestamp only when the caller provides them. It does not currently extract timestamps from the line itself. Regexes are not anchored at the beginning, so lines like 12:00:01.000 BACKEND ... can match, but that prefix is ignored unless a caller separately passes raw_timestamp.

### Manual CLI Import

armactl player-history collect calls player_log_collector.collect_player_log_events() with explicit user-supplied files, bounds, and dry-run/write mode. It does not pass ingested_at, and the collector does not pass parsed line timestamps to the parser.

Result: written CLI imports currently store event created_at as import time unless some future caller changes the collector behavior.

### Web Update events from logs

POST /players/history/collect-logs queues players:collect-log-events and starts the worker. The job resolves only allowlisted current-instance files matching:

DATA_ROOT/INSTANCE/config/logs/*/console.log

The job calls collect_player_log_events(log_paths, registry_db_path, dry_run=False, ...) with no ingested_at. The collector calls parse_player_log_event(line, raw_source_ref=...) only.

Result: stored events usually have:

- observed_at = NULL
- log_timestamp = NULL
- created_at = job ingest time
- UI event_time = created_at

### Event Storage And Registry Observation

ingest_player_log_events() builds row payloads with created_at = ingested_at or _utc_now(). For each newly stored event, it records reliable player observations using observed_at = row.observed_at or row.created_at.

This is where event-ingest time enters players and player_names.

### Stored-Log Sessionization

player_sessionizer.sessionize_stored_player_log_events() reads already stored events ordered by COALESCE(observed_at, log_timestamp, created_at), event_id.

It then creates observations using event.observed_at or event.log_timestamp or event.created_at.

This is where contaminated event ingest time enters player_sessions.open_observed_at, last_seen_at, close_observed_at, scanner_checkpoint_at, created_at, and updated_at.

### Live Scanner And Maintenance

scan_live_player_sessions_once() uses _utc_now_text() unless an explicit observed_at is supplied. These timestamps are observation/scan times, not historical event occurrence times. Stale absence closes and maintenance closes use the explicit scan/maintenance timestamp as close evidence time. That is expected, but UI must not present it as exact leave time.

## Where Click-Time Or Ingest-Time Enters

1. The web button queues a background job. The job usually starts shortly after the click.
2. The collector parses allowlisted console.log lines without extracting a timestamp.
3. ingest_player_log_events() sets created_at = _utc_now().
4. players and player_names use row.observed_at or row.created_at, so imported historical events can update first/last seen to collection time.
5. /players/history displays event.event_time, which is observed_at or created_at.
6. Stored-log sessionization uses observed_at or log_timestamp or created_at, so sessions can inherit collection time.

The problem is therefore not only a display label problem. The stored derived registry/session data can be contaminated after import.

## Safe Reconstruction Assessment

### What exists today

- Some real log lines can contain time-of-day prefixes such as 12:00:01.000, and tests show the parser can still match these lines.
- The parser does not extract that prefix.
- The current web/CLI collector does not derive an absolute timestamp from file context.
- Current source_ref values are sanitized as basename plus a hash of the full path plus line number, for example console.log:HASH:LINE.
- Raw absolute paths, raw log lines, and IP/address-like values are intentionally not stored.

### Can current DB rows reconstruct event time by themselves?

No. For rows produced by the current collector, the DB usually lacks both an absolute event timestamp and a raw line timestamp. created_at is ingest time, not event time.

### Can current source refs help if original logs still exist?

Partially, but only by re-reading allowlisted original logs in place. A future repair tool could enumerate current allowlisted log paths, recompute the same path hash, match source_ref line numbers, and parse the matching line again. That does not require copying raw logs into the repo or storing raw absolute paths. It does require that the original files still exist and the path hash can still be recomputed from the same path.

This is not enough for DB-only recovery, and it is fragile for legacy rows when logs have rotated, been cleaned, moved, or were imported from arbitrary CLI paths no longer available.

### Date and midnight rollover risk

Line prefixes appear to be time-of-day, not a full absolute timestamp. A safe future implementation needs a date source from safe log context, such as a sanitized/parsed run directory marker, journal cursor metadata, or bounded file metadata captured during collection. The current stored source_ref does not preserve a parseable date marker.

If only time-of-day is available, midnight rollover is a real risk. A collector should process each file in line order, combine time-of-day with a trusted date/run marker, and advance the date when timestamps roll backward across midnight. Rows should record the derivation confidence/source so UI can distinguish exact, derived, and ingest-only times.

### Do future fixes need raw absolute paths or raw log lines?

No for new rows. A future collector can derive and persist sanitized structured fields such as event_occurred_at, event_time_source, event_time_confidence, optional bounded raw_log_time, source_file_marker, and line number without storing raw absolute paths or raw lines.

For legacy rows, DB-only reconstruction is not safe. Repair can be attempted only while original allowlisted logs are still present and re-readable in place; raw logs should not be copied into the repo.

## UI Label Problems

### /players/history

- Time is misleading. It displays event.event_time, which currently means observed_at or created_at. For collector-imported rows this is usually ingest/job time.
- Suggested Slice 1 direction: split or relabel as Event time only when a trustworthy occurrence timestamp exists; otherwise show Collected at or Recorded at with a clear source indicator.

### /players/known

- First seen and Last seen are misleading. These are first/last armactl observations, not necessarily real first/last player activity.
- They can be contaminated by log collection ingest time.
- Suggested Slice 1 direction: relabel to First recorded and Last recorded, or add source-aware text such as First observed by armactl / Last observed by armactl.

### /players/sessions

- Observed is ambiguous. It can be auth/update event time, inferred faction/combat presence time, live scan time, or contaminated ingest time.
- Last observed is similarly ambiguous.
- Inferred close is misleading for explicit disconnect or server-boundary closes, because not every close is inferred. It is also misleading for open rows where no close exists.
- Open and Closed status labels are only stored-session state, not guaranteed live online/offline truth. Open sessions summary can be misleading when automatic live tracking is not enabled or timestamps are contaminated.
- Latest observed summary inherits the same ambiguity.
- Suggested Slice 1 direction: use source-aware labels such as Opened/first observed, Last evidence, Close evidence, and Stored open / Stored closed, with timestamp provenance.

## Event Noise And Details Findings

Default /players/history mode is player_events. In list_player_log_events(), this excludes:

- server_lifecycle
- player_disconnected rows with no reliable player_id

Because current disconnect parsers do not set reliable player IDs, disconnect and lifecycle events are mostly hidden from the default history and appear under the session-evidence mode.

Default history can include:

- player_authenticated
- player_update
- faction_join
- kill
- suicide
- teamkill
- other_death
- combat_hint
- any future player_disconnected row that has a reliable player ID

Session evidence/system diagnostics include:

- player_authenticated and player_update as strong open/presence evidence
- faction_join and combat events as inferred presence/faction/combat evidence
- player_disconnected as close evidence only when correlation is unambiguous
- server_lifecycle as server-boundary close evidence

Details often renders - because the template only fills that cell from victim, instigator, combat fields, and combat flags. Auth/update/faction/lifecycle/disconnect rows may have useful structured fields, but those are rendered as player/faction columns or hidden under Diagnostics, not in Details.

Structured safe details available in DB/code include:

- event_type, source, source_ref, confidence
- player_id, player_name, session_player_id, rpl_identity, connection_id, be_slot
- player_faction, faction_resource
- victim_id, victim_name, victim_session_player_id, victim_faction
- instigator_id, instigator_name, instigator_session_player_id, instigator_faction
- teamkill, suicide, ai_instigator, damage_type, hit_zone, distance_m

No raw log lines, raw absolute paths, IP/address columns, role/loadout truth, ban/kick history, or Discord-derived K/D are available in the audited schema.

## Production Snapshot

Skipped during this audit because the slice explicitly prohibited production/SSH/deploy/restart risk. Later approval-gated production read-only and acceptance passes were completed and are recorded in [player-data-truth-remediation-plan.md](player-data-truth-remediation-plan.md) and [player-session-supervised-pipeline-contract.md](player-session-supervised-pipeline-contract.md).

## Recommendation Closure

The recommended timestamp-truth slice is complete: player log events now keep
occurrence, observation, and collection times with source/confidence labels;
collectors derive safe event times without storing raw paths or raw lines;
sessionization uses trusted ordering; UI labels distinguish evidence semantics;
and focused tests cover delayed collection and legacy/ambiguous rows. Legacy
rows remain truthfully labelled rather than being rewritten from unavailable
evidence. The current remaining player work is moderation Slices 7c-7e and
separately truth-gated Discord enrichment.

## Explicit Out Of Scope

- No code/schema/test changes in this slice.
- No remediation plan edits.
- No production DB/log reads or writes.
- No web button clicks or job execution against production.
- No deploy, SSH, service restart, or process control.
- No raw logs copied into the repo.
- No raw absolute paths, raw log lines, IPs, secrets, or full RCON rows stored or displayed.
- No commit or push.
