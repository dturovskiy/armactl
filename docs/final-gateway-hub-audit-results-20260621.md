# Final gateway / hub deviation audit - 2026-06-21

Scope: `feat/web-interface` after the temporary multi-VM `deus-gateway`
deployment deviation and the future hub/product-layer documentation note.

## Result

Blockers: none.

Should-fix before returning to the main web feature plan: none found in this
audit scope.

Readiness: ready to return to the main web feature plan. Keep `deus-gateway`
work separate from armactl core, and keep any future hub/product-layer work as a
separate design/implementation track.

## Verified

- `deus-gateway`, the temporary public IP/port map, and future hostname examples
  are documented only in deployment/planning docs.
- No Proxmox/gateway-specific names or public/private VM addresses were found
  in `src` or runtime tests.
- `8766`/`8767` references in tests are generic web bind-port coverage, not
  hardcoded production VM routing.
- Cookie isolation is implemented through per-runtime
  `ARMACTL_WEB_COOKIE_NAMESPACE`; docs warn not to copy it between VMs.
- The future hub direction is documented as a product/control layer for central
  login, instance selection, organizations, plans, and entitlements.
- VM-local `armactl-web` remains the owner of server actions, config, mods,
  files, logs, jobs, audit, and emergency recovery.
- Gateway/hub logic has not been introduced into armactl web routes, page
  models, services, or adapters.
- Existing platform-adapter debt remains future work and was not worsened by
  this deployment documentation.

## Commands Run

```text
git status -sb
```

Result before this audit report: only the expected docs files modified.

```text
rg -n "deus-gateway|dashboard\\.<domain>|serhiivka\\.<domain>|chervonopilya\\.<domain>|tryzub\\.<domain>|8766|8767|178\\.158\\.196\\.136|192\\.168\\.1\\.|product/control hub|product layer|signed code|signed token" src tests docs README.md
```

Result: gateway/product-layer terms appear in docs; `8766`/`8767` also appear in
generic web CLI/runtime tests. No runtime code leak found.

```text
rg -n "deus-gateway|178\\.158\\.196\\.136|192\\.168\\.1\\.|serhiivka|chervonopilya|tryzub" src tests
```

Result: no matches.

```text
rg -n "ARMACTL_WEB_COOKIE_NAMESPACE|cookie_namespace|session_cookie|csrf_cookie|login_csrf|cookie_name|Host header|X-Forwarded|trusted proxy" src/armactl/web tests docs/web-deployment.md
```

Result: cookie namespace runtime and tests are present; docs cover Host header
and trusted-proxy/IP allowlist future work.

```text
rg -n "service_manager|systemctl|subprocess|os\\.system|Popen|shell=True|route global|__globals__|endpoint.__globals__|monkeypatch.*routes|TODO|FIXME|hack|workaround" src/armactl/web tests docs
```

Result: reviewed. Hits are expected compatibility/documentation/test references:
web quickstart/service helper, documented adapter debt, import-safety tests, and
historical audit docs. No new gateway/hub shortcut found.

```text
.venv/bin/ruff check src/armactl/web tests
```

Result: all checks passed.

```text
git diff --check
```

Result: clean.

```text
ulimit -n 4096 && .venv/bin/python -m pytest -q
```

Result: `744 passed in 114.25s`.

## Return-To-Plan Recommendation

Resume the main web feature plan with the existing priority list:

1. Server update flow: version check/read model, no-op "already up to date",
   background update job, audit/progress, explicit running-server policy.
2. Config schema inventory: safe controls for real Arma keys, including
   third-person/crossplay only after schema verification.
3. Player history and banlist: reliable identity only, no IP storage by
   default, audit/confirmation/backups.
4. Schedule timezone UX: browser-local input with IANA timezone, backend UTC
   normalization, local + UTC display.
5. Users/security foundation: system users, roles, permissions, policy gates,
   recovery, allowlist/trusted proxy, later hub handoff.

Do not start central hub/product-layer implementation until the per-VM web
feature plan has a stable merge target or a separate hub design slice is opened.
