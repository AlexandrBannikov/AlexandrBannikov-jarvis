# Jarvis

Jarvis — персональный AI-ассистент для управления инфраструктурой пользователя.

На текущем этапе Jarvis работает как Telegram-бот с командами `/start`,
`/help`, `/ping` и `/status`.

## Требования

- Python 3.12+
- Telegram Bot Token, полученный у [@BotFather](https://t.me/BotFather)

## Установка

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Укажите настоящий токен в `.env`. Приложение читает конфигурацию только из
переменных окружения, поэтому перед запуском экспортируйте её:

```bash
set -a
source .env
set +a
python -m app.main
```

Логи одновременно выводятся в консоль и записываются в `logs/jarvis.log`.

## Тесты

```bash
python -m pytest
```

## systemd

Пример unit-файла находится в `config/jarvis.service`. Перед использованием
при необходимости измените пользователя и группу, затем скопируйте unit-файл
в системный каталог systemd вручную.
