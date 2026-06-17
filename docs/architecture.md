# Архітектура armactl

## Принцип розділення

В armactl чітко розділені чотири типи файлів:

| Тип | Що | Де живе |
|-----|----|---------|
| **Source code** | Код тулзи, шаблони, тести | GitHub-репо `armactl/` |
| **Runtime data** | Бінарники сервера, конфіг, бекапи, state | `~/armactl-data/<instance>/` |
| **Web runtime data** | Planned web settings, accounts, sessions | `~/armactl-data/web/` |
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
│   ├── run-web                 # planned local web smoke launcher
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
│       ├── ports.py
│       ├── repair.py
│       ├── service_manager.py
│       ├── state.py
│       ├── web/                 # planned browser management panel
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
| `src/armactl/tui/` | TUI-оболонка (Textual), жодної бізнес-логіки |
| `src/armactl/web/` | Planned web-panel routes, templates, static assets, жодної бізнес-логіки |
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
| `service_manager.py` | Генерація та керування systemd service/timer, статус і розклад |
| `installer.py` | Install flow: SteamCMD + config + service |
| `repair.py` | Відновлення зламаної інсталяції |
| `mods.py` | Базові операції над списком модів у `config.json` |
| `mods_manager.py` | Вищорівневе керування модами й mod pack import/export |
| `cleaner.py` | Аналіз і прибирання старих логів, dump-файлів і backup-ів |
| `i18n.py` | Локалізація UI та backend-повідомлень |
| `logs.py` | Читання journalctl логів |
| `metrics.py` | Runtime метрики сервера: CPU/RAM, PID-level state, Server FPS/frame-time telemetry |
| `ports.py` | Перевірка listening портів (ss) |
| `web/` | Planned ASGI web adapter over backend modules |

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

### Planned web panel runtime

Web-panel state is not stored in the repository and not stored inside a game
instance. It belongs to the armactl management layer:

```text
~/armactl-data/web/
├── web.env                         # local web runtime settings
└── web.db                          # users, roles, sessions, jobs
```

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
file access.

---

## 3. Системні файли (поза instance root)

Для автозапуску після reboot та планових рестартів використовуються systemd unit-файли:

```text
/etc/systemd/system/armareforger.service
/etc/systemd/system/armareforger-restart.service
/etc/systemd/system/armareforger-restart.timer
/etc/systemd/system/armactl-web.service      # planned web panel service
```

### Зв'язок service → instance root

Service посилається на конкретний інстанс:

```ini
[Service]
WorkingDirectory=/home/<user>/armactl-data/default/server
ExecStart=/home/<user>/armactl-data/default/start-armareforger.sh
```

Ці unit-файли генеруються з шаблонів у `templates/` під час `armactl install` або `armactl service install`.

---

## 4. Архітектурний принцип

```text
┌─────────────────────────────────────┐
│ CLI / TUI / Telegram / Web (planned)│  ← Адаптери, жодної бізнес-логіки
├─────────────────────────────────────┤
│          Backend modules            │  ← Уся бізнес-логіка / internal API
├──────────┬──────────┬───────────────┤
│ discovery│ config   │ service/timer │  ← Модулі
│ state    │ mods     │ installer     │
│ logs     │ ports    │ repair        │
├──────────┴──────────┴───────────────┤
│          systemd / filesystem       │  ← Системний рівень
└─────────────────────────────────────┘
```

### Правила

1. **TUI не містить бізнес-логіки** — викликає reusable backend-модулі, а не реалізує логіку в екранах
2. **Web route handlers не містять бізнес-логіки** — planned web panel має бути тонким адаптером над тими самими backend-модулями
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
and pending operator work is separate from background jobs.

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

### Planned web dashboard flow

```text
browser
  → HTTPS reverse proxy
  → armactl-web on 127.0.0.1:8765 inside the game VM
  → backend modules
  → state/config/systemd/filesystem
```

The web service should run beside the game server in the same VM. On Proxmox,
multiple game VMs can each use the same local web port because each VM has its
own network namespace. Public exposure should be handled by a reverse proxy with
one subdomain or route per VM.
