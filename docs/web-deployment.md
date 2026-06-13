# Web panel smoke checks and deployment

This guide covers local and VM smoke checks for the armactl web panel, plus the
current reverse proxy / HTTPS deployment model. The current production model is
source checkout plus the repo-local `.venv`; it is not a wheel-only deployment
flow yet.

The web panel should run inside the same Linux VM that owns the Arma Reforger
server. A public website or reverse proxy may run in another VM/container, but
that proxy must forward to the game VM address, not to `127.0.0.1` on the proxy
host.

## Local smoke checklist

Use a temporary data root so the smoke test does not touch the operator's real
`~/armactl-data` runtime.

```bash
cd /home/deus/projects/armactl
git status -sb
```

Confirm the branch is the expected web branch, then make sure web dependencies
are available. If the repo-local virtualenv is missing or stale, refresh it with
the web bootstrap mode used by this checkout:

```bash
./scripts/bootstrap.sh --web
```

Initialize a local web runtime only:

```bash
.venv/bin/python -m armactl web init --data-root /tmp/armactl-web-smoke
```

Create the initial owner explicitly. The password prompt is hidden and asks for
confirmation:

```bash
.venv/bin/python -m armactl web init \
  --data-root /tmp/armactl-web-smoke \
  --owner owner
```

Run the panel in the foreground:

```bash
.venv/bin/python -m armactl web run \
  --data-root /tmp/armactl-web-smoke \
  --host 127.0.0.1 \
  --port 8765
```

Smoke from another terminal:

```bash
curl http://127.0.0.1:8765/healthz
```

Expected response:

```json
{"ok":true}
```

Then check the browser flow:

1. Open `http://127.0.0.1:8765/login`.
2. Submit a wrong password and confirm it is rejected without a traceback.
3. Submit the correct owner password and confirm the dashboard opens.
4. If no Arma server is installed in the temporary data root, confirm the
   dashboard shows the `not_installed` empty state with only useful minimum
   blocks: instance/lifecycle, host metrics, web runtime, and recent jobs if
   present.
5. Confirm no fake Start/Stop/Restart forms are shown for `not_installed`.
6. Log out and confirm returning to `/dashboard` redirects to `/login`.
7. Stop the foreground runner with `Ctrl-C`.

## VM and systemd smoke checklist

Run these commands on the target game VM from the source checkout that will own
the web service.

```bash
cd ~/projects/armactl
git status -sb
./scripts/bootstrap.sh --web
```

Initialize runtime and owner:

```bash
./armactl web init
./armactl web init --owner owner
```

Install the production unit. Install enables `armactl-web.service` but does not
start it automatically:

```bash
./armactl web service install
./armactl web service status
```

Inspect the systemd unit state, then start it:

```bash
systemctl status armactl-web.service
./armactl web service start
./armactl web service status
systemctl status armactl-web.service
```

Smoke the local listener on the game VM:

```bash
curl http://127.0.0.1:8765/healthz
```

For browser testing before reverse proxy setup, use an SSH tunnel from your
workstation:

```bash
ssh -L 8765:127.0.0.1:8765 operator@GAME_VM
```

Then open `http://127.0.0.1:8765/login` locally. Test wrong password, correct
password, dashboard rendering, logout, and service status output.

Basic lifecycle checks:

```bash
./armactl web service restart
./armactl web service status
./armactl web service stop
./armactl web service status
./armactl web service start
```

Keep CLI/TUI access as the fallback path while web rollout is in progress.

## Reverse proxy and HTTPS deployment

Safe default: keep armactl web bound to `127.0.0.1:8765` when the reverse proxy
runs on the same game VM.

When the proxy runs outside the game VM, such as on the Proxmox host or in a
separate website/proxy container, `127.0.0.1` means the proxy host itself. It is
not the game VM. In that topology, configure armactl web inside the game VM to
bind to the game VM private IP or to `0.0.0.0`, then restrict access with
firewall/proxy source rules.

Example game VM config for an external proxy:

```bash
./armactl web init --host 10.0.0.11 --https-required
./armactl web service restart
```

If a stable private IP is not available, `0.0.0.0` can be used only with
firewall rules that allow the reverse proxy or VPN source and deny direct public
access:

```bash
./armactl web init --host 0.0.0.0 --https-required
./armactl web service restart
```

`ARMACTL_WEB_HTTPS_REQUIRED=true` makes web cookies use the `Secure` flag. Use
it for public HTTPS deployments. If it is enabled, plain HTTP browser testing
will not send the session cookie; test through HTTPS, an SSH tunnel with the
setting disabled for local-only smoke, or a local reverse proxy.

Do not expose direct `http://GAME_VM:8765` to the internet. Public `80` and
`443` should belong to the reverse proxy, not to `armactl-web` directly.

### Caddy examples

Same VM proxy:

```caddyfile
server-1.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Proxy in another VM/container:

```caddyfile
server-1.example.com {
    reverse_proxy 10.0.0.11:8765
}
```

Caddy manages certificates automatically when DNS and public ingress are set up
correctly. Keep firewall rules limited to the proxy ingress and the private
upstream path.

### Nginx example

```nginx
server {
    listen 443 ssl http2;
    server_name server-1.example.com;

    ssl_certificate /etc/letsencrypt/live/server-1.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/server-1.example.com/privkey.pem;

    location / {
        proxy_pass http://10.0.0.11:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

The current app-level login throttle uses `request.client.host` and does not
trust `X-Forwarded-For`. App-level IP allowlist and trusted proxy handling are
future work. Prefer firewall and proxy allow rules now.

## Ports

Default web port: `8765/TCP`.

Do not reuse the default Arma service ports for the web panel:

- Arma game: `2001/UDP`
- Steam A2S: `17777/UDP`
- RCON: `19999`
- SSH: `22/TCP`
- Reverse proxy public HTTP/HTTPS: `80/TCP`, `443/TCP`

`80` and `443` belong to the reverse proxy. Use them for direct armactl-web only
when deliberately running without a separate proxy, which is not the recommended
production model.

## Troubleshooting

### Service installed but not started

`armactl web service install` enables the service but intentionally does not
start it. Start it explicitly:

```bash
./armactl web service start
./armactl web service status
```

### Owner is not configured

`armactl web init` is runtime-only unless an owner is explicitly requested. Add
the first owner with:

```bash
./armactl web init --owner owner
```

### Login is blocked by rate limiting

The web login throttle locks a client IP + normalized username pair after
repeated failures. Wait for the 15 minute cooldown, then retry with the correct
password. The error intentionally does not reveal whether a username exists.

### Dashboard says no server found

The web panel is running, but discovery did not find an installed Arma server
for the current `default` instance. Use CLI/TUI fallback paths to detect,
install, or repair the server:

```bash
./armactl detect
./armactl status
```

Web install/repair flows are planned for future background jobs and are not
available from the dashboard yet.

### External bind warning appears

The panel is bound to `0.0.0.0`, `::`, a non-loopback IP, or an unknown host. If
this is intentional, put the panel behind HTTPS, VPN, and firewall/proxy allow
rules, and set HTTPS-required cookies for public HTTPS deployments:

```bash
./armactl web init --https-required
./armactl web service restart
```

For local-only operation, bind back to localhost:

```bash
./armactl web init --host 127.0.0.1 --no-https-required
./armactl web service restart
```

### Proxy cannot connect

Check where the proxy runs. If it is not on the game VM, `127.0.0.1:8765` points
to the proxy machine/container, not the game VM. Either move the proxy onto the
game VM, or bind armactl web to the game VM private IP and point the proxy
upstream at that IP.

Also check VM firewall rules and confirm the service is actually listening:

```bash
./armactl web service status
ss -ltnp | grep 8765
```
