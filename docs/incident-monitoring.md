# Persistent Incident Monitoring

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
`double free` followed by `Application hangs (force crash)` for the same PID is
updated as one incident rather than two unrelated failures.

## Hang detection

When the game service remains `active/running` but `console.log` has stopped
updating for 90 seconds, the monitor records an early
`telemetry_hang_suspected` bundle. If the engine later writes a watchdog or
native crash signal for the same PID, the collector upgrades that bundle with
the confirmed evidence.

The monitor intentionally does not perform automatic recovery. Recovery policy
and profile fallback remain separate operator-controlled mechanisms.

## Native attribution boundary

The monitor installation adds a narrow systemd drop-in with
`LimitCORE=infinity`, and the generated game service includes the same limit.
It takes effect for the next game process after the systemd units are
installed/reloaded; the monitor does not restart a running server merely to
apply it.

A journal message such as `double free or corruption` proves the native memory
failure mechanism, but it does not by itself prove which mod triggered the
engine path. Exact native-function attribution still requires a core or
backtrace supplied by the host's configured core handler. The bundle records
the core limit, kernel core pattern, and availability of `gdb`/`coredumpctl` so
missing capture capability is explicit instead of silently assumed.
