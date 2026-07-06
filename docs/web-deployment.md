# Web Dashboard Deployment

This guide covers the supported deployment profiles for the armactl web
dashboard. The dashboard runs on the same VM or server host as `armactl` and
the Arma Reforger Dedicated Server; any public or operator-facing route should
be owned by an outer reverse proxy, firewall, VPN, or gateway.

## Setup

From the armactl checkout on the server host:

```bash
./armactl web
```

The setup flow installs web dependencies, creates runtime config, creates the first owner when needed, installs/enables `armactl-web.service`, starts it, and prints the URL/status summary.

Use the default local binding for the local/same-host profile. Use LAN access
only when you are intentionally placing the dashboard behind a documented
gateway, firewall, VPN, or reverse-proxy profile:

```bash
./armactl web --access lan
```

For scripted local/same-host setup:

```bash
./armactl web --owner owner
```

For scripted gateway-managed VM setup:

```bash
./armactl web --access lan --owner owner
```

The command prompts for the owner password when the first owner does not exist.

## Wrapper And Bootstrap Drift Recovery

The repo-local `./armactl` wrapper deliberately checks the virtualenv dependency
stamp before it runs the CLI. A stale or missing `.venv/.armactl-pyproject.sha256`
means `pyproject.toml` changed, the installed dependency mode is too small for
the requested command, or the runtime cannot import the expected packages.

For web operations, diagnose the checkout from the server shell:

```bash
./scripts/bootstrap.sh --check --web
```

The `--check` mode is read-only: it does not run `apt`, `sudo`, `pip`, create a
virtualenv, or edit stamp files. If it reports refresh needed, use the supported
interactive recovery path:

```bash
./scripts/bootstrap.sh --web
```

Then re-run the normal wrapper command, for example:

```bash
./armactl web service status
```

Do not hand-edit `.venv/.armactl-pyproject.sha256`. A successful supported
bootstrap writes the current `pyproject.toml` hash and requested mode to that
stamp. The normal wrapper also selects `--web` automatically for `./armactl web
...` commands, and `scripts/run-web` exports the same web bootstrap mode.

The installed `armactl-web.service` runs the pinned repo virtualenv directly as
`.venv/bin/python -m armactl web run ...`; it does not invoke the wrapper at
service start. Wrapper drift therefore affects operator CLI/smoke commands, not
the already-rendered systemd `ExecStart` path.

Use `ARMACTL_PYTHON=.venv/bin/python ./armactl ...` only as a temporary,
explicit smoke workaround when the virtualenv is already known good and the
wrapper stamp is stale. It is not the primary production contract and should be
followed by the supported bootstrap refresh above.

## Deployment Profiles

armactl supports two deployment profiles for the web dashboard. The profile is
an operator deployment decision, not automatic gateway detection.

### Local/Same-Host Profile

Use this profile when the browser, SSH tunnel, or reverse proxy reaches
`armactl-web` on the same host where the web process runs.

Expected runtime shape:

```text
browser or same-host proxy -> 127.0.0.1:8765 -> armactl-web
```

`web.env` should normally contain:

```dotenv
ARMACTL_WEB_BIND_HOST=127.0.0.1
ARMACTL_WEB_BIND_PORT=8765
ARMACTL_WEB_HTTPS_REQUIRED=false
```

Set `ARMACTL_WEB_HTTPS_REQUIRED=true` only when the browser accesses the
dashboard over HTTPS, usually because a same-host reverse proxy terminates TLS
and proxies to `127.0.0.1:8765`. This setting controls the session cookie
`Secure` flag. It does not configure TLS, a reverse proxy, firewall rules, or
gateway port mappings.

### Gateway-Managed VM Profile

Use this profile when a separate gateway or Proxmox host listens on external
operator ports and proxies traffic into a VM that runs `armactl-web`.

Expected runtime shape:

```text
browser/operator network -> gateway external port -> VM_IP:8765 -> armactl-web
```

In this profile, the VM web runtime still uses the internal default port
`8765`. The external gateway ports are separate from the VM bind port. The VM
bind host must be reachable from the gateway, so it may be the VM LAN IP or
`0.0.0.0`:

```dotenv
ARMACTL_WEB_BIND_HOST=<vm-lan-ip-or-0.0.0.0>
ARMACTL_WEB_BIND_PORT=8765
ARMACTL_WEB_HTTPS_REQUIRED=false
```

Current private deployment example, without credentials:

- Proxmox/deus-gateway external `8766` proxies to Serhiivka
  `192.168.1.5:8765`.
- Proxmox/deus-gateway external `8767` proxies to Chervonopilya
  `192.168.1.7:8765`.
- The VM web runtime port remains `8765`; do not change it to `8766` or
  `8767` unless the gateway upstream is changed at the same time.

`ARMACTL_WEB_HTTPS_REQUIRED=false` is acceptable only when TLS/HTTPS is not
terminated in the armactl web process and access is protected by a real outer
layer such as gateway ACLs, firewall rules, VPN, or an identity-aware proxy. If
the browser reaches the dashboard over HTTPS, even with TLS terminated at the
gateway and HTTP used only from gateway to VM, set
`ARMACTL_WEB_HTTPS_REQUIRED=true` so session cookies are marked `Secure`.

The warning `External bind without HTTPS-required cookies` can be expected in
the gateway-managed VM profile when the VM must bind beyond localhost and the
browser path is intentionally non-HTTPS behind gateway/firewall/VPN controls.
Treat it as a required operator check, not as noise to hide. The warning should
remain visible unless an explicit operator-controlled config/profile is added in
the future.

## Local Smoke Check

Check the service:

```bash
./armactl web service status
```

Check the health endpoint on the server host:

```bash
curl http://127.0.0.1:8765/healthz
```

For a gateway-managed VM that binds to a specific VM LAN IP instead of
`0.0.0.0`, check the VM LAN address or the protected gateway route instead.

Expected response:

```json
{"ok":true}
```

Then open the login page in a browser and verify:

1. wrong password is rejected without a traceback;
2. correct owner password opens the dashboard;
3. dashboard status renders;
4. logout redirects protected pages back to login.

## Service Commands

```bash
./armactl web service restart
./armactl web service status
./armactl web service stop
./armactl web service start
```

For logs:

```bash
sudo journalctl -u armactl-web.service -n 200 --no-pager
```

## SSH Tunnel For Remote Browser Testing

Before exposing the dashboard on a network, use an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 operator@example-server
```

Then open:

```text
http://127.0.0.1:8765/login
```

## Reverse Proxy And HTTPS

For any public or semi-public access, put the dashboard behind HTTPS and
restrict access with firewall, VPN, gateway ACLs, or reverse-proxy rules.

Recommended shape:

```text
browser -> HTTPS reverse proxy -> armactl-web on the server host
```

Gateway-managed VM shape:

```text
browser/operator network -> protected gateway/nginx port -> VM_IP:8765
```

Same-host Caddy example:

```caddyfile
server-1.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Nginx example:

```nginx
server {
    listen 443 ssl http2;
    server_name server-1.example.com;

    ssl_certificate /etc/letsencrypt/live/server-1.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/server-1.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

When the browser reaches the dashboard through HTTPS, configure secure cookies:

```bash
./armactl web init --https-required
./armactl web service restart
```

Do not leave direct unauthenticated network exposure to `armactl-web`. Keep
CLI/TUI access available as the fallback management path. `HTTPS_REQUIRED`
only controls cookie behavior; gateway mappings and network access controls are
separate deployment responsibilities.

For dashboard exposure cleanup, gateway hardening, and incident response guidance, see [network-hardening-runbook.md](network-hardening-runbook.md).

## Ports

Default VM web bind port: `8765/TCP`.

Gateway-managed deployments may expose different external operator ports on the
gateway, such as `8766` or `8767`, but those ports should proxy to the VM
runtime port `8765` unless the VM runtime config and gateway upstream are
changed together.

Do not reuse default Arma service ports for the dashboard:

- Arma game: `2001/UDP`
- Steam A2S: `17777/UDP`
- RCON: `19999`
- SSH: `22/TCP`
- Reverse proxy public HTTP/HTTPS: `80/TCP`, `443/TCP`

## Troubleshooting

### Service Installed But Not Started

```bash
./armactl web service start
./armactl web service status
```

### Owner Is Not Configured

```bash
./armactl web init --owner owner
```

### Login Is Blocked By Rate Limiting

Wait for the cooldown, then retry with the correct username and password. The error intentionally does not reveal whether a username exists.

### Dashboard Says No Server Found

The dashboard is running, but discovery did not find an installed Arma server for the selected instance. Use CLI/TUI fallback paths:

```bash
./armactl detect
./armactl status
```

### External Bind Warning Appears

If external binding is intentional, confirm which deployment profile owns it.

For the local/same-host profile, bind back to localhost:

```bash
./armactl web init --host 127.0.0.1 --no-https-required
./armactl web service restart
```

For the gateway-managed VM profile, the warning can be expected only when the
gateway, firewall, VPN, or identity-aware proxy is the documented protection
layer and the gateway upstream points to the VM `:8765`. Do not "fix" this by
changing the VM bind host to `127.0.0.1`; a separate gateway will no longer be
able to reach the VM web process. If the browser path uses HTTPS, set
`ARMACTL_WEB_HTTPS_REQUIRED=true`.

## Safe Operator Checklist

Check `web.env` without printing secrets:

```bash
grep -E '^(ARMACTL_WEB_BIND_HOST|ARMACTL_WEB_BIND_PORT|ARMACTL_WEB_HTTPS_REQUIRED)=' /path/to/web.env
```

For local/same-host:

- expect `ARMACTL_WEB_BIND_HOST=127.0.0.1`;
- expect `ARMACTL_WEB_BIND_PORT=8765`;
- set `ARMACTL_WEB_HTTPS_REQUIRED=true` only when browser access is HTTPS.

For gateway-managed VM:

- expect `ARMACTL_WEB_BIND_HOST=<vm-lan-ip>` or `0.0.0.0`;
- expect `ARMACTL_WEB_BIND_PORT=8765`;
- confirm the gateway upstream still targets `VM_IP:8765`;
- keep external gateway ports such as `8766` and `8767` out of `web.env`;
- do not change the VM bind host back to `127.0.0.1` unless the reverse proxy
  runs on the same VM;
- set `ARMACTL_WEB_HTTPS_REQUIRED=true` when the browser reaches the dashboard
  over HTTPS, and leave it `false` only when the non-HTTPS browser path is
  protected by gateway/firewall/VPN or another outer layer.

Before changing gateway-managed routing, verify the gateway mapping in the live
gateway config and smoke the affected route. Do not change production gateway
rules, service restarts, or deployment state from this repository documentation
slice.
