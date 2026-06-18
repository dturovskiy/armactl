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
- [x] Зафіксувати web architecture guardrails: route/service/facade межі, mutating-action contract, pending-work vs jobs, і стабільні правила тестування
- [x] Провести web architecture cleanup перед наступними великими web-фічами: розділити routes/management.py на окремі config/mods/admins/bot routers без зміни поведінки
- [ ] Розділити oversized web modules: facade.py за page/domain DTO, services/filesystem.py за roots/listing/preview/download/upload перед delete/edit/overwrite flows
- [ ] Розбити oversized tests/test_web_app.py на focused web test modules і прибрати залежність від patching route globals / imported FastAPI endpoint internals
- [ ] Провести audit broad except Exception у web routes/services: залишити тільки documented fail-closed/degradation cases, решту замінити контрольованими domain errors
- [ ] Додати повний web system audit як окремий gate: Arkady проходить docs/web-system-audit.md перед великим ризиковим слайсом і після нього, з findings/blockers/follow-up у handoff
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
- [x] Додати lightweight dashboard live refresh через JSON endpoint і JS polling без WebSocket
- [x] Поліпшити dashboard snapshot layout: широкий config-блок і компактні нижні плитки
- [x] Додати компактні dashboard meters для FPS/CPU/RAM/Disk поверх live-refresh даних
- [ ] Спроєктувати окремий охайний FPS history chart після UI review
- [x] Додати controlled start/stop/restart actions
- [x] Додати web schedule controls для restart timer: show/set/enable/disable/restart-now/next-run
- [x] Додати game server boot/autostart policy, dashboard warning і web enable/disable controls
- [ ] Переглянути UI `/schedule`: рознести timer/autostart/restart-now у спокійніші секції без візуального перевантаження
- [ ] Спроєктувати host reboot/shutdown controls як owner/admin-only future flow з double-confirm і audit
- [ ] Спроєктувати Windows backend adapter як future architecture: service manager, logs, paths, firewall/process/metrics, install/update flow; не змішувати з Linux/systemd web MVP
- [ ] Спроєктувати diagnostic command palette для allowlisted debug/report commands без raw shell
- [ ] Спроєктувати break-glass web terminal як disabled-by-default future flow з IP allowlist/trusted proxy/extra auth/audit
- [x] Додати web i18n adapter і theme preferences поверх існуючих `src/armactl/locales/*.json`
- [ ] Додати dev/test-only pseudolocalization mode для пошуку missing keys, raw text і layout overflow
- [x] Додати fast preferences UX: миттєве перемикання теми і lightweight language switch без зайвого важкого dashboard reload
- [ ] Додати floating toast notifications для save/action result без стрибків на верх сторінки
- [ ] Додати topbar notification indicator/center для pending operator work, saved-but-not-applied work і важливих operator warnings
- [ ] Додати notification про фактичний плановий restart сервера з safe proof із allowlisted systemd timer/service log або audit/source metadata
- [ ] Додати security review gate для premium/mega, diagnostics, terminal, IP allowlist і host controls
- [x] Додати background job metadata model перед long-running operations
- [x] Додати background dispatcher/read-only jobs UI foundation перед install/repair/update flows
- [x] Додати read-only management pages для config/mods/admins/bot через backend modules
- [ ] Спроєктувати settings IA: dashboard summary, basic config, mods, mod settings, network/advanced, diagnostics, danger zone
- [ ] Додати config schema inventory для `config.json`: supported fields, UI groups Basic/Gameplay/Visibility-Crossplay/Network-A2S-RCON/Security/Advanced/Danger Zone, safe vs advanced editor decision
- [ ] Розширити `/config` safe controls для third-person view і crossplay/platform support тільки після перевірки точних Arma Reforger config keys у current schema/backend або official docs
- [ ] Зафіксувати, що normal `config.json` editing живе у `/config`, не у `/files`; secrets/RCON/admin sensitive fields не показувати casually
- [x] Додати `armactl-web.service` template і service commands
- [x] Додати login rate limiting / auth abuse throttling для web login
- [x] Додати явне HTTPS/external-bind warning у web UI/runtime summary
- [ ] Додати optional IP allowlist / trusted proxy handling для remote deployments
- [x] Додати VM smoke checklist docs для web panel
- [x] Додати reverse proxy / HTTPS deployment docs
- [x] Додати read-only logs/report web views з bounded/redacted output
- [ ] Додати prettier audit JSONL rendering для `/logs` без raw стіни JSON
- [ ] Додати logs/report download-export для allowlisted bounded sources
- [ ] Додати logs live follow/auto-refresh з pause/refresh controls
- [ ] Додати logs filters/search/highlighting для level/source/text і `ERROR`/`WARNING`
- [x] Підключити install/repair flows до background jobs без blocking HTTP requests
- [ ] Підключити update flow до background jobs без blocking HTTP requests
- [x] Додати `web_jobs` maintenance migration для старих duplicate active rows: detect/report/resolve дублікати `queued`/`running` за `(kind, instance)` перед production release або наступним install/repair/update slice
- [x] Додати SQLite migration/index для active job lookup за `(kind, instance, status, created_at, id)` або еквівалентний schema-backed guard
- [x] Додати diagnostics/health check для `web_jobs`, який показує duplicate active jobs і job-store integrity проблеми у `/jobs` або diagnostics page, а не ховає їх як hidden debt
- [ ] Follow-up owner: наступний jobs/update slice. Перевірити `EXPLAIN QUERY PLAN` і latency active lookup на production-scale job history перед додаванням update job flow або standalone worker daemon.
- [x] Додати safe web edit/save для базових полів `config.json` через `config_manager`
- [x] Додати audit logging для safe web config save з changed fields і backup path
- [x] Додати audit logging для file upload і web install/repair job enqueue після VM smoke audit cleanup
- [x] Додати stacked web pending operator work для config/admins/mods окремо від `web_jobs`, з compact dashboard table, dense details на `/jobs` і clear після successful web restart
- [x] Додати shared operator UI primitives для dashboard/config pages
- [x] Додати safe web add/update/remove для game admins через `admins_manager` з auth/CSRF/`admins:manage`/audit
- [x] Додати basic web add/update/enable/disable/remove для mods через `mods_manager` з auth/CSRF/`mods:manage`/audit
- [ ] Додати edit/save/delete flows для bot і розширених config полів через backend modules
- [ ] Спроєктувати bulk paste/import/export/clear all/advanced modpack workflows для web mods окремим future flow
- [ ] Спроєктувати окремі mod settings pages для SAT та інших модів з власними runtime config checks
- [ ] Спроєктувати SAT runtime edits для admins/gameMasters/bans як narrow field updates з backup/audit, не full overwrite unrelated SAT config
- [x] Прибрати нейтральне SAT-missing повідомлення з dashboard; показувати SAT на dashboard тільки як реальну health/guard проблему
- [ ] Спроєктувати network/advanced server settings page для game/A2S/RCON ports і sensitive server options
- [ ] Спроєктувати diagnostics page для SAT/config/ports/paths/telemetry/log health checks
- [ ] Спроєктувати advanced danger zone для raw JSON, backup restore і destructive maintenance actions
- [ ] Розділити config permissions: `settings:manage` для allowlisted safe fields, `settings:advanced` для ports/RCON/A2S/crossplay/platform/third-person/security, `config:raw_edit` для break-glass raw JSON
- [ ] Спроєктувати owner/mega-eligible emergency raw JSON config editor як окремий button/mode у `/config`, не `/files`, з explicit permission/CSRF/double confirm/JSON validation/backup/audit/redacted errors/pending-work behavior
- [x] Додати web moderation foundation на `/admins`: current players, server-rendered search, add-to-game-admin only with reliable identity
- [x] Додати instance-scoped player registry foundation (`players.db`) для reliable IDs, nickname history, first/last seen і seen count
- [x] Додати explicit web refresh ingestion з current RCON/player_view roster без вигаданих ID і без IP storage
- [x] Додати `/players` read-only registry page з search по nickname/ID
- [ ] Розширити `Players / Moderation` до detail page per reliable player, session history (`connected_at`, `disconnected_at`, played duration), activity duration і recent/history views
- [ ] Додати додаткове player identity ingestion з logs/SAT adapters без вигаданих ID і без IP storage by default
- [ ] Додати moderation UI: nickname/ID search-filter, player details, and add-to-game-admin action when reliable player/admin reference is known; `/admins` лишається quick-add convenience
- [ ] Додати ban list management тільки по reliable identity/admin reference/SteamID64/backend ID з reason/actor/timestamp/optional expiry, auth/permissions/POST+CSRF/confirmation/audit/backups/rollback
- [x] Додати safe read-only filesystem browser foundation
- [x] Додати download single file для web file browser
- [x] Додати upload одного нового файла в server root для web file browser без overwrite
- [ ] Додати повний threat-model note для uploads beyond current guards: no execute, no auto-unpack, size/path/name limits, bounded redacted preview; upload-new-file already has path/name/size checks and audit
- [ ] Якщо future text editor для `/files` потрібний, обмежити root/path/extension/size, додати backup/audit, і не використовувати його як primary config editor
- [ ] Додати safe single-file delete для web file browser: files only, no dirs, `files:delete`, confirmation, CSRF, audit, no symlink/traversal escape
- [ ] Додати atomic overwrite для web file browser з окремим permission/confirmation/backup/audit
- [ ] Додати delete/rename для web file browser
- [ ] Додати archive extraction flow після окремого threat model
- [ ] Додати optional entitlement/feature-flag model, якщо продукт матиме платні tiers
- [ ] Спроєктувати web product tiers: basic/plus/premium/mega окремо від ролей і permissions
- [ ] Додати explicit permission gates і audit для dangerous features: raw config editor, advanced config, host controls, command palette, terminal, banlist, file edit/delete/overwrite, SAT runtime edits, user/tier/allowlist management
- [ ] Спроєктувати admin-only web user/role/tier management для Deus/Yaroslav operators
- [ ] Спроєктувати IP allowlist management UI/API з CIDR, labels, expiry, audit і trusted-proxy rules
- [ ] Додати remote HTTPS login smoke checklist

---

## Історична стартова точка

Для початкового CLI/TUI циклу старт був **Phase 0 → Phase 1 → Phase 2 →
Phase 3 → Phase 4**. Цей фундамент уже реалізований.

Поточний наступний етап - **Phase 16: Web interface (planned)**.
