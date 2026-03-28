# Troubleshooting

This page collects the most common operational problems seen during bootstrap,
install, repair, and day-to-day server management.

## Bootstrap or first launch fails

Symptoms:

- `./armactl` does not start
- repo-local `.venv` is missing or incomplete
- dependency installation fails

What to check:

```bash
./armactl
./scripts/run-host-tests
```

If the host blocks package downloads or Python package installation, fix the
underlying host/network issue first and then rerun the launcher.

## SteamCMD install or download fails

Symptoms:

- install fails during the SteamCMD step
- the log shows SteamCMD errors or timeouts

What to check:

- SteamCMD is installed and reachable
- the host has outbound network access
- enough disk space is available under `~/armactl-data/<instance>/server`

`armactl` now streams SteamCMD output into the TUI log, so long downloads should
show live lines rather than a silent hang.

## Server is visible in game but unreachable

This usually means the server started and registered, but clients cannot reach
the actual game port.

Check:

```bash
sudo ss -lunp | grep -E '(:2001|:17777|:19999)\b'
sudo ufw status verbose
```

Then verify:

- `bindPort` is correct
- `publicPort` matches the externally reachable UDP port
- `publicAddress` is correct if the host sits behind NAT or unusual networking
- external firewall or cloud security-group rules allow the required UDP ports

## Ports are not listening

Use:

```bash
armactl ports
armactl ports open
```

Or in TUI:

- `Manage Existing Server`
- `Check Ports`

If the service is running but the game port is still missing, inspect the server
log and `config.json`.

## Secure privileged control is not configured

If TUI or the Telegram bot reports that the secure privileged channel is not
installed, rerun:

- `Install / Update Bot Service` in TUI, or
- `Repair Installation`

This reinstalls the narrow helper and sudoers drop-in used for non-interactive
service actions.

## Metrics show as Unknown

Runtime metrics can be unavailable when:

- the service is stopped
- the main PID is gone
- systemd accounting values are missing

Check:

```bash
systemctl show armareforger.service --property=MainPID,MemoryCurrent,CPUUsageNSec
```

## Existing service is found but config or binary is wrong

Use:

- `Detect Existing Server`
- `Repair Installation`

If you are migrating from an older layout, also read [migration.md](migration.md).

