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

If the problem started after running install/repair from a root shell, rerun it
from the regular Linux account that owns the instance so the sudoers rule is
generated for the correct user.

## Metrics show as Unknown

Runtime metrics can be unavailable when:

- the service is stopped
- the main PID is gone
- systemd accounting values are missing

Check:

```bash
systemctl show armareforger.service --property=MainPID,MemoryCurrent,CPUUsageNSec
```

## Server FPS telemetry is unavailable or stale

Symptoms:

- `armactl status` shows Server FPS as unavailable
- TUI or Telegram metrics do not show FPS/frame-time
- telemetry age is stale

Check that the running server process includes `-logStats 10000`:

```bash
pgrep -af ArmaReforgerServer
```

Expected fragment:

```text
-logStats 10000 -maxFPS
```

Check that the server is writing FPS telemetry:

```bash
grep -RiaE 'FPS:|frame time' ~/armactl-data/default/config/logs | tail -20
```

Check service logs:

```bash
sudo journalctl -u armareforger.service -n 200 --no-pager
```

If `-logStats 10000` is missing, regenerate the service/start script through
repair or service generation, then restart the server:

```bash
./armactl repair
sudo systemctl restart armareforger.service
./armactl status
```

If `-logStats 10000` is present but no `FPS:` lines appear, wait for the next
telemetry interval and re-check the runtime logs.

## Existing service is found but config or binary is wrong

Use:

- `Detect Existing Server`
- `Repair Installation`
