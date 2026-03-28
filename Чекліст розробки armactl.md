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
- [x] Підв'язати TUI до backend CLI
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

## Phase 12 — TUI schedule

- [x] Додати екран `Timer status`
- [x] Додати поле `Restart schedule`
- [x] Додати `Enable timer`
- [x] Додати `Disable timer`
- [x] Додати `Restart now`
- [x] Додати показ наступного запуску таймера

## Phase 13 — Telegram Bot Integration

- [ ] Вибрати бібліотеку (aiogram / python-telegram-bot)
- [ ] Додати команду `/status` (стан сервера, гравці)
- [ ] Додати команду `/start` та `/stop`
- [ ] Додати команду `/restart`
- [ ] Додати управління графіком (schedule)
- [ ] Додати обмеження доступу (тільки для адміна по Chat ID)
- [ ] Створити окремий systemd сервіс для бота (`armactl-bot.service`)

## Phase 14 — Діагностика і полірування

- [ ] Додати показ CPU/RAM сервера
- [ ] Додати summary по конфігу
- [ ] Додати summary по модах
- [ ] Додати зрозумілі помилки
- [ ] Додати success notifications
- [ ] Додати masking для паролів
- [ ] Перевірити, що секрети не течуть у логах

## Phase 14 — Тести

- [ ] Unit tests для discovery
- [ ] Unit tests для config manager
- [ ] Unit tests для mod manager
- [ ] Integration tests для install
- [ ] Integration tests для service/timer
- [ ] Manual smoke test на чистій VM
- [ ] Manual smoke test на existing server
- [ ] Manual smoke test після reboot

## Phase 15 — Release readiness

- [ ] Описати інсталяцію в README
- [ ] Описати запуск TUI в README
- [ ] Описати режим existing server
- [ ] Описати repair mode
- [ ] Додати changelog
- [ ] Підготувати перший GitHub release
- [ ] Перевірити встановлення з релізного архіву
- [ ] Перевірити запуск на іншій машині

---

## Стартова точка

Починати треба з **Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4**.
Саме після цього вже буде сенс робити installer і TUI.
