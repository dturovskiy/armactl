# Operational Validation Report — 2026-09-22

Scope: the public `armactl` checkout on `feat/web-interface` and the two
existing game VMs. This is a point-in-time evidence report, not a second task
list or approval to merge into `main`.

## Result

The admin-roster and mapped-identity duplicate fixes were deployed Git-only,
Serhiivka first, without changing game configuration, profiles, scenarios,
Workshop payloads, or game processes. Both web services passed health and
readiness after restart. This does **not** complete live Game Master acceptance
for a designated non-`deus` player.

| Check | Serhiivka | Chervonopilya |
| --- | --- | --- |
| Deployed code | `3ea668f` | `3ea668f` |
| Game process after web deploy | PID `238219`, `NRestarts=0` | PID `396612`, `NRestarts=0` |
| Public status snapshot at 07:12 UTC | Ready, 0/128, 120 FPS | Ready, 0/128, 120 FPS |
| Web readiness | `/healthz` and `/readyz` passed | `/healthz` and `/readyz` passed |
| Native admin / SAT / WCS ACLs | 7 identities aligned | 6 identities aligned |

The Chervonopilya web restart was preceded by a zero-player check and a
read-only check showing no queued or running web jobs. Neither game service was
restarted. These checks show current operation and configuration consistency;
they do not prove long-term stability or effective mod permissions in-game.

## Code And CI Evidence

- `1a4cd4a` uses the explicit instance-local SteamID64-to-UUID mapping when
  marking an RCON roster player as an existing official admin. Name-only
  matches are rejected. CI run `35694522416` passed Ruff, 1664 tests, and
  package build.
- `4f9dde1` uses that mapping in the shared Web/TUI admin mutation so an
  existing SteamID64 record is updated rather than duplicated when its UUID is
  submitted. The corrected test head `3ea668f` passed CI run `35695653132`:
  Ruff, 1665 tests, and package build.
- The subsequent documentation-only head `a0a78ba` passed CI run
  `35696367381`. Production checkouts intentionally remain on the verified
  code commit `3ea668f`; no game update was invoked for these web changes.

## Python 3.14 Local Test Diagnosis

The WSL `.venv` uses CPython 3.14.4, FastAPI 0.136.3, Starlette 1.3.0, and
AnyIO 4.13.0. In the restricted tool sandbox, even a minimal FastAPI
`TestClient` request hangs. A stack dump shows the caller waiting for AnyIO's
cross-thread portal while the event-loop thread is idle. The smaller decisive
reproduction is `socket.socketpair()`: creation succeeds but sending one byte
returns `PermissionError: EPERM` in that sandbox. The same Python executable,
outside the restriction, sends and receives on the socketpair and receives
HTTP 200 from the minimal `TestClient`. Therefore the observed hang is caused
by denied local socket I/O, not by demonstrated Python 3.14 incompatibility.

`scripts/run-host-tests` now checks local socketpair communication before
starting pytest and fails with an actionable message instead of waiting in
`TestClient`. This check was exercised in both blocked and permitted
environments.

## Local Validation

- Outside the socket-restricted sandbox, the complete suite on CPython 3.14.4
  passed: **1665 tests**, one non-fatal `DeprecationWarning`, in 380.79 seconds.
  The warning concerns `fork()` from a multithreaded test process; no test
  failed or hung. The earlier attempt stopped at 95% only because its command
  timeout was set too low (420 seconds).
- `ruff check src tests`, `sh -n scripts/run-host-tests`, `git diff --check`,
  and the documentation-status tests passed locally.
- The host-test runner fails immediately with the socketpair explanation in
  the restricted sandbox. The same runner passes focused pytest and Ruff with
  the same Python 3.14 executable when local socket I/O is permitted.

This establishes a usable local 3.14 validation path; it does not change the
package's published Python support claims. Aligning `requires-python`,
classifiers, local validation, and the CI version matrix remains a separate
public-merge gate in [checklist.md](checklist.md).

## Remaining Acceptance Boundary

Serhiivka is currently on vanilla, so its synchronized SAT and WCS files are
not evidence that their roles were loaded into the game. A designated
non-`deus` player must verify the intended native admin and mod Game Master
behavior during a safely loaded modded session. No live admin mutation was
made for this report. Native moderation acceptance and the public-`main` merge
gate remain separate open work in [checklist.md](checklist.md).
