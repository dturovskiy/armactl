# Архітектура armactl

## Принцип розділення

В armactl чітко розділені кілька типів файлів:

| Тип | Що | Де живе |
|-----|----|---------|
| **Source code** | Код тулзи, шаблони, тести | GitHub-репо `armactl/` |
| **Runtime data** | Бінарники сервера, конфіг, бекапи, state | `~/armactl-data/<instance>/` |
| **Web runtime data** | Web settings, users, sessions, jobs, pending work | `~/armactl-data/web/` |
| **Player registry data** | Instance-scoped reliable player registry | `~/armactl-data/<instance>/players.db` |
| **armactl logs** | Centralized armactl-owned logs and audit files | `~/armactl-data/logs/` |
| **System services** | systemd unit-файли для автозапуску | `/etc/systemd/system/` |

Змішувати ці шари не можна — це різні lifecycle, різні власники, різні правила оновлення.

---

## 1. Структура репозиторію

```text
armactl/
├── README.md
├── LICENSE
├── pyproject.toml
├── .gitignore
├── armactl
├── docs/
│   ├── architecture.md
│   ├── localization.md
│   ├── telegram-bot.md
│   └── ...
├── scripts/
│   ├── bootstrap.sh
│   ├── run-host-tests
│   ├── run-tui
│   ├── run-web                 # local web smoke launcher
│   └── ...
├── templates/
│   ├── config.json.j2
│   ├── start-armareforger.sh.j2
│   ├── armareforger.service.j2
│   ├── armareforger-restart.service.j2
│   └── armareforger-restart.timer.j2
├── src/
│   └── armactl/
│       ├── __init__.py
│       ├── cleaner.py
│       ├── cli.py
│       ├── config_manager.py
│       ├── discovery.py
│       ├── i18n.py
│       ├── installer.py
│       ├── logs.py
│       ├── mods.py
│       ├── mods_manager.py
│       ├── paths.py
│       ├── platform/
│       │   └── service_adapter.py
│       ├── ports.py
│       ├── repair.py
│       ├── service_manager.py
│       ├── state.py
│       ├── web/                 # browser management panel package
│       │   ├── auth/
│       │   ├── jobs/
│       │   ├── page_models/
│       │   ├── routes/
│       │   ├── runtime/
│       │   ├── services/
│       │   ├── templates/
│       │   └── static/
│       └── tui/
│           ├── app.py
│           └── screens.py
├── website/                      # static marketing site, not the panel
└── tests/
    ├── test_config_manager.py
    ├── test_discovery.py
    ├── test_i18n.py
    ├── test_mods.py
    ├── test_paths.py
    ├── test_service_manager.py
    └── test_state.py
```

### Що де

| Директорія | Призначення |
|------------|-------------|
| `src/armactl/` | Увесь код: CLI, discovery, config, mods, TUI |
| `src/armactl/platform/` | Platform adapter boundaries for service/timer and future OS-specific backends |
| `src/armactl/tui/` | TUI-оболонка (Textual), жодної бізнес-логіки |
| `src/armactl/web/` | Web-panel routes, page models, services, auth, jobs, templates, static assets; routes are HTTP glue |
| `website/` | Static marketing site, окремо від authenticated management panel |
| `templates/` | Jinja2-шаблони для config, service, timer, start script |
| `scripts/` | Зручні launcher-и та dev-скрипти |
| `docs/` | Документація проєкту |
| `tests/` | Unit та integration тести |

### Ключові модулі

| Модуль | Відповідальність |
|--------|------------------|
| `cli.py` | Точка входу, парсер команд |
| `paths.py` | Визначення та валідація шляхів інстансу |
| `discovery.py` | Пошук існуючого сервера |
| `state.py` | Читання/запис `state.json` |
| `config_manager.py` | Безпечне редагування `config.json` |
| `platform/service_adapter.py` | Adapter contract for service/timer operations; default backend is Linux/systemd |
| `service_manager.py` | Linux/systemd implementation for service/timer generation, status, control, and schedule |
| `installer.py` | Install flow: SteamCMD + config + service |
| `repair.py` | Відновлення зламаної інсталяції |
| `mods.py` | Базові операції над списком модів у `config.json` |
| `mods_manager.py` | Вищорівневе керування модами й mod pack import/export |
| `cleaner.py` | Аналіз і прибирання старих логів, dump-файлів і backup-ів |
| `i18n.py` | Локалізація UI та backend-повідомлень |
| `logs.py` | Читання journalctl логів |
| `metrics.py` | Runtime метрики сервера: CPU/RAM, PID-level state, Server FPS/frame-time telemetry |
| `ports.py` | Перевірка listening портів (ss) |
| `web/` | ASGI web adapter over backend modules; routes enforce explicit permissions and do not authorize dangerous features from product tier names alone |

---

## 2. Структура runtime-даних на сервері

Це **не репозиторій**, а те, що створюється на машині користувача під час `armactl install` або `armactl detect`.

### Логічний root інстансу

```text
~/armactl-data/default/
```

### Структура всередині

```text
~/armactl-data/default/
├── server/                          # SteamCMD install dir
│   ├── ArmaReforgerServer           # бінарник сервера
│   ├── addons/
│   ├── battleye/
│   └── steamapps/
├── bot/
│   └── .env                         # optional Telegram bot config source of truth
├── config/
│   └── config.json                  # конфіг сервера
├── backups/                         # автоматичні backup-и перед змінами
├── admins-state.json                # локальні labels/source metadata для game.admins
├── sat-admin-uuid-map.json          # optional Steam/name -> SAT UUID map
├── players.db                       # web player registry foundation for reliable IDs
├── state.json                       # стан інстансу для discovery
└── start-armareforger.sh            # launch script
```

### Що де лежить

| Шлях | Призначення |
|------|-------------|
| `server/` | SteamCMD install dir — сам Arma Reforger Dedicated Server |
| `config/config.json` | Конфіг сервера (редагується через `armactl config`) |
| `admins-state.json` | armactl metadata для labels/source офіційних `game.admins` |
| `sat-admin-uuid-map.json` | Optional map для ServerAdminTools UUID, коли `game.admins` містить SteamID64 |
| `players.db` | Instance-scoped web player registry foundation: reliable IDs, nickname history, first/last seen, seen count; no IP storage by default |
| `bot/.env` | Optional Telegram bot config; те саме джерело правди для TUI і ручного редагування |
| `backups/` | Резервні копії конфігу перед кожною зміною |
| `state.json` | Discovery/state файл armactl |
| `start-armareforger.sh` | Стартовий скрипт, на який посилається systemd service; запускає SAT admin guard перед сервером |

`sat-admin-uuid-map.json` is only needed when an official server admin is stored
as a SteamID64 or label but ServerAdminTools needs its own UUID. Keep
`config/config.json` and `admins-state.json` as the source of truth for official
admins; the SAT map is a narrow conversion layer:

```json
{
  "version": 1,
  "identities": {
    "Bublik": "21761a7f-c9b4-4bff-8375-b4b43abb95ec"
  }
}
```

### Multi-instance (майбутнє)

Модель `~/armactl-data/<instance>/` готова до розширення:

```text
~/armactl-data/
├── default/       # перший інстанс
├── training/      # другий інстанс
└── events/        # третій інстанс
```

Кожен інстанс — повністю ізольований, зі своїм конфігом, бекапами і state.

### Web panel runtime on `feat/web-interface`

Web-panel state is not stored in the repository and not stored inside a game
instance. It belongs to the armactl management layer:

```text
~/armactl-data/web/
├── web.env                         # local web runtime settings
└── web.db                          # users, sessions, CSRF, jobs, pending work
```

`web.db` schema changes are applied through the web runtime migration runner
and recorded in `web_schema_meta.schema_version`. Instance player registry
changes are applied through the player registry migration runner in
`<instance>/players.db` and recorded in
`player_registry_schema_meta.schema_version`. Keep these DB files private
(`0600`) and add future tables such as roles, settings, bans, sessions, or
activity history through explicit idempotent migrations.

Do not store Steam credentials, SteamCMD secrets, or other server-update secrets
in `web.db`. Future update/version state may store safe metadata such as
installed build, last check time, check status, and non-secret result details,
but credentials belong outside the web runtime database.

armactl-owned logs have a centralized root with separate files/directories per
subsystem:

```text
~/armactl-data/logs/
├── host-tests/<instance>/           # saved host-test output
├── instances/<instance>/            # instance-scoped armactl task logs
└── web/
    ├── audit.log                    # append-only audit trail for web actions
    └── runtime.log                  # optional file diagnostics beyond journal
```

The web file manager should start with the VM-local game server install root as
its allowed write area:

```text
~/armactl-data/<instance>/server/
```

Absolute paths, symlink traversal outside allowed roots, source repository
paths, `.git`, `.venv`, and system unit directories are out of scope for browser
file access. Future browser file edit/delete/overwrite flows require POST+CSRF,
explicit confirmation for destructive actions, audit, path jail checks, no
symlink escape, and backup/quarantine or rollback where practical.

---

## 3. Системні файли (поза instance root)

Для автозапуску після reboot та планових рестартів використовуються systemd unit-файли:

```text
/etc/systemd/system/armareforger.service
/etc/systemd/system/armareforger-restart.service
/etc/systemd/system/armareforger-restart.timer
/etc/systemd/system/armactl-web.service      # web panel service
```

### Зв'язок service → instance root

Service посилається на конкретний інстанс:

```ini
[Service]
WorkingDirectory=/home/<user>/armactl-data/default/server
ExecStart=/home/<user>/armactl-data/default/start-armareforger.sh
```

Ці unit-файли генеруються з шаблонів у `templates/` під час `armactl install` або `armactl service install`.

Web service and restart-timer actions call `platform/service_adapter.py`
instead of reaching into `service_manager.py` directly. The default adapter is
the existing Linux/systemd backend, so `armareforger.service`,
`armareforger-restart.service`, and `armareforger-restart.timer` naming and
behavior stay unchanged. CLI/TUI direct `service_manager` imports remain a
compatibility path for now and can move to the adapter in later safe slices.

---

## 4. Архітектурний принцип

```text
┌─────────────────────────────────────┐
│ CLI / TUI / Telegram / Web          │  ← Адаптери, жодної бізнес-логіки
├─────────────────────────────────────┤
│          Backend modules            │  ← Уся бізнес-логіка / internal API
├──────────┬──────────┬───────────────┤
│ discovery│ config   │ service/timer │  ← Модулі
│ state    │ mods     │ installer     │
│ logs     │ ports    │ repair        │
├──────────┴──────────┴───────────────┤
│ platform adapters / systemd / files │  ← Системний рівень
└─────────────────────────────────────┘
```

### Правила

1. **TUI не містить бізнес-логіки** — викликає reusable backend-модулі, а не реалізує логіку в екранах
2. **Web route handlers не містять бізнес-логіки** — web panel має бути тонким адаптером над тими самими backend-модулями
3. **Internal API — це Python backend-модулі** — CLI/TUI/Telegram/web мають викликати їх напряму, а не використовувати TUI або CLI як API
4. **CLI — стабільна точка входу для адміністрування** — але це адаптер над backend-модулями; core-логіка працює і без CLI/TUI/web
5. **Модулі незалежні** — discovery не знає про TUI, config manager не знає про installer
6. **Templates → generated files** — конфіги та unit-файли генеруються з Jinja2-шаблонів
7. **Backup before write** — будь-яка зміна конфігу створює backup
8. **Marketing site is separate** — top-level `website/` не є authenticated management panel

For the web panel, `docs/web-interface-plan.md` is the detailed architecture
guardrail document. In short: routes stay thin, facades/views build DTOs,
services own web workflows, existing backend modules remain the source of truth,
mutating actions require auth/permission/CSRF/audit/backup where applicable,
and pending operator work is separate from background jobs. Pending work stacks
by category and clears only after a successful relevant action, while `/jobs`
keeps pending work and background jobs in separate sections. Route handlers are
HTTP glue only; web service modules own workflow, audit, pending-work, and
rollback/compensation decisions; low-level adapters perform pure filesystem,
systemd, or database operations without route/session knowledge.

Current web package boundaries on `feat/web-interface`:

- `routes/` is HTTP glue: auth/permission/CSRF/form parsing, one service or
  page-model call, and rendering.
- `services/` owns workflow, audit, pending-work updates, mutation
  orchestration, and controlled result objects.
- `page_models/` and `views/` own DTO/read-model aggregation for templates and
  status JSON.
- `platform/service_adapter.py` is the service/schedule backend seam used by web
  start/stop/restart, restart-timer, and game-service autostart workflows. The
  default adapter is Linux/systemd through `service_manager`.
- `jobs/` stores background job metadata in `web.db`, deduplicates active jobs
  by kind/instance, starts workers only for newly created jobs, and uses atomic
  worker claim before execution.
- Future server-version/update work should add a backend service/adapter seam
  for installed/latest build detection and update execution. Routes/templates
  must not parse SteamCMD output or contain systemctl/path/process logic.
  Update execution must be a deduplicated background job with audit and
  progress state, not a blocking HTTP request.
- `services/filesystem_*` modules split roots, path safety, listing, preview,
  transfer, URLs, and upload publishing; `services/filesystem.py` is only a
  compatibility re-export.
- Player registry/moderation is split across `player_sources`,
  `player_identity`, `player_registry`, `page_models/players`, and
  `player_actions`. The registry stores reliable IDs and nickname history in
  `<instance>/players.db`; it does not store IP addresses by default.

Current web branch capabilities include dashboard, safe config editing, mods,
admins, restart schedule, files browse/upload/download/preview, logs/report,
jobs/background operations, player registry foundation, auth/session/CSRF,
permissions, audit logging, and pending operator work. These are implemented on
`feat/web-interface`; stable release, deployment validation, and hardening are
still pending.

Windows backend support is future platform architecture, not part of the current
Linux/systemd web MVP. Adding it requires new adapter implementations for
service/log/path/firewall/process/metrics and install/update flow before web
routes should target Windows hosts.

Known future web/platform work is intentionally separate from the implemented
foundation: server version/update read model and update job adapter, versioned
migrations for `web.db` and `players.db`, player refresh failure outcome
auditing, a typed settings registry, feature policy/roles/tiers, broader
platform adapters, banlist management, destructive file workflows, and SAT/mod
runtime settings.

Paid/premium features are not implemented. If product tiers are added, keep
entitlements separate from permissions through a policy/feature-gate layer.
Existing public MIT history cannot be made private retroactively, and
proprietary premium implementation should not be placed in the public MIT repo
without an explicit business/legal repo and licensing decision.

For broad modularity or platform work, use `docs/system-modularity-audit.md` before
implementation. That audit is the source of truth for checking whether CLI, TUI,
web, bot, backend modules, platform adapters, persistence, tests, and docs still
have clean boundaries for adding future features without hidden shortcuts.

---

## 5. Потоки даних

### Server FPS telemetry flow

```text
generated start-armareforger.sh
  → ArmaReforgerServer -logStats 10000
  → config/logs/*/console.log
  → metrics.query_server_fps_metrics()
  → CLI status / TUI status / Telegram metrics
```

`armactl` treats FPS as engine telemetry, not as a derived host metric. If the
latest log line is missing, stale, or malformed, the UI reports the telemetry as
unavailable/stale instead of inventing a value.

### Install flow

```text
armactl install
  → перевірка ОС, sudo, steamcmd
  → створення ~/armactl-data/default/
  → SteamCMD → server/
  → templates → config.json, start.sh, service, timer
  → systemctl daemon-reload + enable
  → smoke check
  → state.json
```

### Web server update flow (future)

```text
dashboard read model
  → version adapter/API reads installed build and latest available build if safe
  → dashboard shows up to date / update available / unknown / check failed

operator confirms update
  → authenticated POST + server:update/jobs:update + CSRF + impact confirmation
  → web service records intent audit
  → enqueue deduplicated background update job in web_jobs
  → adapter-backed update implementation handles SteamCMD/app manifest/server metadata
  → bounded redacted progress/log tails and controlled failure/result
  → outcome audit and job status visible on /jobs
```

The future update action must not be a blocking HTTP request or a direct route
shell-out. It should not run automatically by default. If the game server is
running, require explicit confirmation, optionally stop/drain before update, and
restart only when the operator confirms or the update workflow explicitly owns
restart. Preserve config/state, avoid secrets in logs, and show
rollback/recovery notes where the backend can provide them.

### Detect flow (existing server)

```text
armactl detect
  → шукає state.json
  → шукає ~/armactl-data/
  → парсить systemd unit (ExecStart, WorkingDirectory)
  → fallback: ручний режим
  → записує state.json
```

### Config change flow

```text
armactl config set-name "My Server"
  → читає config.json
  → створює backup у backups/
  → змінює поле
  → валідує JSON
  → atomic write
```

### Web dashboard flow

```text
browser
  → HTTPS reverse proxy
  → armactl-web on 127.0.0.1:8765 inside the game VM
  → backend modules
  → state/config/systemd/filesystem
```

The web service runs beside the game server in the same VM. On Proxmox,
multiple game VMs can each use the same local web port because each VM has its
own network namespace. Public exposure should be handled by a reverse proxy with
one subdomain or route per VM.
