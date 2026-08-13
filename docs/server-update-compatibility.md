# Server Update Compatibility

armactl treats the game package and the configured mod stack as two separate
compatibility concerns. An update never edits or selectively deletes Workshop
addons to make a server boot.

## Update flow

1. Save a compact pre-update baseline under
   `<instance>/backups/update-baselines/<UTC timestamp>/`.
2. Install the Steam build into an isolated candidate package.
3. Create a config-only candidate bundle and canary-test the configured scenario
   and mod stack against the shared Workshop addon pool.
4. If the modded canary passes, promote the candidate in modded mode.
5. If it fails, canary-test the same build with official Conflict Everon,
   `game.mods: []`, and persistence disabled.
6. If vanilla passes, atomically park the modded config bundle at
   `<instance>/server-update/parked-modded-profile/` and activate the new build
   with the generated vanilla profile.
7. If vanilla also fails, leave the active package and profile unchanged.

The parked bundle contains the original `config.json` and its mod/scenario
selection. Workshop payload stays in the canonical `<instance>/config/addons/`
pool. Profile switching never clones, moves, or deletes those files. A vanilla
profile has `game.mods: []`, so after restart the server simply ignores every
stored Workshop addon.

The compact baseline contains the non-addon profile, `config.json` SHA-256,
addon directory inventory, small addon metadata, and a manifest. It deliberately
does not duplicate multi-gigabyte `.pak` files. Full `.pak` preservation comes
from the untouched shared addon pool. Infrastructure snapshots remain an
additional recovery layer, not a replacement for the in-guest baseline.

## Operator commands

The game service must be stopped before update or profile canaries.

```text
armactl update status
armactl update server
armactl update vanilla
armactl update retry-modded
armactl update rollback
```

- `update server` downloads a new candidate and automatically chooses modded or
  vanilla compatibility mode from canary evidence.
- `update vanilla` uses the already-installed build and is useful when a server
  is currently unable to boot its modded stack.
- `update retry-modded` tests the parked mod/scenario selection against the
  active build and shared addon pool.
  A failed retry keeps vanilla active; a successful retry restores all mods and
  the custom scenario together.
- `update rollback` restores the retained pre-update package/profile generation.

The compatibility state is stored at `<instance>/server-update/state.json`.
Canary logs are disposable and stay under `<instance>/server-update/canary-logs/`.

## Named profiles and automatic policy

Profiles are operator-owned config bundles. The active `config.json` occupies
`<instance>/config/`; inactive profiles live under
`<instance>/server-update/profiles/<name>/`. All profiles share the one canonical
`<instance>/config/addons/` pool. A successful switch changes only the active
config and optional armactl mod-state sidecar; it does not touch addon payload.
The selected profile is always canary-tested against the current server build
and shared addon pool before the service starts it.

```text
armactl update profile list
armactl update profile rename-active zakarpattia
armactl update profile create vanilla-everon --vanilla
armactl update profile create test-current
armactl update profile switch vanilla-everon
armactl update auto-fallback status
armactl update auto-fallback on
armactl update auto-fallback off
```

The policy is stored at `<instance>/server-update/policy.json`. With automatic
fallback disabled, a failed modded update canary leaves the current generation
unchanged and requires an explicit profile or vanilla switch.

## Mod compatibility evidence

Canaries write bounded evidence to
`<instance>/server-update/mod-compatibility.json`. The Mods page shows results
only for the exact active Steam build, named profile, and configured mod version:

- `Compatible`: the mod was a member of a profile whose canary passed;
- `Incompatible`: fatal output was uniquely attributable to that mod ID/name;
- `Blocked by dependency`: `addon.gproj` declares a direct or transitive
  dependency that was identified as incompatible;
- `Stack failed; mod unknown`: the profile failed but evidence did not safely
  identify this individual mod;
- `Not tested for current build`: there is no exact evidence.

A Workshop version string is not treated as proof of game compatibility. armactl
does not claim that every member of a failed stack is broken, and does not
blindly enable a mod whose dependencies or scenario requirements were not
verified together.
