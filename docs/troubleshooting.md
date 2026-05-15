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

## Telegram bot cannot reach Telegram API

Symptoms:

- Telegram buttons stop responding or respond only intermittently
- bot logs show `NetworkError`, `TimedOut`, `ReadError`, or `ConnectError`
- `/start` or inline button callbacks work after retries but fail after idle periods

Check bot logs:

```bash
sudo journalctl -u armactl-bot.service -n 200 --no-pager
```

Check recent Telegram/network failures:

```bash
sudo journalctl -u armactl-bot.service --since "10 minutes ago" --no-pager \
  | grep -Ei 'ERROR|WARNING|NetworkError|TimedOut|ReadError|ConnectError|BadRequest|callback answer failed|message edit failed' \
  || echo "OK: no bot errors"
```

Check outbound HTTPS connectivity to Telegram:

```bash
curl -4 -sS -o /dev/null \
  --connect-timeout 3 \
  --max-time 6 \
  -w 'code=%{http_code} connect=%{time_connect}s tls=%{time_appconnect}s total=%{time_total}s ip=%{remote_ip}\n' \
  https://api.telegram.org/
```

A healthy outbound IPv4 path usually returns quickly with an HTTP status and a
Telegram IP address. If this fails, fix the host, provider firewall, DNS, proxy,
or outbound network path before debugging bot code.

## IPv6 vs IPv4 outbound diagnostics

Some hosts have broken IPv6 routing: DNS resolves an IPv6 address, but outbound
IPv6 connections fail or hang. Compare IPv4 and IPv6 explicitly:

```bash
curl -4 https://api.telegram.org
curl -6 https://api.telegram.org
```

Interpretation:

- `curl -4` works and `curl -6` fails quickly: IPv4 is usable, IPv6 is broken or
  unavailable on the host.
- both fail: this is a general outbound HTTPS/DNS/firewall problem.
- both work: Telegram outbound transport is probably not the bottleneck.

If IPv6 is broken and the bot still tries IPv6 first on that host, prefer a
host/network fix. As a temporary operator workaround, disable or deprioritize
broken IPv6 at the OS/network layer rather than changing server config blindly.

## Server heartbeat or registration is intermittent

Symptoms:

- server is running locally, but appears/disappears from the in-game browser
- local status and ports look correct, but public registration is inconsistent
- logs mention backend, heartbeat, registration, or connectivity warnings

Check the game service:

```bash
systemctl status armareforger.service --no-pager
sudo journalctl -u armareforger.service -n 200 --no-pager
```

Check that expected UDP ports are listening:

```bash
sudo ss -lunp | grep -E '(:2001|:17777|:19999)\b'
```

Confirm `config.json` networking values:

- `bindPort`
- `publicPort`
- `publicAddress`
- A2S/query port
- RCON port, if enabled

Also check external firewalls:

```bash
sudo ufw status verbose
sudo nft list ruleset
```

Provider firewalls or cloud security groups can block traffic even when UFW is
open. Confirm the hosting-provider panel allows the same UDP ports.

If local service state, config, and firewall rules are correct but registration
is still intermittent, treat it as an upstream or network-path symptom first.
Keep service logs and exact timestamps before changing unrelated armactl code.

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
