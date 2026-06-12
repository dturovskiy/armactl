# Чекліст розробки `armactl`

## Phase 0 — Підготовка репозиторію

- [x] Створити репозиторій `armactl`
- [x] Визначити базову структуру директорій
- [x] Додати `README.md`
- [x] Додати `.gitignore`
- [x] Вибрати стек: Python + Textual
- [x] Зафіксувати цільову платформу: Ubuntu 24.04, 1 сервер, 1 інстанс
- [x] Зафіксувати файлову модель (один root інстансу):
  - [x] `~/armactl-data/default/` — root інстансу
  - [x] `~/armactl-data/default/server/` — SteamCMD install dir
  - [x] `~/armactl-data/default/config/config.json` — конфіг сервера
  - [x] `~/armactl-data/default/backups/` — автоматичні backup-и
  - [x] `~/armactl-data/default/state.json` — стан інстансу
  - [x] `~/armactl-data/default/start-armareforger.sh` — launch script
  - [x] `/etc/systemd/system/armareforger.service` — systemd service
  - [x] `/etc/systemd/system/armareforger-restart.timer` — systemd timer

## Phase 1 — Discovery і state

- [x] Реалізувати пошук існуючого сервера
- [x] Реалізувати пошук `ArmaReforgerServer`
- [x] Реалізувати пошук `config.json`
- [x] Реалізувати пошук `systemd service`
- [x] Реалізувати пошук `systemd timer`
- [x] Реалізувати перевірку `server running/stopped`
- [x] Реалізувати перевірку відкритих портів
- [x] Реалізувати збереження результату в `state.json`
- [x] Додати fallback на ручне вказання шляхів
- [x] Перевірити discovery на чистій системі
- [x] Перевірити discovery на вже існуючому сервері

## Phase 2 — Backend CLI skeleton

- [x] Створити CLI `armactl`
- [x] Додати команду `detect`
- [x] Додати команду `status`
- [x] Додати команду `start`
- [x] Додати команду `stop`
- [x] Додати команду `restart`
- [x] Додати команду `logs`
- [x] Додати команду `ports`
- [x] Додати зрозумілі exit codes
- [x] Додати JSON output mode для TUI
- [x] Перевірити, що всі базові команди працюють без TUI

## Phase 3 — Config manager

- [x] Реалізувати читання `config.json`
- [x] Реалізувати валідацію JSON
- [x] Реалізувати backup перед записом
- [x] Реалізувати atomic write
- [x] Додати `config show`
- [x] Додати `config set-name`
- [x] Додати `config set-scenario`
- [x] Додати `config set-maxplayers`
- [x] Додати `config set-password-admin`
- [x] Додати `config set-rcon-password`
- [x] Додати `config validate`
- [x] Перевірити, що зміни не ламають конфіг

## Phase 4 — Service і timer manager

- [x] Реалізувати генерацію `start-armareforger.sh`
- [x] Реалізувати генерацію `armareforger.service`
- [x] Реалізувати генерацію `armareforger-restart.service`
- [x] Реалізувати генерацію `armareforger-restart.timer`
- [x] Додати `service install`
- [x] Додати `service enable`
- [x] Додати `service disable`
- [x] Додати `service status`
- [x] Додати `timer install`
- [x] Додати `timer enable`
- [x] Додати `timer disable`
- [x] Додати `schedule show`
- [x] Додати `schedule set`
- [x] Додати `schedule restart-now`
- [x] Перевірити автозапуск після ребуту
- [x] Перевірити плановий рестарт

## Phase 5 — Installer

- [x] Реалізувати перевірку ОС
- [x] Реалізувати перевірку `sudo`
- [x] Реалізувати перевірку `steamcmd`
- [x] Реалізувати встановлення `steamcmd`, якщо його нема
- [x] Реалізувати створення install dir
- [x] Реалізувати встановлення сервера через SteamCMD
- [x] Реалізувати smoke-check після install
- [x] Реалізувати генерацію базового `config.json`
- [x] Реалізувати генерацію service/timer під час install
- [x] Реалізувати запуск сервера після install
- [x] Реалізувати запис `state.json`
- [x] Перевірити install на чистій VM

## Phase 6 — Repair mode

- [x] Реалізувати `repair`
- [x] Додати перевірку неповної інсталяції
- [x] Додати перевстановлення `start script`
- [x] Додати перевстановлення `service`
- [x] Додати перевстановлення `timer`
- [x] Додати `steamcmd validate/update` у repair
- [x] Додати оновлення `state.json` після repair
- [x] Перевірити repair на навмисно зламаному стані

## Phase 7 — Mod manager

- [x] Реалізувати `mods list`
- [x] Реалізувати `mods add`
- [x] Реалізувати `mods remove`
- [x] Реалізувати `mods dedupe`
- [x] Реалізувати `mods count`
- [x] Реалізувати batch import модів
- [x] Реалізувати batch export модів
- [x] Заборонити дублікати `modId`
- [x] Перевірити редагування модів у реальному `config.json`

## Phase 8 — TUI foundation

- [x] Створити базовий TUI app
- [x] Додати home screen
- [x] Додати режим `Install server`
- [x] Додати режим `Manage existing server`
- [x] Додати режим `Repair installation`
- [x] Підв'язати TUI до backend-шару без логіки в екранах
- [x] Перевірити, що TUI не містить бізнес-логіки

## Phase 9 — TUI server controls

- [x] Додати кнопку `Start`
- [x] Додати кнопку `Stop`
- [x] Додати кнопку `Restart`
- [x] Додати екран `Status`
- [x] Додати екран `Ports`
- [x] Додати екран `Logs`
- [x] Додати confirm dialog для stop/restart

## Phase 10 — TUI config editor

- [x] Додати поле `Server name`
- [x] Додати поле `Scenario ID`
- [x] Додати поле `Max players`
- [x] Додати поля портів
- [x] Додати поля паролів
- [x] Додати `Save`
- [x] Додати `Save and restart`
- [x] Додати повідомлення про backup
- [x] Перевірити, що зміни застосовуються правильно

## Phase 11 — TUI mods

- [x] Додати екран списку модів
- [x] Додати форму `Add mod`
- [x] Додати `Remove mod`
- [x] Додати `Import mod pack`
- [x] Додати `Export mod pack`
- [x] Додати `Dedupe mods`
- [x] Перевірити роботу з великим списком модів

## Phase ??? — Miscellaneous additions
- [x] Додати i18n Локалізацію (en/uk)
- [x] Додати Maintenance / Cleanup screen
- [x] Додати функцію Detect Existing Server в UI
- [x] Додати one-click launcher `./armactl` з автозавантаженням залежностей
- [x] Додати Raw Config JSON editor у TUI
- [x] Додати запуск host tests з TUI
- [x] Додати автозбереження логів host tests у файл
- [x] Додати ServerAdminTools admin guard перед стартом сервера

## Phase 12 — TUI schedule

- [x] Додати екран `Timer status`
- [x] Додати поле `Restart schedule`
- [x] Додати `Enable timer`
- [x] Додати `Disable timer`
- [x] Додати `Restart now`
- [x] Додати показ наступного запуску таймера

## Phase 13 — Telegram Bot Integration

- [x] Вибрати бібліотеку: `python-telegram-bot`
- [x] Додати TUI-екран конфігурації бота
- [x] Використовувати `~/armactl-data/<instance>/bot/.env` як source of truth
- [x] Додати `.env.example` у репо та ігнорити реальний `.env` у git
- [x] Додати встановлення/керування bot service через TUI
- [x] Додати команду `/status` (стан сервера)
- [x] Додати показ гравців у `/status`
- [x] Додати команду `/start` та `/stop`
- [x] Додати команду `/restart`
- [x] Додати управління графіком (schedule)
- [x] Додати обмеження доступу (тільки для адміна по Chat ID)
- [x] Створити окремий systemd сервіс для бота (`armactl-bot.service`)
- [x] Локалізація для кнопок

## Phase 14 — Діагностика і полірування

- [x] Додати показ CPU/RAM сервера
- [x] Додати summary по конфігу
- [x] Додати summary по модах
- [x] Додати зрозумілі помилки
- [x] Додати success notifications
- [x] Додати masking для паролів
- [x] Перевірити, що секрети не течуть у логах
- [x] Додати показ реального Server FPS/frame-time через `-logStats`
- [x] Додати stale/unavailable handling для FPS telemetry

## Phase 14 — Тести

- [x] Unit tests для discovery
- [x] Unit tests для config manager
- [x] Unit tests для mod manager
- [x] Integration tests для install
- [x] Integration tests для service/timer
- [x] Manual smoke test на чистій VM
- [x] Manual smoke test на existing server
- [x] Manual smoke test після reboot

## Phase 15 — Release readiness

- [x] Описати інсталяцію в README
- [x] Описати запуск TUI в README
- [x] Описати режим existing server
- [x] Описати repair mode
- [x] Додати changelog
- [x] Перевірити Server FPS telemetry на live сервері
- [x] Перевірити `./armactl status` після merge в `main`
- [x] Підготувати GitHub release `v0.5.0`
- [x] Підготувати перший GitHub release
- [x] Підготувати patch release `v0.5.1`
- [ ] Перевірити встановлення з релізного архіву
- [ ] Перевірити запуск на іншій машині

## Phase 16 — Web interface (planned)

- [x] Зафіксувати web architecture plan у `docs/web-interface-plan.md`
- [x] Відділити тести від збереженої UI-мови оператора
- [x] Зафіксувати, що `website/` є marketing/static site, не management panel
- [x] Зафіксувати per-VM deployment model для Proxmox
- [x] Зафіксувати дефолтні порти Arma і planned web port
- [x] Зафіксувати always-on lifecycle model для web panel
- [x] Зафіксувати remote-user і local-smoke сценарії для web panel
- [x] Додати shared blocked-port dictionary для web port validation
- [x] Зафіксувати central reverse-proxy upstream caveat для Proxmox
- [x] Зафіксувати rollout order і існуючі environment roles
- [x] Зафіксувати external SSHFS/mount project boundary для web file manager
- [x] Зафіксувати one-cabinet-per-machine MVP boundary
- [x] Зафіксувати logging/observability model для web panel
- [x] Додати centralized armactl log directory path model
- [x] Зафіксувати self-hosted dashboard visual direction і post-MVP portal idea
- [x] Додати handoff інструкції для implementation/review chats
- [x] Додати `src/armactl/web/` package skeleton
- [x] Додати optional web dependencies
- [x] Додати `scripts/run-web` і bootstrap support
- [x] Додати web-facing facade/DTO layer поверх existing backend modules
- [x] Оновити packaging/bootstrap для web templates/static і optional deps
- [x] Додати FastAPI app factory, health endpoint і minimal dashboard routes
- [x] Додати web runtime storage/config/db foundation
- [x] Додати `armactl web init` і first setup flow
- [x] Додати auth DB/password foundation і мінімальну owner role
- [x] Додати owner-user setup flow
- [x] Додати session і CSRF primitives
- [x] Додати login/logout routes, templates і cookie wiring
- [x] Підключити foreground `armactl web run` smoke launcher через Uvicorn
- [x] Додати Uvicorn reload для `armactl web run --dev`
- [x] Додати web permission categories foundation для dashboard/actions/files/backups/users
- [x] Додати dashboard read-only parity з TUI
- [x] Додати controlled start/stop/restart actions
- [x] Додати web i18n adapter і theme preferences поверх існуючих `src/armactl/locales/*.json`
- [x] Додати background job metadata model перед long-running operations
- [x] Додати background dispatcher/read-only jobs UI foundation перед install/repair/update flows
- [ ] Підключити install/repair/update flows до background jobs без blocking HTTP requests
- [x] Додати read-only management pages для config/mods/admins/bot через backend modules
- [ ] Додати edit/save/delete flows для config/mods/admins/bot через backend modules
- [ ] Додати safe local filesystem browser/upload/download
- [ ] Додати optional entitlement/feature-flag model, якщо продукт матиме платні tiers
- [ ] Додати `armactl-web.service` template і service commands
- [ ] Додати reverse proxy / HTTPS deployment docs
- [ ] Додати VM smoke checklist для web panel
- [ ] Додати remote HTTPS login smoke checklist

---

## Історична стартова точка

Для початкового CLI/TUI циклу старт був **Phase 0 → Phase 1 → Phase 2 →
Phase 3 → Phase 4**. Цей фундамент уже реалізований.

Поточний наступний етап - **Phase 16: Web interface (planned)**.
