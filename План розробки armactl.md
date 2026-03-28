Нижче — готовий, самодостатній план проєкту, розбитий на окремі ТЗ. Його можна копіювати частинами в інші чати.

> **Примітка:** Номери ТЗ (ТЗ-1, ТЗ-2, …) — це **тематичні розділи специфікації**, а не порядок реалізації. Фактичний порядок робіт зафіксований у секції «Пріоритети розробки» нижче та в окремому файлі `Чекліст розробки armactl.md`.

---

# Глобальне ТЗ: `armactl`

## 1. Мета проєкту

Зробити open-source bundle для **Arma Reforger Dedicated Server** на Linux, який:

- встановлює сервер з нуля через SteamCMD;
- підхоплює вже існуючий сервер;
- керує сервером через **TUI**;
- налаштовує `systemd service` і `systemd timer`;
- редагує `config.json` без ручного редагування;
- керує модами, сценаріями, параметрами сервера;
- має нормальний release flow через GitHub.

## 2. Основні сценарії використання

### Сценарій А: чиста машина

Користувач клонить/завантажує репо, запускає TUI, обирає `Install server`, проходить майстер, сервер ставиться і запускається.

### Сценарій Б: сервер уже існує

Користувач запускає TUI, інструмент знаходить існуючий сервер, конфіг, service і timer, після чого переходить у режим `Manage existing server`.

### Сценарій В: зламана або неповна інсталяція

Інструмент знаходить частину компонентів і пропонує `Repair installation`.

## 3. Цільова платформа

Базова ціль:

- Ubuntu 24.04
- один dedicated server
- один Linux-користувач
- один інстанс сервера на машині

Пізніше можна розширити до multi-instance.

## 4. Розміщення файлів і директорій

Усі файли armactl та дані сервера зібрані під **один root інстансу**. Системні unit-файли для автозапуску лишаються в `/etc/systemd/system/`.

### Логічний root інстансу

```text
/home/<user>/armactl-data/default/
```

### Структура всередині

```text
/home/<user>/armactl-data/default/
  server/                          # SteamCMD install dir (ArmaReforgerServer тут)
  config/
    config.json                    # конфіг сервера
  backups/                         # автоматичні backup-и перед змінами
  state.json                       # стан інстансу для discovery
  start-armareforger.sh            # launch script
```

### Системні файли (поза root-ом)

```text
/etc/systemd/system/armareforger.service
/etc/systemd/system/armareforger-restart.service
/etc/systemd/system/armareforger-restart.timer
```

Service посилається на root інстансу:
- `WorkingDirectory=/home/<user>/armactl-data/default/server`
- `ExecStart=/home/<user>/armactl-data/default/start-armareforger.sh`

### Переваги цієї моделі

- Усе важливе в одному місці — простіший backup і міграція
- Зрозуміла файлова модель для користувача
- Легший existing-server detection
- Готова до multi-instance розширення (інші папки поруч із `default/`)

## 5. Основні функції v1

- detect existing installation
- install server via SteamCMD
- start / stop / restart / status
- tail logs
- read/write config.json
- set server name
- set scenarioId
- set maxPlayers
- list/add/remove mods
- install and manage systemd service
- install and manage scheduled restart timer
- create backups before changes
- TUI as main entrypoint

## 6. Архітектурний принцип

Логіка не має жити в TUI.

Правильна модель:

- **backend modules**: reusable Python-модулі з бізнес-логікою
- **backend CLI**: `armactl` поверх цих модулів
- **TUI**: оболонка над backend-шаром (через reusable modules / CLI), без логіки в екранах
- **systemd**: рушій сервера
- **state/discovery**: окремий модуль

---

# ТЗ-1: Discovery та state management

## Мета

Навчити інструмент знаходити вже існуючий сервер і визначати стан системи.

## Функціональні вимоги

Інструмент має визначати:

- чи існує install dir;
- чи існує `ArmaReforgerServer`;
- чи існує `config.json`;
- чи існує `armareforger.service`;
- чи існує `armareforger-restart.timer`;
- чи сервіс активний;
- чи слухаються порти;
- який `scenarioId` зараз записаний;
- який `maxPlayers`;
- скільки модів у конфігу.

## Джерела істини для пошуку

Порядок пошуку:

1. `~/armactl-data/default/state.json`
2. стандартні шляхи (`~/armactl-data/`)
3. `systemd unit` (`ExecStart`, `WorkingDirectory`)
4. ручний режим `Locate existing installation`

## Очікуваний об’єкт стану

```json
{
  "server_installed": true,
  "binary_exists": true,
  "config_exists": true,
  "service_exists": true,
  "timer_exists": true,
  "server_running": true,
  "instance_root": "/home/user/armactl-data/default",
  "install_dir": "/home/user/armactl-data/default/server",
  "config_path": "/home/user/armactl-data/default/config/config.json",
  "service_name": "armareforger.service",
  "timer_name": "armareforger-restart.timer",
  "ports": {
    "game": 2001,
    "a2s": 17777,
    "rcon": 19999
  }
}
```

## Definition of Done

- [ ] discovery працює на чистій системі;
- [ ] discovery працює на вже встановленому сервері;
- [ ] discovery не падає, якщо частина файлів відсутня;
- [ ] результати discovery можна показати в TUI.

## Чекліст

- [ ] є модуль `discovery.py`
- [ ] є функція пошуку install dir
- [ ] є функція пошуку config
- [ ] є функція читання systemd unit
- [ ] є функція перевірки `systemctl status`
- [ ] є функція читання портів через `ss`
- [ ] є збереження в `state.json`
- [ ] є fallback на ручний режим

---

# ТЗ-2: Installer

## Мета

Реалізувати встановлення сервера з нуля.

## Функціональні вимоги

Installer має:

- перевіряти ОС;
- перевіряти наявність `sudo`;
- перевіряти наявність `steamcmd`;
- за потреби встановлювати `steamcmd`;
- створювати директорії;
- встановлювати Arma Reforger через SteamCMD;
- створювати базовий `config.json`;
- створювати start script;
- створювати `systemd service`;
- створювати `systemd timer`;
- запускати сервер;
- перевіряти, що сервер стартував.

## Вхідні параметри для install wizard

Installer має питати:

- install dir
- config dir
- server name
- bind port
- a2s port
- rcon port
- `passwordAdmin`
- `rcon.password`
- `scenarioId`
- `maxPlayers`
- `battlEye`
- чи одразу налаштувати auto-start
- чи одразу налаштувати scheduled restart

## Стратегія встановлення

1. Підготовка
2. SteamCMD install
3. Валідація файлів
4. Генерація config
5. Генерація service/timer
6. `systemctl daemon-reload`
7. `enable --now`
8. smoke check

## Definition of Done

- [ ] install працює на чистій Ubuntu 24.04;
- [ ] після install сервер реально запускається;
- [ ] створені service і timer;
- [ ] створений `state.json`;
- [ ] повторний запуск installer не ламає систему.

## Чекліст

- [ ] перевірка ОС
- [ ] перевірка `steamcmd`
- [ ] створення install dir
- [ ] створення config dir
- [ ] завантаження сервера
- [ ] перевірка `ArmaReforgerServer`
- [ ] генерація `config.json`
- [ ] генерація start script
- [ ] генерація service
- [ ] генерація timer
- [ ] daemon-reload
- [ ] enable service
- [ ] smoke test
- [ ] запис `state.json`

---

# ТЗ-3: systemd service та timer

## Мета

Стандартизувати автозапуск і планові рестарти.

## Service requirements

Service має:

- запускатися після `network-online.target`;
- працювати від вказаного користувача;
- використовувати окремий start script;
- мати `Restart=always`;
- мати `RestartSec`;
- коректно зупинятися;
- логуватися в `journalctl`.

## Timer requirements

Timer має:

- підтримувати щоденний рестарт;
- підтримувати кілька рестартів на добу;
- бути `Persistent=true`;
- викликати окремий `.service`, який робить `systemctl restart`.

## Керування з backend

Потрібні команди:

- install service
- remove service
- enable service
- disable service
- status service
- install timer
- remove timer
- enable timer
- disable timer
- set restart schedule
- show next scheduled restart

## Definition of Done

- [ ] service стартує після reboot;
- [ ] timer спрацьовує за розкладом;
- [ ] рестарт не вимагає ручної участі;
- [ ] TUI може керувати цим.

## Чекліст

- [ ] шаблон `armareforger.service`
- [ ] шаблон `armareforger-restart.service`
- [ ] шаблон `armareforger-restart.timer`
- [ ] команда встановлення service
- [ ] команда встановлення timer
- [ ] команда зміни `OnCalendar`
- [ ] команда `list-timers`
- [ ] команда перевірки `systemctl status`

---

# ТЗ-4: Backend CLI `armactl`

## Мета

Зробити єдиний backend, який виконує всю логіку.

## Обов’язкові команди v1

### Сервер

- `armactl status`
- `armactl start`
- `armactl stop`
- `armactl restart`
- `armactl logs`
- `armactl ports`

### Встановлення

- `armactl install`
- `armactl repair`
- `armactl update`

### Конфіг

- `armactl config show`
- `armactl config set-name`
- `armactl config set-scenario`
- `armactl config set-maxplayers`
- `armactl config set-password-admin`
- `armactl config set-rcon-password`
- `armactl config validate`

### Моди

- `armactl mods list`
- `armactl mods add`
- `armactl mods remove`
- `armactl mods dedupe`
- `armactl mods count`

### Розклад

- `armactl schedule show`
- `armactl schedule set`
- `armactl schedule enable`
- `armactl schedule disable`
- `armactl schedule restart-now`

### Discovery

- `armactl detect`
- `armactl locate`

## Вимоги до UX CLI

- зрозумілі повідомлення;
- повернення коректних exit code;
- JSON-output режим для TUI;
- backup перед змінами;
- dry-run для критичних дій.

## Definition of Done

- [ ] усі дії працюють без TUI;
- [ ] TUI може використовувати тільки CLI;
- [ ] є стабільні exit codes;
- [ ] є machine-readable output.

## Чекліст

- [ ] базовий parser
- [ ] підкоманди server
- [ ] підкоманди config
- [ ] підкоманди mods
- [ ] підкоманди schedule
- [ ] підкоманди detect/install/repair
- [ ] JSON output mode
- [ ] backup mode
- [ ] dry-run mode

---

# ТЗ-5: Config manager

## Мета

Безпечно редагувати `config.json`.

## Основні вимоги

Менеджер конфігу має:

- читати JSON;
- валідовувати JSON перед записом;
- робити backup перед записом;
- підтримувати atomic write;
- вміти змінювати окремі поля без затирання інших.

## Поля, які мають підтримуватися у v1

- `game.name`
- `game.scenarioId`
- `game.maxPlayers`
- `game.password`
- `game.passwordAdmin`
- `rcon.password`
- `bindPort`
- `a2s.port`
- `rcon.port`
- `game.visible`
- `game.gameProperties.*`
- `game.mods`

## Спеціальні вимоги для модів

- не допускати дублікатів `modId`
- вміти додавати новий мод
- вміти прибирати мод
- вміти перевіряти кількість модів
- мати імпорт зі списку

## Definition of Done

- [ ] зміни не ламають JSON;
- [ ] backup створюється завжди;
- [ ] моди не дублюються;
- [ ] можна відновити попередній конфіг.

## Чекліст

- [ ] load/save JSON
- [ ] validate before write
- [ ] backup creation
- [ ] atomic write
- [ ] set nested fields
- [ ] add/remove mod
- [ ] dedupe mods
- [ ] restore from backup

---

# ТЗ-6: Mod management

## Мета

Керувати модами через інтерфейс, а не руками.

## Основні функції

- показати список модів;
- додати мод вручну;
- видалити мод;
- змінити порядок модів;
- знайти дублікати;
- імпортувати пачку модів;
- експортувати список модів.

## Формат мода

Базовий формат:

```json
{
  "modId": "XXXX",
  "name": "Mod Name"
}
```

Пізніше можна додати підтримку `version`, якщо буде потрібно.

## UX-вимоги

- TUI має показувати:
  - кількість модів;
  - останні додані;
  - попередження про дублікати;
  - підтвердження перед видаленням.

## Definition of Done

- [ ] список модів редагується без ручного JSON;
- [ ] дублікати не пролазять;
- [ ] зміни відображаються в `config.json`.

## Чекліст

- [ ] list mods
- [ ] add mod
- [ ] remove mod
- [ ] dedupe
- [ ] import batch
- [ ] export batch
- [ ] reorder
- [ ] validate mod entries

---

# ТЗ-7: TUI

## Мета

Зробити головний інтерфейс користувача.

## Технологія

Рекомендовано: Python + Textual.

## Основні екрани

### Головний екран

- detected status
- server running/stopped
- install/manage/repair
- швидкі дії

### Server

- start
- stop
- restart
- status
- ports
- tail logs

### Config

- server name
- scenarioId
- maxPlayers
- ports
- passwords
- visibility
- BattlEye
- save / save and restart

### Mods

- list mods
- add mod
- remove mod
- import list
- dedupe

### Schedule

- timer enabled/disabled
- current schedule
- edit schedule
- restart now

### Logs

- `journalctl -u armareforger -n 100`
- manual refresh

## UX-принципи

- одна дія — один екран або одна форма;
- підтвердження перед stop/restart;
- повідомлення про успіх/помилку;
- ніякого ручного редагування JSON у v1.

## Definition of Done

- [ ] повний базовий цикл керування можливий лише через TUI;
- [ ] installer доступний із TUI;
- [ ] manage existing server доступний із TUI.

## Чекліст

- [ ] home screen
- [ ] detect flow
- [ ] install flow
- [ ] server controls
- [ ] config forms
- [ ] mods screen
- [ ] schedule screen
- [ ] logs screen
- [ ] confirm dialogs
- [ ] error dialogs

---

# ТЗ-8: Repair mode

## Мета

Відновлювати вже існуючу або частково пошкоджену інсталяцію.

## Що має вміти repair

- знайти install dir;
- перевірити binary;
- перевірити config;
- перевірити service;
- перевірити timer;
- перевстановити service;
- перевстановити timer;
- відновити start script;
- оновити server files через SteamCMD;
- створити backup перед repair.

## Сценарії repair

- binary є, service нема
- service є, config нема
- config є, timer нема
- install dir є, але файли биті
- paths у service не збігаються з реальністю

## Definition of Done

- [ ] repair не шкодить робочій системі;
- [ ] repair відновлює типовий broken state;
- [ ] repair вміє переприв’язати service до правильних шляхів.

## Чекліст

- [ ] detect broken state
- [ ] rebuild start script
- [ ] rebuild service
- [ ] rebuild timer
- [ ] steamcmd validate/update
- [ ] config backup
- [ ] state.json refresh

---

# ТЗ-9: Logs та діагностика

## Мета

Дати простий доступ до діагностики.

## Функції

- показати `systemctl status`
- показати останні 100 рядків `journalctl`
- показати listening ports
- показати CPU/RAM процесу
- показати server state
- показати `scenarioId`, `maxPlayers`, mods count

## Definition of Done

- [ ] основна діагностика доступна без ручного SSH-набору команд;
- [ ] TUI показує зрозумілий стан.

## Чекліст

- [ ] status
- [ ] logs
- [ ] ports
- [ ] process stats
- [ ] config summary
- [ ] service summary

---

# ТЗ-10: Packaging та GitHub releases

## Мета

Зробити нормальний спосіб розповсюдження.

## Мінімум для v1

Репо має містити:

- README
- install instructions
- release artifact
- changelog
- versioning

## Формат релізу

Варіанти:

1. репо + `git clone`
2. tarball release
3. shell bootstrap script

Для v1 достатньо:

- `git clone`
- запуск `python -m ...` або `./run-tui`

## Бажаний UX релізу

```bash
git clone ...
cd armactl
./run-tui
```

або

```bash
wget release.tar.gz
tar -xzf ...
cd ...
./run-tui
```

## Definition of Done

- [ ] користувач може поставити й запустити інструмент без ручного збирання;
- [ ] документація відповідає реальності.

## Чекліст

- [ ] README
- [ ] install guide
- [ ] run guide
- [ ] release versioning
- [ ] changelog
- [ ] example screenshots

---

# ТЗ-11: Безпека

## Вимоги

- не зберігати паролі у логах;
- не друкувати `passwordAdmin` і `rcon.password` у відкритому вигляді без потреби;
- робити підтвердження для stop/restart/reinstall;
- мінімізувати `sudo`;
- бажано мати точкові sudo-rules для service operations.

## Чекліст

- [ ] passwords masked in UI
- [ ] no secrets in logs
- [ ] confirmations for dangerous actions
- [ ] minimal sudo usage
- [ ] documented sudoers strategy

---

# ТЗ-12: Тести

## Типи тестів

### Unit

- config editing
- mod add/remove/dedupe
- discovery parsing
- state generation

### Integration

- install on clean machine
- detect existing install
- service creation
- timer creation
- config save + restart

### Manual smoke

- install server
- start server
- reboot machine
- server auto-start
- timer restart
- change scenario
- add/remove mod

## Чекліст

- [ ] unit tests for config manager
- [ ] unit tests for discovery
- [ ] integration tests for install flow
- [ ] integration tests for service flow
- [ ] smoke test checklist documented

---

# Пріоритети розробки

## Фаза 1 — foundation

1. discovery
2. backend CLI skeleton
3. config manager
4. service/timer manager

## Фаза 2 — installer

5. install flow
6. repair flow
7. smoke validation

## Фаза 3 — TUI

8. home screen
9. server controls
10. config editor
11. logs
12. schedule

## Фаза 4 — mod tools

13. mods list/add/remove/dedupe
14. import/export mod pack

## Фаза 5 — polish

15. docs
16. screenshots
17. release packaging

---

# Master checklist

## Архітектура

- [ ] є backend CLI
- [ ] є TUI
- [ ] є discovery
- [ ] є state file
- [ ] є config manager
- [ ] є service manager
- [ ] є timer manager

## Installer

- [ ] install from zero
- [ ] repair existing server
- [ ] detect existing install
- [ ] smoke check after install

## Управління сервером

- [ ] start
- [ ] stop
- [ ] restart
- [ ] status
- [ ] logs
- [ ] ports

## Конфіг

- [ ] set name
- [ ] set scenario
- [ ] set maxPlayers
- [ ] set ports
- [ ] set passwords
- [ ] validate config
- [ ] backup config

## Моди

- [ ] list
- [ ] add
- [ ] remove
- [ ] dedupe
- [ ] import
- [ ] export

## Автоматизація

- [ ] service auto-start
- [ ] restart on crash
- [ ] scheduled restart
- [ ] show next timer run

## Реліз

- [ ] README
- [ ] install guide
- [ ] run guide
- [ ] changelog
- [ ] tagged release

---

# Коротке ТЗ для іншого чату

Скопіюй це окремо, якщо треба дати іншому чату короткий контекст:

```text
Потрібно спроєктувати і реалізувати open-source Linux bundle для Arma Reforger Dedicated Server під назвою armactl.

Ціль:
- installer + manager в одному інструменті
- TUI як основний інтерфейс
- робота і на чистій машині, і з already existing server
- SteamCMD install
- керування через systemd
- планові рестарти через systemd timer
- редагування config.json без ручного редагування
- керування модами
- logs/status/ports
- repair mode

Файлова модель — один root інстансу:
- instance root: ~/armactl-data/default/
- server dir:    ~/armactl-data/default/server/
- config file:   ~/armactl-data/default/config/config.json
- backups:       ~/armactl-data/default/backups/
- state file:    ~/armactl-data/default/state.json
- start script:  ~/armactl-data/default/start-armareforger.sh
- service:       /etc/systemd/system/armareforger.service
- timer:         /etc/systemd/system/armareforger-restart.timer

Потрібно спочатку спроєктувати backend CLI armactl:
- detect
- install
- repair
- update
- start/stop/restart/status/logs
- config set-name/set-scenario/set-maxplayers
- mods list/add/remove/dedupe
- schedule show/set/enable/disable

Потім поверх цього зробити TUI.
```

---

# Що робити далі

Найкращий наступний крок — **не код усього одразу**, а окреме ТЗ на **структуру репозиторію + backend CLI v1**. Це перший шматок, який треба реалізувати правильно.
