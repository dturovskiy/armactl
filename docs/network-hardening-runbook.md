# Network Hardening And Incident Runbook

This runbook is public-safe guidance for operators who expose an Arma Reforger
server and the local `armactl` dashboard. Keep deployment-specific
credentials and provider contacts in private operator notes. Non-secret private
IP examples may be documented here only when they are intentionally used as
operator deployment truth.

## Exposure Inventory

Before changing firewall, router, or proxy state, collect a read-only inventory
and mark every unknown as a manual check.

Record:

- router or provider NAT/port-forward rules;
- host firewall rules on the gateway, Proxmox host, and each VM;
- listening TCP/UDP sockets on the gateway, Proxmox host, and each VM;
- reverse-proxy routes and upstreams;
- health checks and public status endpoints.

Useful read-only commands:

```bash
ss -lntup
sudo ss -lntup
sudo ufw status verbose
sudo nft list ruleset
sudo iptables -S
sudo ip6tables -S
systemctl status nginx --no-pager
nginx -T
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/public/server-status.json
```

On a Proxmox gateway, keep discovery read-only:

```bash
qm list
qm config <vmid>
qm guest cmd <vmid> network-get-interfaces
ip neigh show
```

Needed public surfaces are normally limited to:

- Arma game UDP port;
- Steam A2S/query UDP port;
- optional RCON only from trusted admin networks;
- public HTTP/HTTPS for the website or status widget;
- SSH only from trusted admin networks;
- VPN or protected gateway routes for the dashboard.

## Dashboard Exposure Plan

The `armactl` dashboard is an authenticated management surface. Do not expose
the VM-local dashboard directly to the internet.

Supported dashboard deployment profiles:

- Local/same-host: `armactl-web` binds to `127.0.0.1:8765`; the browser uses a
  local browser, SSH tunnel, or reverse proxy on the same host.
- Gateway-managed VM: a separate gateway or Proxmox host listens on external
  operator ports and proxies to `VM_IP:8765`; `armactl-web` on the VM binds to
  the VM LAN IP or `0.0.0.0:8765` so the gateway can reach it.

Current private deployment example, without credentials:

- deus-gateway external `8766` proxies to Serhiivka `192.168.1.5:8765`.
- deus-gateway external `8767` proxies to Chervonopilya `192.168.1.7:8765`.
- The VM web runtime port is still `8765`; external gateway ports are not
  stored in VM `web.env`.

`ARMACTL_WEB_HTTPS_REQUIRED` controls only the web session cookie `Secure`
flag. It does not configure TLS, nginx, firewall policy, VPN policy, or gateway
port mappings. Use `true` when the browser reaches the dashboard over HTTPS.
Use `false` only when HTTPS is not part of the browser path and the access path
is still protected by a real outer layer such as gateway ACLs, firewall rules,
VPN, or an identity-aware proxy.

An `External bind without HTTPS-required cookies` warning can be expected for a
gateway-managed VM only when that outer gateway/firewall/VPN profile is real and
documented. Keep the warning visible as an operator check; do not suppress it
automatically.

Operator checklist:

1. Check `web.env` for `ARMACTL_WEB_BIND_HOST`, `ARMACTL_WEB_BIND_PORT`, and
   `ARMACTL_WEB_HTTPS_REQUIRED` without printing secrets.
2. For local/same-host, expect `127.0.0.1:8765`.
3. For gateway-managed VM, expect the VM LAN IP or `0.0.0.0:8765`; do not
   switch back to `127.0.0.1` unless the reverse proxy is on the same VM.
4. Verify the gateway mapping points external operator ports to `VM_IP:8765`.
5. Set `ARMACTL_WEB_HTTPS_REQUIRED=true` when browser access is HTTPS.
6. Keep fallback CLI/TUI access and a rollback note before changing network
   routing.

Temporary protection:

- bind `armactl-web` to localhost for local/same-host, or to the VM LAN IP or
  `0.0.0.0` only for a documented gateway-managed VM profile;
- allow dashboard access only from known operator IPs at the router, provider
  firewall, or gateway;
- put `/login` and authenticated dashboard polling behind nginx rate limits;
- keep `/healthz` reachable only where health checks need it;
- log denies and rate-limit hits separately from application logs.

Normal production protection:

- require VPN, Tailscale, WireGuard, or an identity-aware proxy such as
  Cloudflare Access before dashboard routes;
- publish only the website and `/public/server-status.json` without dashboard
  authentication;
- route each VM dashboard through the gateway by hostname or a protected route;
- avoid direct broad external port ranges for VM dashboards.

## Port Forwarding Cleanup

Avoid keeping a broad dashboard forwarding range when only a few dashboards are
active. A broad range expands the scan surface, makes rate limits and logs
noisier, hides which routes are actually owned, and increases the chance that a
future service is accidentally exposed.

Separate ports by purpose:

- game ports: Arma game UDP and Steam A2S/query UDP;
- admin ports: SSH, VPN, RCON, and dashboard access;
- web ports: public HTTP/HTTPS and public status JSON.

Cleanup should be staged:

1. Capture current router/provider/gateway mappings and active listeners.
2. Confirm the fallback admin path, such as SSH through a trusted IP or VPN.
3. Add the protected gateway route for each dashboard.
4. Smoke-test login, dashboard polling, `/healthz`, and
   `/public/server-status.json`.
5. Remove one unused forward at a time.
6. Recheck active listeners and external reachability after each change.
7. Keep rollback notes for the exact rule that was removed.

Do not remove SSH, VPN, or the only working admin path during cleanup.

## nginx Hardening Plan

Use these as implementation patterns, not as a drop-in config. Apply only after
reviewing the live gateway config and running `nginx -t`.

```nginx
# Return an explicit throttle code instead of nginx's default 503 for limit_req.
limit_req_status 429;

# Prefer throttling login writes/auth attempts. Empty keys are not accounted, so
# ordinary GET /login renders and redirect storms from expired tabs are not
# treated as failed auth attempts.
map $request_method $armactl_login_limit_key {
    default "";
    POST $binary_remote_addr;
}

limit_req_zone $armactl_login_limit_key zone=armactl_login:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=armactl_dashboard_status:10m rate=120r/m;
proxy_cache_path /var/cache/nginx/armactl-status
    levels=1:2
    keys_zone=armactl_public_status:10m
    max_size=32m
    inactive=10m
    use_temp_path=off;

map $request_uri $blocked_probe {
    default 0;
    ~*^/\.env 1;
    ~*^/\.git 1;
    ~*^/wp-admin 1;
    ~*^/wp-login\.php 1;
    ~*^/phpmyadmin 1;
    ~*\.(bak|backup|old|orig|save|sql|tar|tgz|gz|zip|7z)$ 1;
}

server {
    listen 443 ssl http2;
    server_name example-dashboard-host;

    if ($blocked_probe) {
        return 444;
    }

    location = /healthz {
        proxy_pass http://armactl_upstream;
        access_log off;
    }

    location = /login {
        limit_req zone=armactl_login burst=10 nodelay;
        proxy_pass http://armactl_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location = /dashboard/status.json {
        limit_req zone=armactl_dashboard_status burst=60 nodelay;
        proxy_pass http://armactl_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location = /public/server-status.json {
        proxy_cache armactl_public_status;
        proxy_cache_valid 200 5s;
        proxy_cache_valid 503 2s;
        proxy_cache_lock on;
        add_header X-Cache-Status $upstream_cache_status always;
        proxy_pass http://armactl_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }

    location / {
        proxy_pass http://armactl_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Preserve normal authenticated browser flow: session cookies, CSRF cookies,
redirects to `/login`, static assets, and dashboard polling must continue to
work. Do not rate-limit `/healthz` so tightly that health checks flap.

GET `/login` must not be hard-limited in a way that turns many expired tabs or a
redirect storm into `503 Service Temporarily Unavailable`. If the gateway needs a
GET-side limit, keep it much softer than auth-attempt limits and validate it with
multiple already-open dashboard tabs. Prefer putting the stricter limiter on
`POST /login` or equivalent credential-submission/auth-attempt paths. Keep
`/dashboard/status.json` bounded, but size the rate and burst for normal 7-second
polling across a small set of concurrently open tabs.

When nginx logs `limiting requests ... zone "armactl_login"` and returns 429,
treat it as gateway throttling, not an armactl web app outage. Confirm app health
separately with local `/healthz` and upstream logs before restarting services.

## Game DDoS Decision Record

Local firewall rules, Proxmox rules, and nginx can reduce management-surface
noise, but they cannot save the service when an attack saturates the uplink or
targets the public UDP game endpoint directly.

Acceptable decisions:

1. Stay as-is and accept the availability risk.
2. Move public game endpoints to a DDoS-protected game hosting provider.
3. Use a game-specific DDoS tunnel or scrubbing service that supports the
   required UDP traffic.

Cloudflare HTTP protection helps the website and dashboard proxy. It is not a
simple free fix for Arma Reforger UDP game traffic.

## Incident Runbook

During an attack:

- keep SSH, VPN, and provider-console access open;
- record start time, symptoms, affected endpoints, and provider status;
- identify whether HTTP, dashboard, SSH, game UDP, or the whole uplink is
  affected;
- collect read-only evidence before changing rules;
- contact the provider with timestamps, destination IP, destination ports,
  traffic type, and packet-rate or bandwidth graphs if available.

Read-only evidence commands:

```bash
date -Is
uptime
ss -s
ss -lntup
sudo nft list ruleset
sudo ufw status verbose
sudo journalctl -u nginx -n 200 --no-pager
sudo journalctl -u armactl-web.service -n 200 --no-pager
sudo journalctl -u armareforger.service -n 200 --no-pager
sudo tail -n 200 /var/log/nginx/access.log
sudo tail -n 200 /var/log/nginx/error.log
```

Temporary containment candidates, after confirming rollback and admin access:

- disable public dashboard forwards or gateway routes;
- block or rate-limit obvious HTTP scanner paths;
- temporarily close RCON from public networks;
- if the game endpoint is under attack and the provider cannot scrub traffic,
  temporarily close the game UDP forward or move the endpoint.

Do not:

- pay extortion demands;
- make chaotic firewall edits without a rollback path;
- restart unrelated services while collecting evidence;
- close SSH, VPN, or provider-console access;
- assume nginx rules protect the UDP game server.
