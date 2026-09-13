# Persistent Incident Monitoring

Status: active detailed contract. The Serhiivka baseline is accepted; remaining
production acceptance is tracked only in [checklist.md](checklist.md).

`armactl` can run a supervised, read-only evidence collector every 15 seconds.
It records evidence but never stops, starts, or restarts the game server and
never changes the active config, profile, scenario, or Workshop files.

## What is captured

For a native crash, allocator failure, engine watchdog hang, or terminal startup
failure, one correlated bundle contains:

- `metadata.json` with the UTC event/capture times, PID, classification,
  confidence, selected evidence, and artifact manifest;
- `journal.log` with a bounded, redacted window around the signal;
- bounded tails of `console.log`, `script.log`, `error.log`, and `crash.log`;
- `service.json` with systemd state, restart count, exit result, and core limits;
- `runtime.json` with the server version found in the log, scenario, active mod
  IDs/names/versions, and core-capture capabilities;
- a best-effort `/proc/<PID>` snapshot (`status`, limits, memory maps, thread
  names, wait channels, and syscalls) when the original process is still alive.

Secrets and IP addresses are redacted. The collector snapshots only an
allowlisted config summary; it never copies the raw config or its passwords.
Artifacts are bounded and can be opened only by authenticated web users with
log-view permission.

The storage location is:

```text
<data-root>/<instance>/incidents/<UTC timestamp>-<kind>-<PID>-<digest>/
```

These incident bundles are separate from update/config backups. Existing
update baselines remain under
`<data-root>/<instance>/backups/update-baselines/`.

## Installation and status

Deploy the code through Git, then install and explicitly enable the timer:

```bash
./armactl incidents monitor install
./armactl incidents monitor enable
./armactl incidents monitor status
```

Run one non-restarting foreground collection pass with:

```bash
./armactl incidents monitor run --once
```

The first pass looks back 24 hours so a recent retained journal failure is not
lost. Later passes use the systemd journal cursor and event fingerprints.
Engine-log fingerprints use the log generation and absolute line offset, so an
old fatal line is not rediscovered merely because the active append-only log's
mtime changes. Correlated updates retain the first known game PID and any early
live-process artifacts while adding later confirmation evidence.
`double free` followed by `Application hangs (force crash)` for the same PID is
updated as one incident rather than two unrelated failures.

Known high-signal failure windows are assigned a bounded likely-trigger label.
For example, an unresolved `SAL_DroneBulletComponent` immediately before a
native crash is attributed to the Realistic Combat Drones/FPV dependency path.
This is a reproduction lead rather than proof that addon script code owns the
final native Enfusion fault.

## Hang detection

When the game service remains `active/running` but `console.log` has stopped
updating for 90 seconds, the monitor records an early
`telemetry_hang_suspected` bundle. If the engine later writes a watchdog or
native crash signal for the same PID, the collector upgrades that bundle with
the confirmed evidence.

The monitor intentionally does not perform automatic recovery. Recovery policy
and profile fallback remain separate operator-controlled mechanisms.

## Active log health warning

The authenticated dashboard also performs a lightweight, read-only health check
against the current log generation selected by the existing FPS telemetry
reader. This warning is independent from retained crash incidents:

- only sibling `console.log`, `error.log`, and `script.log` files are allowed;
- symlinks and paths outside the current instance log generation are rejected;
- at most the final 256 KiB of each file is read;
- a file is considered unusually large at 256 MiB or more;
- a spam signal requires at least 20 occurrences in the bounded tail of one
  fixed marker: `Virtual Machine Exception`, `Reason: Division by zero`,
  `Unknown class`, `Addon loading failed`, or `Cannot create game`.

The dashboard returns only the log basename, size, controlled signal label, and
match count to users with log-view permission. It never returns the filesystem
path or matching raw lines and never restarts the game or changes a profile.

## Native attribution boundary

The monitor installation adds a narrow systemd drop-in with
`LimitCORE=infinity`, and the generated game service includes the same limit.
It takes effect for the next game process after the systemd units are
installed/reloaded; the monitor does not restart a running server merely to
apply it.

## Serhiivka acceptance evidence

On 2026-09-13, commit `32ec2e9` was deployed through Git and only the web
service was restarted. The game PID and restart counter remained unchanged;
the public status stayed ready at 120 FPS. The 15-second monitor timer remained
active, its journal probe succeeded, and its heartbeat was current.

The retained 2026-09-12 startup crash was re-evaluated from its bounded bundle
without rewriting the stored metadata. The operator view reported
`Realistic Combat Drones / FPV dependency stack` with high trigger-correlation
confidence and retained the immediately preceding
`Unknown class 'SAL_DroneBulletComponent'` line. An unrelated obsolete
`CLBR_PlayerController` warning was excluded. The assessment still states that
the final native fault may be inside Enfusion until a core or backtrace proves
the owning native function.

All captured files in that bundle were below 100 KiB, sensitive values were
redacted, and the runtime snapshot contained only the allowlisted profile
summary. `LimitCORE` was unlimited, while `coredumpctl` and `gdb` were not
available in that VM; the capability report exposes this limitation instead of
claiming that a native backtrace was captured.

This completes retained-incident presentation acceptance on Serhiivka. A
deliberately induced live stale-telemetry event was not performed, and
Chervonopilya remains a separate approval-gated production acceptance step.

A journal message such as `double free or corruption` proves the native memory
failure mechanism, but it does not by itself prove which mod triggered the
engine path. Exact native-function attribution still requires a core or
backtrace supplied by the host's configured core handler. The bundle records
the core limit, kernel core pattern, and availability of `gdb`/`coredumpctl` so
missing capture capability is explicit instead of silently assumed.
