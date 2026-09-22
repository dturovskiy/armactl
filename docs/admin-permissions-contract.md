# Admin Permission Synchronization Contract

Status: active detailed contract. Implementation is complete; remaining
non-`deus` production identity acceptance is tracked only in
[checklist.md](checklist.md).

## Purpose

`game.admins` in the instance `config.json` is the canonical administrator list.
Supported mod ACLs must not become independent sources of operator membership.

The synchronized role targets are:

- ServerAdminTools `admins`;
- ServerAdminTools `gameMasters`;
- WCS Admin `gameMaster`.

The web UI describes this canonical mutation as **full admin + GM**. It is one
audited action, not a separate native-admin and mod-admin toggle. When GM Tools
is active, its documented fallback treats the native `ADMINISTRATOR` role as a
Game Master; rank-changing commands still depend on that mod's own runtime
configuration.

Supported role fields may use either a legacy JSON list or the production
object form `UUID -> label`. Synchronization preserves each file's existing
representation. Object-form roles keep existing labels for retained UUIDs and
use the local admin label for newly synchronized UUIDs.

WCS `Team`, ban lists, developer roles, and unrelated mod settings are not part
of ordinary game-admin membership and are preserved.

## Mutation Contract

Web and TUI add, update, and remove actions use the shared
`armactl.admin_acl_sync` orchestration layer:

1. Acquire the instance admin-ACL lock.
2. Snapshot `config.json`, `admins-state.json`, and existing supported mod ACLs.
3. Apply the canonical `game.admins` mutation through `admins_manager`.
4. Resolve reliable mod identity IDs.
5. Validate and stage exact SAT/WCS role lists.
6. Back up changed mod configs under `backups/admin-permissions/`.
7. Publish with same-directory temporary files, atomic replace, file fsync, and
   directory fsync where supported.
8. Roll back every snapped file if validation or publishing fails.

An administrator is therefore not left with only some supported rights. If a
Steam admin has no reliable UUID mapping while SAT/WCS is installed, the change
fails and the canonical mutation is rolled back.

Raw `config.json` editing cannot change `game.admins`; operators must use the
Admins workflow so synchronization, backup, audit, and restart tracking cannot
be bypassed.

## Paths

Generated Arma profile files normally live below `config/profile/`. A legacy
config directly below `config/` is accepted only when the profile copy is
absent. If both copies exist, synchronization fails closed instead of choosing
an ambiguous runtime file.

Missing SAT or WCS configs are treated as not installed/not initialized and are
not created from guessed defaults.

## Operational Notes

- A game-server restart remains required for changed ACLs to become effective.
- The Web action audit remains the operator audit record.
- Backups contain the original private mod configuration and must remain inside
  the instance backup boundary. The workflow keeps the newest 50 ACL backups
  to prevent unbounded growth.
- Symlinked or out-of-bound mod ACL files are rejected.
- Direct manual edits outside armactl can still create drift; use the supported
  Admins workflow for routine changes.

## Staged Roster Acceptance (2026-09-22 UTC)

Commit `1a4cd4a8a6195336681983955fa39b4cd58537aa` fixes the remaining
existing-admin roster mismatch: `game.admins` may contain a SteamID64 while
RCON reports that same player's IdentityId. The Admins page now uses only the
explicit instance-local SteamID64-to-UUID mapping already used for supported
mod ACL synchronization. A matching player is shown as an existing admin
rather than offered the add action. A name-only mapping does not qualify as
proof.

GitHub Actions run `35694522416` passed Ruff, 1664 tests, and package build.
Serhiivka was fast-forwarded from `0d642f3` to `1a4cd4a` first, with a web-only
restart and successful `/healthz` and `/readyz`. The game stayed on PID `238219`
with zero systemd restarts. Chervonopilya followed after a zero-player preflight
with no queued or running web jobs; its web restart passed both checks, and the
game stayed on PID `396612` with zero systemd restarts and fresh 120 FPS
telemetry. Neither deployment changed game config, profiles, mods, or Workshop
payloads.

Read-only production ACL checks found all configured identities aligned across
`game.admins`, SAT `admins`, SAT `gameMasters`, and WCS `gameMaster`: seven on
Serhiivka and six on Chervonopilya. Serhiivka's one SteamID64 admin has an
explicit UUID mapping; Chervonopilya's six native entries are already UUIDs.
This proves configuration consistency and the UI regression, not effective
in-game Game Master or rank-changing behavior. A designated non-`deus` player
must still perform the live acceptance while the relevant mods are loaded;
Serhiivka is currently on vanilla, so the mod-side runtime check remains open.

Follow-up commit `4f9dde1` adds the same exact-match guard to the shared
Web/TUI mutation: submitting the mapped RCON UUID updates the existing
SteamID64 admin instead of creating a second `game.admins` entry. An invalid
or ambiguous mapping fails closed. The corrected test head `3ea668f` passed
GitHub Actions run `35695653132` (Ruff, 1665 tests, package build). Both VMs
were then fast-forwarded through Git to `3ea668f`, Serhiivka first, and only
their web services were restarted. `/healthz` and `/readyz` passed on both;
Serhiivka kept game PID `238219`, Chervonopilya kept `396612`, and each retained
`NRestarts=0`. Chervonopilya remained Ready with fresh 120 FPS telemetry.
No live admin mutation was used for this acceptance; the non-`deus` in-game
Game Master check remains open.
