# Запуск под Linux

Ниже — два варианта: простой запуск через `run.sh` и вариант «по красоте» через `systemd`.

## 1) Быстрый запуск (через bash)

1) Установи зависимости системы:

**Debian/Ubuntu:**
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

2) Распакуй архив, зайди в папку и поставь права на скрипты:
```bash
chmod +x install.sh run.sh
```

3) Установи зависимости в виртуальное окружение:
```bash
./install.sh
```

4) Открой `SH_discord_bot_split/.env` и вставь токен:
```bash
nano SH_discord_bot_split/.env
```

5) Запусти:
```bash
./run.sh
```

> Чтобы бот не падал при закрытии SSH — используй `screen` или `tmux`.

---

## 0) Если ты запускаешь на хостинге/панели (BotHost и т.п.)

На многих панелях зависимости ставятся **только** из `requirements.txt` в корне проекта.
В этом архиве он уже есть.

Обычно нужно указать:

- **Команда сборки (install/build):**
  ```bash
  python -m pip install -r requirements.txt
  ```
- **Команда запуска (start/run):**
  ```bash
  python SH_discord_bot_split/main.py
  ```

Если панель не использует `.env`, то лучше выставить переменную окружения `DISCORD_TOKEN` прямо в настройках.

## 2) Автозапуск через systemd (рекомендовано)

### Шаг A — размести бота
Например, в `/opt/shbot`:
```bash
sudo mkdir -p /opt/shbot
sudo cp -r ./* /opt/shbot/
```

### Шаг B — создай отдельного пользователя
```bash
sudo useradd -r -m -d /opt/shbot -s /usr/sbin/nologin shbot
sudo chown -R shbot:shbot /opt/shbot
```

### Шаг C — установи зависимости
```bash
cd /opt/shbot
sudo -u shbot chmod +x install.sh run.sh
sudo -u shbot ./install.sh
sudo -u shbot nano SH_discord_bot_split/.env
```

### Шаг D — включи сервис
1) Отредактируй пути в unit-файле (если ставишь не в `/opt/shbot`):
```bash
sudo nano /opt/shbot/systemd/sh-discord-bot.service
```

2) Установи unit в systemd:
```bash
sudo cp /opt/shbot/systemd/sh-discord-bot.service /etc/systemd/system/sh-discord-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now sh-discord-bot.service
```

3) Проверка статуса и логов:
```bash
sudo systemctl status sh-discord-bot.service
sudo journalctl -u sh-discord-bot.service -f
```
