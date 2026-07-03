# Troubleshooting

This page covers common operational problems during bootstrap, install, repair, and day-to-day server management.

## Bootstrap Or First Launch Fails

Symptoms:

- `./armactl` does not start
- repo-local `.venv` is missing or incomplete
- dependency installation fails

Try:

```bash
./armactl
./scripts/run-host-tests
```

If package downloads or Python package installation fail, fix the host/network issue first and rerun the launcher.

## SteamCMD Install Or Download Fails

Symptoms:

- install fails during the SteamCMD step
- logs show SteamCMD errors or timeouts

Check:

- SteamCMD is installed and reachable
- the host has outbound network access
- enough disk space is available under `~/armactl-data/<instance>/server`

`armactl` streams SteamCMD output into the TUI log so long downloads should show live lines instead of a silent hang.

## Telegram Bot Cannot Reach Telegram API

Symptoms:

- Telegram buttons stop responding or respond intermittently
- bot logs show `NetworkError`, `TimedOut`, `ReadError`, or `ConnectError`

Check bot logs:

```bash
sudo journalctl -u armactl-bot.service -n 200 --no-pager
```

Check outbound HTTPS connectivity:

```bash
curl -4 -sS -o /dev/null \
  --connect-timeout 3 \
  --max-time 6 \
  -w 'code=%{http_code} connect=%{time_connect}s tls=%{time_appconnect}s total=%{time_total}s ip=%{remote_ip}\n' \
  https://api.telegram.org/
```

If this fails, fix the host, provider firewall, DNS, proxy, or outbound network path before debugging bot code.

## IPv6 Vs IPv4 Outbound Diagnostics

Some hosts have broken IPv6 routing. Compare IPv4 and IPv6 explicitly:

```bash
curl -4 https://api.telegram.org
curl -6 https://api.telegram.org
```

If IPv4 works and IPv6 fails, prefer a host/network fix. Avoid changing server config blindly.

## Server Heartbeat Or Registration Is Intermittent

Symptoms:

- server is running locally but appears/disappears from the in-game browser
- local status and ports look correct but public registration is inconsistent
- logs mention backend, heartbeat, registration, or connectivity warnings

Check the game service:

```bash
systemctl status armareforger.service --no-pager
sudo journalctl -u armareforger.service -n 200 --no-pager
```

Check expected UDP ports:

```bash
sudo ss -lunp | grep -E '(:2001|:17777|:19999)\b'
```

Confirm `config.json` networking values:

- `bindPort`
- `publicPort`
- `publicAddress`
- A2S/query port
- RCON port, if enabled

Also check external firewall rules:

```bash
sudo ufw status verbose
sudo nft list ruleset
```

## Server Is Visible In Game But Unreachable

The server may have registered, but clients cannot reach the game port.

Check:

```bash
sudo ss -lunp | grep -E '(:2001|:17777|:19999)\b'
sudo ufw status verbose
```

Then verify `bindPort`, `publicPort`, `publicAddress`, and any provider firewall rules.

## Ports Are Not Listening

Use:

```bash
armactl ports
armactl ports open
```

Or in TUI:

- `Manage Existing Server`
- `Check Ports`

If the service is running but the game port is missing, inspect server logs and `config.json`.

## Secure Privileged Control Is Not Configured

If TUI or the Telegram bot reports that the secure privileged channel is not installed, rerun:

- `Install / Update Bot Service` in TUI, or
- `Repair Installation`

This reinstalls the narrow helper and sudoers drop-in used for non-interactive service actions.

If the problem started after running install/repair from a root shell, rerun it from the regular Linux account that owns the instance.

## Metrics Show As Unknown

Runtime metrics can be unavailable when:

- the service is stopped
- the main PID is gone
- systemd accounting values are missing

Check:

```bash
systemctl show armareforger.service --property=MainPID,MemoryCurrent,CPUUsageNSec
```

## Server FPS Telemetry Is Unavailable Or Stale

Check that the running server process includes `-logStats 10000`:

```bash
pgrep -af ArmaReforgerServer
```

Check telemetry lines:

```bash
grep -RiaE 'FPS:|frame time' ~/armactl-data/default/config/logs | tail -20
```

If `-logStats 10000` is missing, regenerate service/start files and restart the server:

```bash
./armactl repair
sudo systemctl restart armareforger.service
./armactl status
```

## Scheduled Restart Hangs Or Leaves A Stale Server Process

Regenerate service files after updating armactl so the installed systemd units include
the bounded restart helper:

```bash
./armactl service install
./armactl schedule set 06:00,18:00
systemctl status armareforger.service armareforger-restart.timer --no-pager
```

The generated restart timer should call the root-owned
`armactl-safe-restart` helper. It requests a non-blocking stop, waits for the
service to become inactive/failed, sends `SIGKILL` to the service control group
if the stop grace is exceeded, starts the service again, and verifies a short
active/running stability window.

Useful logs:

```bash
sudo journalctl -u armareforger-restart.service -n 120 --no-pager
sudo journalctl -u armareforger.service --since "1 hour ago" --no-pager
```

If the game logs show script shutdown exceptions, treat that as a game/mod
shutdown problem. The helper limits the operational fallout, but the script/mod
error still needs separate investigation.

## Disabled Mods Still Appear In Local Files Or Profile Settings

When armactl disables a Workshop mod, it removes that mod from server-facing
`game.mods` in `config.json` and stores the reversible disabled entry in
`mods-state.json`. The dedicated server should not receive disabled sidecar
entries through `config.json`.

Local addon directories may remain under the managed `config/addons` cache.
That is expected: disabled mods can be re-enabled without another full download,
and addon files on disk are informational unless the mod is still listed in
`game.mods` or required by the selected scenario/world.

The `/mods` diagnostics panel reports:

- active `config.json` mod count;
- disabled sidecar count and list;
- overlap between active and disabled IDs;
- installed addon directory count;
- disabled addon directories still present on disk;
- allowlisted profile settings references to known disabled mod module names.

Do not treat disabled addon directories as cleanup candidates by default. The
unused-addon cleanup protects active and disabled IDs, and it must not delete
disabled addon files unless the operator explicitly removes that disabled mod.

If diagnostics show stale profile settings references, use the `/mods` action
`Cleanup stale profile settings references`. This is a narrow POST-only cleanup
for the allowlisted profile settings candidates. It backs up each changed file
under instance backups, removes only exact known disabled-mod module blocks with
balanced braces, audits counts only, and marks restart-pending work when it
changes a file. It does not delete disabled addon directories, remove disabled
mods from the sidecar, change active `game.mods`, or provide a generic profile
or config editor.

If the action reports skipped ambiguous references, no partial block is written
for that ambiguous stanza. Inspect the profile settings file manually before
retrying.

Profile settings residue can explain game log warnings about unknown module
keywords after a mod is disabled. It is not evidence by itself that the mod is
still present in server-facing `game.mods`.

## Existing Service Is Found But Config Or Binary Is Wrong

Use:

- `Detect Existing Server`
- `Repair Installation`

## Web Dashboard Notes

The local browser dashboard is included; CLI, TUI, and Telegram remain reliable fallback management paths.

Keep these layers separate when troubleshooting:

- Arma game/A2S/RCON ports are configured in `config.json`.
- `armactl-web` uses its own local TCP port.
- Public HTTP/HTTPS should be handled by a reverse proxy, not by the game service.
- The public marketing website is separate from the authenticated dashboard.

Current dashboard surfaces include dashboard status, safe config editing, mods, admins, schedule, files, logs/report, jobs, player registry foundation, auth/session/CSRF, action records, and pending operator work.

Useful checks:

```bash
./armactl web service status
curl http://127.0.0.1:8765/healthz
sudo journalctl -u armactl-web.service -n 200 --no-pager
```

See [web-deployment.md](web-deployment.md) for setup and reverse-proxy guidance, and [web-interface-plan.md](web-interface-plan.md) for the public dashboard overview.
