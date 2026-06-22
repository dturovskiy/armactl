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

## Phase 16 — Web interface foundation (`feat/web-interface`)

Web foundation is implemented on the branch; stable release, VM validation, and
hardening work remain pending. Unchecked items below are future slices, not proof
that the whole web panel is still only planned.

- [x] Зафіксувати web architecture plan у `docs/web-interface-plan.md`
- [x] Зафіксувати web architecture guardrails: route/service/facade межі, mutating-action contract, pending-work vs jobs, і стабільні правила тестування
- [x] Провести web architecture cleanup перед наступними великими web-фічами: розділити routes/management.py на окремі config/mods/admins/bot routers без зміни поведінки
- [x] Розділити `facade.py` за page/domain DTO: dashboard, config, mods, admins, bot і schedule loaders винесені з моноліту
- [x] Розділити `services/filesystem.py` за roots/listing/preview/download/upload перед delete/edit/overwrite flows
- [x] Розбити oversized `tests/test_web_app.py` на focused web test modules і прибрати залежність від patching route globals / imported FastAPI endpoint internals
- [x] Прибрати залишковий web test debt з monkeypatch route internals: web tests патчать stable service/page_model/adapter seams, не FastAPI route globals або endpoint internals
- [x] Повторити architecture/modularity audit після завершених refactor slices (`facade.py`, `services/filesystem.py`, `tests/test_web_app.py`); результат: `docs/system-modularity-audit-results-20260619.md`
- [x] Провести audit broad except Exception у web routes/services: mutating route/service catches очищені; documented fail-closed/degradation cases зафіксовані у plan/handoff
- [ ] Додати повний web system audit як окремий gate: Arkady проходить docs/web-system-audit.md перед великим ризиковим слайсом і після нього, з findings/blockers/follow-up у handoff
- [x] Додати system-wide modularity audit protocol у `docs/system-modularity-audit.md` для Аркадія перед великими platform/domain/refactor slices
- [x] Провести baseline/repeat system modularity audit по всьому проекту: CLI/TUI/web/bot, backend modules, platform adapters, persistence, tests/CI, docs; результат: `docs/system-modularity-audit-results-20260619.md`
- [x] Провести documentation audit після web/refactor/modularity slices; результат: `docs/documentation-audit-results-20260619.md`
- [x] Оновити top-level docs після documentation audit: README, architecture, checklist, troubleshooting, roadmap, з future-only blockers винесеними окремо
- [x] Відділити тести від збереженої UI-мови оператора
- [x] Винести marketing/static site в окремий repo `dturovskiy/armactl-website`
- [x] Зафіксувати per-VM deployment model для Proxmox
- [x] Зафіксувати дефолтні порти Arma і default web port
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
- [x] Додати versioned migration runner для `web.db` з compatibility upgrade, schema metadata і post-schema job-store maintenance
- [x] Додати one-command `./armactl web` setup flow поверх `armactl web init`/service subcommands
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
- [x] Зафіксувати timezone-explicit web schedule model: browser-local input + IANA timezone, backend UTC normalization, UTC source of truth, local/UTC UI display, timezone-aware next/last run і audit local/timezone/UTC values
- [ ] Реалізувати timezone-explicit `/schedule` input: не приймати bare time без timezone context, fallback UTC з warning, existing OnCalendar трактувати як UTC unless proven otherwise
- [x] Додати game server boot/autostart policy, dashboard warning і web enable/disable controls
- [x] Ввести мінімальний `platform/service_adapter.py` для web service/timer actions; Linux/systemd лишається default backend, CLI/TUI `service_manager` imports лишаються compatibility path
- [ ] Переглянути UI `/schedule`: рознести timer/autostart/restart-now у спокійніші секції без візуального перевантаження
- [ ] Спроєктувати host reboot/shutdown controls як owner/admin-only future flow з double-confirm і audit
- [ ] Спроєктувати Windows backend adapter як future architecture: service manager, logs, paths, firewall/process/metrics, install/update flow; не реалізовувати в Linux/systemd web MVP
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
- [x] Почати config schema inventory seed: currently web-safe fields are `game.name`, `game.scenarioId`, `game.maxPlayers`, `game.visible`, `game.gameProperties.battlEye`, `game.gameProperties.serverMaxViewDistance`, and `game.gameProperties.serverMinGrassDistance`
- [x] Complete config schema inventory for `config.json`: current web/TUI/template/read-only sources, validation, restart/runtime unknowns, risk class, permission, UI control type, and next-slice plan; result: `docs/config-schema-inventory.md`
- [x] Classify TUI-only advanced fields before web edit: `bindPort`/`publicPort`, `a2s.port`, `rcon.port`, `game.password`, `game.passwordAdmin`, `rcon.password`; ports and RCON/A2S need future `settings:advanced`; passwords/secrets stay masked and need stronger future secret policy
- [x] Mark local third-person/crossplay/platform candidates: `game.gameProperties.disableThirdPerson` is template-confirmed but needs upstream/default/restart verification; crossplay/platform keys are not locally confirmed and must not be invented
- [ ] Next safe config toggles slice: verify candidate keys/value shapes/defaults/restart behavior against official docs or real config samples, add descriptor-backed allowlist/tests, keep secrets/raw JSON out of normal UI, and keep `/files` out of config editing
- [x] Зафіксувати, що normal `config.json` editing живе у `/config`, не у `/files`; secrets/RCON/admin sensitive fields не показувати casually
- [x] Додати `armactl-web.service` template і service commands
- [x] Додати login rate limiting / auth abuse throttling для web login
- [x] Додати явне HTTPS/external-bind warning у web UI/runtime summary
- [ ] Додати optional IP allowlist / trusted proxy handling для remote deployments
- [x] Додати VM smoke checklist docs для web panel
- [x] Додати reverse proxy / HTTPS deployment docs
- [x] Зафіксувати `deus-gateway` як окремий deployment repo/tool для Proxmox/edge routing, не як частину armactl core
- [x] Зафіксувати тимчасову multi-VM smoke-схему: `8766 -> serhiivka`, `8767 -> chervonopilya`, `tryzub pending`
- [x] Зафіксувати майбутню доменну схему: `serhiivka.<domain>`, `chervonopilya.<domain>`, `tryzub.<domain>` після rollout, `dashboard.<domain>` для gateway/status
- [x] Додати короткий VM smoke checklist для паралельних armactl-web інстансів через gateway
- [x] Провести фінальний architecture/shortcut audit після gateway-відхилення перед поверненням у main web feature plan; результат: `docs/final-gateway-hub-audit-results-20260621.md`
- [x] Зафіксувати future hub/product-layer design перед реалізацією: central login/instance picker/plans live in hub, VM-local armactl-web keeps server actions/audit/recovery
- [x] Повернутись до main web feature plan після чистого audit і закрити перший feature slice: server update check/update flow
- [x] Провести TUI/Web parity inventory перед config schema work: звірити TUI main/manage screens з web routes і розділити стан на done/future/deliberately-not-now
- [x] Зафіксувати current web parity done: login/session/CSRF, dashboard/status, start/stop/restart, install/repair/update jobs, `/updates`, basic `/config`, basic `/mods`, game admins, `/players`, `/files`, `/logs`, `/schedule`, bot read-only, public status endpoint
- [ ] Перенести web Host Tests flow з TUI як allowlisted background diagnostic job з audit, bounded output і без raw shell
- [ ] Додати web Maintenance/Cleanup flow: old logs/backups/dumps and unused workshop addons, з dry-run, confirmation, audit і rollback notes where possible
- [ ] Додати bot edit/service web flow через `bot_config`/bot service helpers: token masked, chat IDs validated, service actions audited; current `/bot` stays read-only until then
- [ ] Додати modpack/bulk mod workflows з TUI parity: import append/replace, export, dedupe/cleanup, explicit confirmations and audit
- [ ] Deliberately not now: normal raw JSON editor, config editing through `/files`, terminal/host controls, file delete/edit/overwrite, banlist, paid/security-sensitive tools before policy/users/security foundation
- [ ] Продовжити main web feature plan після config schema inventory: player history/banlist, timezone UX, users/security foundation, settings registry, dashboard redesign integration
- [x] Додати read-only logs/report web views з bounded/redacted output
- [ ] Додати prettier audit JSONL rendering для `/logs` без raw стіни JSON
- [ ] Додати logs/report download-export для allowlisted bounded sources
- [ ] Додати logs live follow/auto-refresh з pause/refresh controls
- [ ] Додати logs filters/search/highlighting для level/source/text і `ERROR`/`WARNING`
- [x] Підключити install/repair flows до background jobs без blocking HTTP requests
- [x] Додати build check/read model для installed server build, latest available build і future optional human-readable version і статусів `up to date` / `update available` / `unknown` / `check failed`
- [x] Додати safe explicit latest-build check/cache через `server:update-check`: dashboard GET читає тільки local appmanifest + `web.db` cache, а SteamCMD `app_info_print` працює лише у background job
- [x] Для update UX не створювати update job, коли installed build == latest available build; показати controlled `Server is already up to date`, audit safe check result без secrets і dashboard `up to date`
- [x] Якщо version check failed або latest unknown, не запускати update автоматично; показати controlled `unknown`/`check failed` і дозволяти operator override тільки за окремою future policy
- [x] Додати dashboard version badge/signal, який не ламає dashboard, якщо latest build недоступний
- [x] Додати окрему /updates сторінку для server build update flow: GET читає тільки persisted/read-model state, показує build fields/states, Check for updates, Update server лише коли backend gate дозволяє, і dashboard лишає компактний update-блок з management link
- [x] Підключити update server flow до окремого background job без blocking HTTP requests або route shell-out
- [x] Додати audit/progress/log visibility для update intent, enqueue, running output, controlled failure і outcome
- [x] Зафіксувати first-slice running-server update policy: web update дозволений тільки коли game server stopped; running fail-closed без job/worker/SteamCMD mutation
- [x] Додати `server:update` permission і окремий policy/feature gate для paid tiers без змішування tiers з permissions
- [x] Винести update backend у adapter/service layer: SteamCMD/app manifest/log/version metadata/systemd/path logic не живе в routes/templates
- [x] Додати тести для update dedupe/idempotency, controlled failure, audit events, dashboard version/update states і no-secret log handling
- [x] Додати `web_jobs` maintenance migration для старих duplicate active rows: detect/report/resolve дублікати `queued`/`running` за `(kind, instance)` перед production release або наступним install/repair/update slice
- [x] Додати SQLite migration/index для active job lookup за `(kind, instance, status, created_at, id)` або еквівалентний schema-backed guard
- [x] Додати diagnostics/health check для `web_jobs`, який показує duplicate active jobs і job-store integrity проблеми у `/jobs` або diagnostics page, а не ховає їх як hidden debt
- [ ] Follow-up owner: наступний jobs/update slice. Додати formal stop/drain/restart ownership policy, standalone worker hardening, release/VM smoke, і перевірити `EXPLAIN QUERY PLAN`/latency active lookup на production-scale job history перед standalone worker daemon.
- [x] Додати safe web edit/save для базових полів `config.json` через `config_manager`
- [x] Додати audit logging для safe web config save з changed fields і backup path
- [x] Додати intent/outcome audit logging для file upload і web install/repair job enqueue після VM smoke audit cleanup
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
- [x] Додати versioned migration runner для `players.db` зі schema metadata, idempotent old-shape upgrade і mode `0600`
- [x] Додати explicit web refresh ingestion з current RCON/player_view roster без вигаданих ID і без IP storage
- [x] Додати `/players` read-only registry page з search по nickname/ID
- [x] Розділити player registry / moderation boundary: current roster source, SQLite registry storage, web page DTO/loaders і refresh+audit workflow; reliable identity і no-IP-by-default лишаються правилами
- [x] Закрити player refresh failure outcome audit should-fix: source/storage failures write controlled failure outcome audit, failed intent audit aborts roster/persist, and successful persist with outcome-audit failure reports an audit problem without rolling back `players.db`
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
- [x] Зафіксувати web security foundation для users/roles/permissions/tiers/recovery/IP allowlist/mobile device trust перед premium/terminal/host/raw-config/destructive-file/SAT slices
- [ ] Спроєктувати `web_users`/roles/permissions schema окремо від Arma/game admins і `players.db`
- [ ] Реалізувати System Admin Panel (`/system/users` або `/users`) для create user, role change, password change/reset, disable/enable, session reset і audit усіх змін
- [ ] Додати last-owner guard: заборонити видалити, disable або понизити останнього owner/deus-level web user
- [ ] Додати owner bootstrap/recovery CLI: перший owner тільки локально/CLI; one-time recovery token з коротким TTL, audit/log записом, password reset або create-new-owner flow
- [ ] Додати central policy/feature-gate service для ролей, named permissions, entitlements, IP/trusted-proxy/device gates і denied-action audit
- [ ] Додати feature entitlement model, якщо продукт матиме paid tiers; basic/plus/premium/mega тримати окремо від roles і permissions
- [ ] Додати typed settings registry для runtime/product/security settings замість route/template workaround flags
- [ ] Спроєктувати IP allowlist/trusted proxy model: CIDR, labels, enabled/disabled, expiry, per-user/per-feature scope, trusted forwarded headers, audit і local CLI/SSH recovery
- [ ] Додати 2FA/passkey future hook для high-impact actions і user-management flows
- [ ] Спроєктувати mobile device trust future design: per-device keypair, registered public key, challenge signatures, revocation; не замінює user auth/permissions
- [ ] Проаудитити всі dangerous features against central policy before implementation: raw config editor, advanced config, host controls, command palette, terminal, banlist, file edit/delete/overwrite, SAT runtime edits, user/tier/allowlist management, paid/premium tools
- [ ] Якщо current routes/templates/services boundary заважає policy/users/settings foundation, спершу зробити refactor/module boundary, потім feature; не додавати workaround у routes/templates
- [ ] Додати remote HTTPS login smoke checklist

---

## Історична стартова точка

Для початкового CLI/TUI циклу старт був **Phase 0 → Phase 1 → Phase 2 →
Phase 3 → Phase 4**. Цей фундамент уже реалізований.

Поточний web foundation реалізований у **Phase 16: Web interface foundation
(`feat/web-interface`)**. Player refresh failure outcome audit should-fix
закрито. Найближчі future slices: settings registry, feature
policy/roles/tiers, broader platform adapters, banlist, destructive file
workflows, SAT/mod runtime settings, and VM/release hardening.
