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
- [ ] Перевірити, що всі базові команди працюють без TUI

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
- [ ] Перевірити, що зміни не ламають конфіг

## Phase 4 — Service і timer manager

- [ ] Реалізувати генерацію `start-armareforger.sh`
- [ ] Реалізувати генерацію `armareforger.service`
- [ ] Реалізувати генерацію `armareforger-restart.service`
- [ ] Реалізувати генерацію `armareforger-restart.timer`
- [ ] Додати `service install`
- [ ] Додати `service enable`
- [ ] Додати `service disable`
- [ ] Додати `service status`
- [ ] Додати `timer install`
- [ ] Додати `timer enable`
- [ ] Додати `timer disable`
- [ ] Додати `schedule show`
- [ ] Додати `schedule set`
- [ ] Додати `schedule restart-now`
- [ ] Перевірити автозапуск після ребуту
- [ ] Перевірити плановий рестарт

## Phase 5 — Installer

- [ ] Реалізувати перевірку ОС
- [ ] Реалізувати перевірку `sudo`
- [ ] Реалізувати перевірку `steamcmd`
- [ ] Реалізувати встановлення `steamcmd`, якщо його нема
- [ ] Реалізувати створення install dir
- [ ] Реалізувати встановлення сервера через SteamCMD
- [ ] Реалізувати smoke-check після install
- [ ] Реалізувати генерацію базового `config.json`
- [ ] Реалізувати генерацію service/timer під час install
- [ ] Реалізувати запуск сервера після install
- [ ] Реалізувати запис `state.json`
- [ ] Перевірити install на чистій VM

## Phase 6 — Repair mode

- [ ] Реалізувати `repair`
- [ ] Додати перевірку неповної інсталяції
- [ ] Додати перевстановлення `start script`
- [ ] Додати перевстановлення `service`
- [ ] Додати перевстановлення `timer`
- [ ] Додати `steamcmd validate/update` у repair
- [ ] Додати оновлення `state.json` після repair
- [ ] Перевірити repair на навмисно зламаному стані

## Phase 7 — Mod manager

- [ ] Реалізувати `mods list`
- [ ] Реалізувати `mods add`
- [ ] Реалізувати `mods remove`
- [ ] Реалізувати `mods dedupe`
- [ ] Реалізувати `mods count`
- [ ] Реалізувати batch import модів
- [ ] Реалізувати batch export модів
- [ ] Заборонити дублікати `modId`
- [ ] Перевірити редагування модів у реальному `config.json`

## Phase 8 — TUI foundation

- [ ] Створити базовий TUI app
- [ ] Додати home screen
- [ ] Додати режим `Install server`
- [ ] Додати режим `Manage existing server`
- [ ] Додати режим `Repair installation`
- [ ] Підв'язати TUI до backend CLI
- [ ] Перевірити, що TUI не містить бізнес-логіки

## Phase 9 — TUI server controls

- [ ] Додати кнопку `Start`
- [ ] Додати кнопку `Stop`
- [ ] Додати кнопку `Restart`
- [ ] Додати екран `Status`
- [ ] Додати екран `Ports`
- [ ] Додати екран `Logs`
- [ ] Додати confirm dialog для stop/restart

## Phase 10 — TUI config editor

- [ ] Додати поле `Server name`
- [ ] Додати поле `Scenario ID`
- [ ] Додати поле `Max players`
- [ ] Додати поля портів
- [ ] Додати поля паролів
- [ ] Додати `Save`
- [ ] Додати `Save and restart`
- [ ] Додати повідомлення про backup
- [ ] Перевірити, що зміни застосовуються правильно

## Phase 11 — TUI mods

- [ ] Додати екран списку модів
- [ ] Додати форму `Add mod`
- [ ] Додати `Remove mod`
- [ ] Додати `Import mod pack`
- [ ] Додати `Export mod pack`
- [ ] Додати `Dedupe mods`
- [ ] Перевірити роботу з великим списком модів

## Phase 12 — TUI schedule

- [ ] Додати екран `Timer status`
- [ ] Додати поле `Restart schedule`
- [ ] Додати `Enable timer`
- [ ] Додати `Disable timer`
- [ ] Додати `Restart now`
- [ ] Додати показ наступного запуску таймера

## Phase 13 — Діагностика і полірування

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