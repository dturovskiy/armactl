# Міграція на нову структуру armactl

Цей документ описує безпечний процес міграції існуючого Arma Reforger сервера зі старих розкиданих шляхів на нову уніфіковану структуру `~/armactl-data/default/`.

## Старі шляхи (до міграції)

```text
~/arma-reforger/                              # install dir
~/.config/ArmaReforgerServer/config.json      # конфіг
~/bin/start-armareforger.sh                   # start script
/etc/systemd/system/armareforger.service      # service
/etc/systemd/system/armareforger-restart.timer # timer
```

## Нові шляхи (після міграції)

```text
~/armactl-data/default/
├── server/                  # ← ~/arma-reforger
├── config/
│   └── config.json          # ← ~/.config/ArmaReforgerServer/config.json
├── backups/                 # нова директорія
├── state.json               # нова директорія
└── start-armareforger.sh    # ← ~/bin/start-armareforger.sh (перегенерований)

/etc/systemd/system/armareforger.service              # оновлений
/etc/systemd/system/armareforger-restart.service       # оновлений (якщо є)
/etc/systemd/system/armareforger-restart.timer         # без змін
```

---

## Стратегія: copy → switch → test → cleanup

> **Увага:** Ніколи не робити `mv` на працюючому сервері. Завжди `rsync`/`cp` → переключення → тест → видалення старого.

---

## Покроковий план

### Крок 1. Створити нову структуру

```bash
mkdir -p ~/armactl-data/default/server
mkdir -p ~/armactl-data/default/config
mkdir -p ~/armactl-data/default/backups
```

### Крок 2. Зупинити сервер

```bash
sudo systemctl stop armareforger
```

Перевірити, що зупинився:

```bash
sudo systemctl status armareforger --no-pager
```

### Крок 3. Скопіювати сервер у нове місце

```bash
rsync -a ~/arma-reforger/ ~/armactl-data/default/server/
```

> `rsync -a` зберігає permissions, timestamps, symlinks.

### Крок 4. Скопіювати конфіг

```bash
cp ~/.config/ArmaReforgerServer/config.json ~/armactl-data/default/config/config.json
```

### Крок 5. Зробити backup старого service

```bash
sudo cp /etc/systemd/system/armareforger.service /etc/systemd/system/armareforger.service.bak
```

### Крок 6. Створити новий start script

Створити файл `~/armactl-data/default/start-armareforger.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ROOT="/home/$(whoami)/armactl-data/default"

cd "${INSTANCE_ROOT}/server"

exec "${INSTANCE_ROOT}/server/ArmaReforgerServer" \
  -config "${INSTANCE_ROOT}/config/config.json" \
  -profile "${INSTANCE_ROOT}/config" \
  -maxFPS 60
```

Зробити виконуваним:

```bash
chmod +x ~/armactl-data/default/start-armareforger.sh
```

### Крок 7. Оновити `armareforger.service`

Змінити у файлі `/etc/systemd/system/armareforger.service`:

```ini
[Service]
WorkingDirectory=/home/<user>/armactl-data/default/server
ExecStart=/home/<user>/armactl-data/default/start-armareforger.sh
```

> Замінити `<user>` на реального користувача.

### Крок 8. Перечитати systemd

```bash
sudo systemctl daemon-reload
```

### Крок 9. Запустити сервер

```bash
sudo systemctl start armareforger
```

### Крок 10. Перевірити

```bash
# Статус сервісу
sudo systemctl status armareforger --no-pager

# Порти
ss -lunpt | grep -E '2001|17777|19999'

# Логи
journalctl -u armareforger -n 50 --no-pager
```

**Що має бути:**
- [ ] `armareforger.service` у стані `active (running)`
- [ ] Порти 2001, 17777, 19999 слухаються
- [ ] В логах нема помилок

### Крок 11. Прибрати старі шляхи

> **Тільки після повної перевірки!** Якщо щось не так — просто відкоти service назад із `.bak`.

```bash
rm -rf ~/arma-reforger
rm -rf ~/.config/ArmaReforgerServer
rm ~/bin/start-armareforger.sh
sudo rm /etc/systemd/system/armareforger.service.bak
```

---

## Чекліст міграції

- [ ] Створено `~/armactl-data/default/` зі структурою
- [ ] Зупинено сервер
- [ ] Скопійовано server files через `rsync`
- [ ] Скопійовано `config.json`
- [ ] Зроблено backup старого service
- [ ] Створено новий `start-armareforger.sh`
- [ ] Оновлено `armareforger.service`
- [ ] `systemctl daemon-reload`
- [ ] Сервер запущено
- [ ] Порти слухаються
- [ ] Логи чисті
- [ ] Старі шляхи прибрано

---

## Відкат (якщо щось пішло не так)

```bash
# Зупинити сервер
sudo systemctl stop armareforger

# Відновити старий service
sudo cp /etc/systemd/system/armareforger.service.bak /etc/systemd/system/armareforger.service
sudo systemctl daemon-reload

# Запустити зі старими шляхами
sudo systemctl start armareforger
```

Старі файли (`~/arma-reforger`, `~/.config/ArmaReforgerServer`) ще на місці — нічого не пропало.

---

## Після міграції

Створити початковий `state.json`:

```bash
cat > ~/armactl-data/default/state.json << 'EOF'
{
  "server_installed": true,
  "instance_root": "/home/<user>/armactl-data/default",
  "install_dir": "/home/<user>/armactl-data/default/server",
  "config_path": "/home/<user>/armactl-data/default/config/config.json",
  "service_name": "armareforger.service",
  "timer_name": "armareforger-restart.timer",
  "migrated_from": "legacy",
  "migrated_at": "<timestamp>"
}
EOF
```

Цей файл дозволить `armactl detect` одразу знайти інстанс.
