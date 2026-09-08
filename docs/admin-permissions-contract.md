# Admin Permission Synchronization Contract

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
