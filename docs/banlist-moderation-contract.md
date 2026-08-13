# Banlist And Moderation Contract

## Status

Slices 7a-7c are complete for design, the typed read-only native adapter/page,
and the backend-only verified ban/unban service. Slice 7c adds no mutation route,
template, browser control, or production deployment. Mutation UI and staged
production acceptance remain split across Slices 7d-7e. Kick remains deferred
until fresh-roster target resolution and native response fixtures are proven.

## Authoritative Backend Decision

The Arma Reforger dedicated server's native ban list, queried and mutated
through admin RCON, is the only source of truth for armactl ban state.

The official Reforger server-management contract provides these commands:

- `#ban list [page]` lists native server bans;
- `#ban create <playerId|identityId> <durationInSeconds> [reason]` creates a
  ban;
- `#ban remove <identityId>` removes a ban;
- `#kick <playerId>` disconnects a currently connected player without creating
  a ban.

See the official [Arma Reforger Server Management][server-management] and
[Server Config][server-config] documentation. RCON must use `admin` permission;
the server-side RCON whitelist/blacklist may still deny individual commands and
must fail closed.

[server-management]: https://community.bistudio.com/wiki/Arma_Reforger:Server_Management
[server-config]: https://community.bistudio.com/wiki/Arma_Reforger:Server_Config

armactl must not create a second ban registry in `players.db`, `web.db`,
`config.json`, a sidecar, or a mod config. Local audit and recovery records are
operator evidence, not ban truth.

## Existing Surface Audit

| Surface | Current behavior | Slice 7 decision |
| --- | --- | --- |
| `src/armactl/rcon.py` | Uses the configured RCON password transiently and runs only `#players` / `players` roster queries. | Keep protocol transport ownership here. Add typed moderation operations later; never expose arbitrary request-supplied commands. |
| `game.admins` | Canonical game-admin membership. | Not a ban source. Admin synchronization remains separate. |
| `ServerAdminTools_Config.json` `bans` | The SAT guard clears placeholder-only values and otherwise preserves the field. | Not authoritative, not mirrored, and not mutated by Slice 7. A later SAT-specific feature would require its own contract. |
| WCS/SAT admin ACLs | Synchronized from `game.admins`. | Not ban sources. Existing ACL rollback behavior must not be coupled to native bans. |
| `players.db` player registry and sessions | Stores reliable IDs, names, evidence, and sessions without IPs. | Read-only candidate/search context only. It must not store authoritative ban state. |
| Web audit log | Records bounded intent/outcome events for existing mutations. | Records moderation intent/outcome and uncertainty, but does not prove current ban state. |
| Current/known/session player pages | Authenticated identity and evidence views. Ban list tab is disabled/planned. | Reuse reliable identity links after the native read adapter exists. Do not bolt mutation logic into page models. |

No current armactl code implements ban, unban, kick, native ban-list parsing,
or ban-list persistence. No current permission grants moderation authority.

## Identity Contract

The durable moderation target is a normalized reliable player identity accepted
by `player_identity.normalize_reliable_player_id(...)` and supported by the
native Reforger command.

- Prefer `identityId` for create/remove because it does not require the player
  to be connected.
- A nickname is display/search context only. It is never an action target.
- Historical aliases must not merge two reliable IDs.
- Slot/player IDs are transient. They may be used only for a kick or online
  ban after a fresh roster lookup immediately before execution.
- A stale slot/player ID must never be reused after a refresh failure or roster
  change.
- Name-only, AI, count-only A2S, ambiguous, or missing-ID rows stay read-only.
- Ban removal targets the authoritative identity returned by the native ban
  list, not a local player-name match.

## IP Policy

IP moderation is not part of Slices 7b-7e.

Raw game/BattlEye logs can contain addresses, but the current player collector
redacts address-like text before storage. That behavior remains unchanged.
armactl must not add an IP column, IP history, IP search, IP ban target, raw log
display, or address-bearing audit detail as part of the native identity ban
manager.

Any future IP moderation requires a separate product/security decision that
defines legal basis, purpose, retention, access permission, encryption or
hashing limits, deletion, audit exposure, migration, NAT/VPN/shared-address
risks, and the actual authoritative backend. It must not be smuggled into a
player identity or banlist migration.

## Permission Contract

Slice 7 runtime work must introduce one dedicated code-level permission,
`players:moderate`, rather than reusing `players:view`, `admins:manage`, or
`actions:run`.

- Ban-list reads and all moderation actions require `players:moderate`.
- Existing owner users receive it through `ALL_PERMISSIONS`.
- `players:view` continues to allow player/session/history views only.
- Future roles can grant player visibility without moderation authority.
- Every mutation remains authenticated, POST-only, and CSRF-protected.
- GET routes must never ban, unban, kick, refresh registries, run jobs, or write
  recovery state.

## RCON Adapter Contract

The transport layer may gain typed operations for native moderation, but no web
or service caller may submit a raw command string.

Required typed DTOs:

- `NativeBanEntry`: authoritative identity, native ban ID when returned,
  duration/expiry representation when proven, and only fields present in the
  native response;
- `NativeBanListResult`: `available`, bounded entries, pagination state, and a
  controlled error class/message;
- `NativeModerationResult`: action, target identity, command outcome,
  verification outcome, changed/no-op/uncertain classification, and controlled
  message;
- `CurrentKickTarget`: fresh transient player ID plus reliable identity and
  roster observation time.

The adapter must:

- reuse the existing RCON packet/session transport;
- set bounded connect/command/overall timeouts;
- cap pages and rows (`#ban list` returns at most 25 rows per RCON page);
- parse only fixture-proven response shapes;
- treat unknown/malformed/truncated pages as unavailable or partial, never as
  an empty authoritative list;
- redact passwords, hosts, ports, raw commands, raw responses, paths, IPs, and
  control characters from DTO errors;
- execute only through the moderation service's per-instance lock so
  list/create/remove/kick checks cannot race inside armactl;
- reject newline/control characters and out-of-contract durations/reasons;
- never persist the RCON password or raw response.

## Slice 7b: Read-Only Native Ban List

Slice 7b establishes the backend adapter before any mutation UI.

Acceptance criteria:

- query `#ban list` through the typed adapter with bounded pagination;
- prove parser behavior with sanitized fixtures for empty, one-page,
  multi-page, malformed, timeout, permission-denied, and truncated responses;
- return an explicit unavailable/partial state when full authority cannot be
  proven;
- use no persistent ban cache and no shadow ban table;
- keep GET query-only for armactl storage;
- expose no ban actions yet;
- add an authenticated read-only ban-list page gated by `players:moderate` only
  after the adapter tests are green;
- show native source/freshness/unavailable labels and never present SAT or audit
  rows as current bans.

### Slice 7b Runtime Contract

`NativeBanEntry` contains exactly the fixture-proven native columns:

- `native_ban_id: str`;
- `player_uid: str`;
- `duration_seconds: int`.

It contains no raw response, nickname, IP, reason, expiry, path, command, host,
port, or error field. `duration_seconds == 0` is rendered as the native
permanent value; positive values are rendered as exact native seconds.

`NativeBanListResult` contains:

- `requested_page`, bounded to `1..100`;
- `available` and `complete` booleans;
- `status`, exactly `complete`, `partial`, or `unavailable`;
- at most 25 `entries`;
- controlled `error_code` and `error`;
- derived `has_previous` and conservative `has_next` navigation flags.

One adapter call sends only `#ban list <requested_page>` and never follows
pages automatically. Previous is available above page 1. Next is offered only
for a verified complete 25-row page below page 100; this is a bounded
continuation hint, not a claimed total page count.

Parser classification is fail-closed:

- the documented `BanID ; Player UID ; Duration` header with no rows is a
  complete authoritative empty page;
- one to 25 valid three-column rows with no unknown content is complete;
- valid bounded rows plus malformed, conflicting, excess, or truncated content
  is partial;
- malformed/unknown content without a valid row, an empty unproven response,
  permission denial, timeout, login failure, stopped server, missing
  configuration, or RCON failure is unavailable;
- no unavailable/partial result is represented as an authoritative empty list.

Controlled error codes are `not_configured`, `server_unavailable`,
`timeout`, `permission_denied`, `malformed_response`,
`rcon_unavailable`, `config_unavailable`, and `command_unavailable`.
Messages are fixed and never copy an exception or native response. The DTO
therefore cannot carry a raw password, host, port, IP, path, command, response,
control character, or traceback.

`web.services.native_banlist` validates the page and performs the typed
adapter call under a per-instance moderation lock. The web route never accepts
a command string. `GET /players/bans` requires authentication plus
`players:moderate`; `players:view` alone receives 403. The player-page Ban
list tab renders only for users with `players:moderate`.

The page labels its source as `Reforger RCON`, reports the requested page and
complete/partial/unavailable state, and renders only the three native fields.
The response has no native observation timestamp, so the UI does not invent a
freshness time. It has no ban, unban, kick, reason edit, POST route,
confirmation, action button, local history, or nickname enrichment.

Slice 7b adds no schema migration, ban table, persistent cache, mirror,
sidecar, audit event, job, or recovery record. The authenticated GET performs
the native read only; it does not create or mutate `players.db`, ban storage,
audit output, jobs, or recovery state. Standard authenticated-page session
liveness and CSRF bookkeeping remain active so long-lived pages keep working;
those existing auth writes are not ban state or moderation side effects.

## Slice 7c: Ban And Unban Mutations

Ban/unban is a service workflow, not route or template logic.

Workflow:

1. Validate permission/CSRF in the thin route.
2. Normalize the reliable identity, duration, and bounded optional reason.
3. Acquire the per-instance moderation lock.
4. Read the authoritative native list and classify the baseline.
5. Write a redacted intent audit. Intent failure stops before RCON mutation.
6. Execute the typed native command.
7. Read the authoritative list again.
8. Classify the result as confirmed changed, confirmed idempotent no-op,
   confirmed unchanged failure, or uncertain.
9. Write the bounded outcome audit.
10. If verification or post-mutation bookkeeping is unavailable, write an
    operator-visible moderation-verification recovery record.

Idempotency rules:

- banning an already banned identity with the same effective native state is a
  successful no-op;
- unbanning an identity absent from a fully read authoritative list is a
  successful no-op;
- a partial/unavailable list can never prove either no-op;
- retries after transport uncertainty must re-read before another mutation.

Rollback/recovery rules:

- do not issue a blind inverse command after an RCON timeout because the first
  command may already have succeeded;
- do not automatically undo a confirmed safety action because outcome audit or
  local bookkeeping failed;
- record a separate moderation verification item, not a restart-pending item;
- the recovery item stores only action, normalized identity, safe reason class,
  timestamps, and verification state;
- operator retry first performs an authoritative read and then chooses no-op or
  mutation;
- restart is not required for a confirmed native RCON ban/unban.

Slice 7c implements the recovery record in `web.db` schema version 16. The
record stores only instance, action, normalized reliable identity, reason class,
verification state, and timestamps. It is not a ban cache or a shadow source of
truth. Because duration and reason content are deliberately not persisted, a
ban retry must receive an explicit validated duration and optional replacement
reason from the operator-facing caller. No blind default can turn an uncertain
temporary ban into a permanent retry.

## Kick Contract

Kick is a separate transient action and must not be implemented as a side effect
of viewing a player or creating a local record.

- Resolve the target from a fresh reliable RCON roster under the moderation
  lock.
- Require exact reliable identity match and a current transient player ID.
- Re-read/re-resolve immediately before `#kick <playerId>`.
- If the player disappeared, identity conflicts, or the roster is unavailable,
  fail closed without sending the command.
- A kick outcome does not create ban state and does not modify player/session
  truth.
- Kick may share audit/recovery DTOs with ban/unban, but not target resolution.

Kick is deferred to a narrower follow-up because the required fresh-roster
target-resolution and native response fixtures are not yet proven. It is not
part of the completed Slice 7c ban/unban backend.

## UI Contract

- The disabled `Ban list - Planned` tab remains disabled through Slice 7a.
- Slice 7b may enable a read-only list with source/freshness/availability.
- Mutating controls appear only after Slice 7c service acceptance.
- Ban/unban requires explicit confirmation through the Slice 7d UI; kick
  requires separate confirmation and remains absent until the deferred follow-up.
- Search may use reliable ID or known/current nickname, but the confirmation
  screen displays and submits the normalized reliable identity.
- Duration uses bounded predefined choices plus a validated exact value only if
  the native backend supports it.
- Reason is optional, bounded, sanitized, and never treated as round-trippable
  unless the native list actually returns it.
- Backend unavailable means `Ban list unavailable`, not an empty list.
- Raw native IDs not needed for action, commands, RCON output, host/port,
  passwords, IPs, paths, and tracebacks never render.

## Architecture And Reuse Boundaries

- `rcon.py` owns protocol/session transport and typed native commands.
- A new moderation service owns validation, locking, read-before-write,
  verification, idempotency, audit orchestration, and recovery classification.
- Player registry/session services remain query-only identity context and never
  gain ban columns or mutation methods.
- Routes own auth, permission, CSRF, DTO binding, redirect/notices only.
- Page models/templates render service DTOs and contain no backend decisions.
- Reuse `player_identity` normalization, audit helpers, redaction, and existing
  bounded text conventions.
- Do not reuse `mutation_recovery.RestartPendingRecovery` for native RCON
  uncertainty; it represents restart-required file/config mutations. Add a
  narrow moderation verification record instead of weakening that contract.
- Do not create a generic arbitrary RCON command endpoint or generic remote
  mutation framework.

## Slice Plan

- [x] Slice 7a: audit sources, choose native RCON truth, settle identity/IP,
  permission, recovery, UX, and architecture contracts.
- [x] Slice 7b: typed read-only native ban adapter, fixtures, permission, and
  authenticated read-only list.
- [x] Slice 7c: implement typed ban/unban mutations.
  - [x] Add bounded typed `#ban create` and `#ban remove` commands plus
    fixture-proven response classification in `rcon.py`.
  - [x] Normalize and validate reliable identity, duration, and bounded reason.
  - [x] Add one per-instance moderation lock.
  - [x] Read and classify the complete authoritative native baseline.
  - [x] Write redacted intent audit before any RCON mutation.
  - [x] Execute only the typed native command.
  - [x] Re-read the authoritative list after the command.
  - [x] Classify changed, idempotent no-op, unchanged failure, or uncertain.
  - [x] Write bounded outcome audit without raw command/response data.
  - [x] Add a dedicated operator-visible moderation-verification record and
    read-first retry path; do not reuse restart-pending recovery.
  - [ ] Deferred follow-up: add kick only with a fresh reliable roster, exact
    identity plus current player ID, immediate re-resolution, and
    fixture-proven response handling.
- [ ] Slice 7d: implement the mutation UI.
  - [ ] Keep all mutations POST-only, CSRF-protected, and gated by
    `players:moderate`.
  - [ ] Add separate explicit confirmations for supported ban and unban actions.
  - [ ] Submit normalized reliable identity, bounded duration, and sanitized
    optional reason; nickname remains search/display-only.
  - [ ] Render controlled changed/no-op/failed/uncertain/recovery notices with no
    IP, raw command/response, secret, path, or traceback exposure.
  - [ ] Add focused permission, route, template, CSRF, read-only GET, and
    sensitive-output regression coverage.
- [ ] Slice 7e: complete staged production acceptance.
  - [ ] Run Serhiivka-first read/mutation/retry/recovery smoke with a designated
    test identity and audited action.
  - [ ] Review journals, audit, authoritative state, and recovery state.
  - [ ] Roll out to Chervonopilya only after explicit approval.
- [ ] Future separate decision: IP moderation/privacy contract.

## Global Acceptance Gate

Slice 7 is not complete until all of the following are true:

- one authoritative native ban source and no shadow/mirrored list;
- reliable identity targeting only, with nickname display-only;
- dedicated moderation permission, auth, CSRF, and POST-only mutation;
- bounded typed RCON operations with no arbitrary command surface;
- read-before-write and read-after-write verification;
- idempotent retries and explicit uncertain state;
- no blind rollback on transport uncertainty;
- operator-visible recovery without pretending restart is required;
- no IP storage/exposure and no raw command/password/response/path leakage;
- GET pages remain free of armactl mutation;
- focused and full regression suites pass;
- staged Serhiivka acceptance precedes any Chervonopilya rollout.

## Out Of Scope

- SAT/WCS ban mirroring or synchronization;
- IP bans or IP history;
- automatic moderation, anti-cheat scoring, vote systems, or scheduled actions;
- public/Discord ban or identity data;
- nickname-only actions;
- player identity merge/split;
- moderation history inferred from raw game logs;
- arbitrary RCON console access;
- production changes during Slice 7a.
