# Web Dashboard Deployment

This guide covers local and LAN setup for the armactl web dashboard. The dashboard runs on the same server host as `armactl` and the Arma Reforger Dedicated Server.

## Setup

From the armactl checkout on the server host:

```bash
./armactl web
```

The setup flow installs web dependencies, creates runtime config, creates the first owner when needed, installs/enables `armactl-web.service`, starts it, and prints the URL/status summary.

Use the default local binding unless you are ready to expose the dashboard on the LAN:

```bash
./armactl web --access lan
```

For scripted setup:

```bash
./armactl web --access lan --owner owner
```

The command prompts for the owner password when the first owner does not exist.

## Local Smoke Check

Check the service:

```bash
./armactl web service status
```

Check the health endpoint on the server host:

```bash
curl http://127.0.0.1:8765/healthz
```

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

For any public or semi-public access, put the dashboard behind HTTPS and restrict access with firewall, VPN, or reverse-proxy rules.

Recommended shape:

```text
browser -> HTTPS reverse proxy -> armactl-web on the server host
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

When serving through HTTPS, configure secure cookies:

```bash
./armactl web init --https-required
./armactl web service restart
```

Do not leave direct unauthenticated network exposure to `armactl-web`. Keep CLI/TUI access available as the fallback management path.

## Ports

Default web port: `8765/TCP`.

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

If external binding is intentional, put the dashboard behind HTTPS and network access rules, then enable HTTPS-required cookies. For local-only operation, bind back to localhost:

```bash
./armactl web init --host 127.0.0.1 --no-https-required
./armactl web service restart
```
