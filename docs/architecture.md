# Архітектура armactl

## Принцип розділення

В armactl чітко розділені три типи файлів:

| Тип | Що | Де живе |
|-----|----|---------|
| **Source code** | Код тулзи, шаблони, тести | GitHub-репо `armactl/` |
| **Runtime data** | Бінарники сервера, конфіг, бекапи, state | `~/armactl-data/<instance>/` |
| **System services** | systemd unit-файли для автозапуску | `/etc/systemd/system/` |

Змішувати ці три шари не можна — це різні lifecycle, різні власники, різні правила оновлення.

---

## 1. Структура репозиторію

```text
armactl/
├── README.md
├── LICENSE
├── pyproject.toml
├── .gitignore
├── docs/
│   ├── architecture.md
│   ├── install.md
│   ├── migration.md
│   └── screenshots/
├── scripts/
│   ├── run-tui
│   └── bootstrap-dev.sh
├── templates/
│   ├── config.json.j2
│   ├── start-armareforger.sh.j2
│   ├── armareforger.service.j2
│   ├── armareforger-restart.service.j2
│   └── armareforger-restart.timer.j2
├── src/
│   └── armactl/
│       ├── __init__.py
│       ├── cli.py
│       ├── paths.py
│       ├── discovery.py
│       ├── state.py
│       ├── config_manager.py
│       ├── service_manager.py
│       ├── timer_manager.py
│       ├── installer.py
│       ├── repair.py
│       ├── mods.py
│       ├── logs.py
│       ├── ports.py
│       ├── utils.py
│       └── tui/
│           ├── app.py
│           ├── screens/
│           ├── widgets/
│           └── forms/
└── tests/
    ├── test_discovery.py
    ├── test_config_manager.py
    ├── test_mods.py
    └── test_paths.py
```

### Що де

| Директорія | Призначення |
|------------|-------------|
| `src/armactl/` | Увесь код: CLI, discovery, config, mods, TUI |
| `src/armactl/tui/` | TUI-оболонка (Textual), жодної бізнес-логіки |
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
| `service_manager.py` | Генерація та керування systemd service |
| `timer_manager.py` | Генерація та керування systemd timer |
| `installer.py` | Install flow: SteamCMD + config + service |
| `repair.py` | Відновлення зламаної інсталяції |
| `mods.py` | Керування модами: add/remove/dedupe/import/export |
| `logs.py` | Читання journalctl логів |
| `ports.py` | Перевірка listening портів (ss) |
| `utils.py` | Спільні утиліти |

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
├── config/
│   └── config.json                  # конфіг сервера
├── backups/                         # автоматичні backup-и перед змінами
├── state.json                       # стан інстансу для discovery
└── start-armareforger.sh            # launch script
```

### Що де лежить

| Шлях | Призначення |
|------|-------------|
| `server/` | SteamCMD install dir — сам Arma Reforger Dedicated Server |
| `config/config.json` | Конфіг сервера (редагується через `armactl config`) |
| `backups/` | Резервні копії конфігу перед кожною зміною |
| `state.json` | Discovery/state файл armactl |
| `start-armareforger.sh` | Стартовий скрипт, на який посилається systemd service |

### Multi-instance (майбутнє)

Модель `~/armactl-data/<instance>/` готова до розширення:

```text
~/armactl-data/
├── default/       # перший інстанс
├── training/      # другий інстанс
└── events/        # третій інстанс
```

Кожен інстанс — повністю ізольований, зі своїм конфігом, бекапами і state.

---

## 3. Системні файли (поза instance root)

Для автозапуску після reboot та планових рестартів використовуються systemd unit-файли:

```text
/etc/systemd/system/armareforger.service
/etc/systemd/system/armareforger-restart.service
/etc/systemd/system/armareforger-restart.timer
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
│              TUI (Textual)          │  ← Тільки UI, жодної логіки
├─────────────────────────────────────┤
│           Backend CLI (armactl)     │  ← Уся бізнес-логіка
├──────────┬──────────┬───────────────┤
│ discovery│ config   │ service/timer │  ← Модулі
│ state    │ mods     │ installer     │
│ logs     │ ports    │ repair        │
├──────────┴──────────┴───────────────┤
│          systemd / filesystem       │  ← Системний рівень
└─────────────────────────────────────┘
```

### Правила

1. **TUI не містить бізнес-логіки** — тільки викликає CLI-команди
2. **CLI — єдина точка входу до логіки** — усе працює і без TUI
3. **Модулі незалежні** — discovery не знає про TUI, config manager не знає про installer
4. **Templates → generated files** — конфіги та unit-файли генеруються з Jinja2-шаблонів
5. **Backup before write** — будь-яка зміна конфігу створює backup

---

## 5. Потоки даних

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
