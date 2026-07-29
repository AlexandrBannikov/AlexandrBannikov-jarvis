# Jarvis

Jarvis — персональный AI-ассистент для управления инфраструктурой пользователя.

Jarvis работает как Telegram-бот с командами `/start`, `/help`, `/ping` и
`/status`. Обычные текстовые сообщения передаются подключённому LLM через
независимый от конкретного поставщика слой `AIClient`.

## Требования

- Python 3.12+
- Telegram Bot Token, полученный у [@BotFather](https://t.me/BotFather)
- API-ключ OpenAI

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
API-ключи и полные ответы модели в журнал не записываются.

## Настройка LLM

Пример конфигурации OpenAI:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-5.5
OPENAI_BASE_URL=
```

Переменные окружения:

- `TELEGRAM_BOT_TOKEN` — обязательный токен Telegram-бота.
- `LLM_PROVIDER` — поставщик LLM; сейчас поддерживается `openai`.
- `OPENAI_API_KEY` — ключ OpenAI. Если он отсутствует, бот вернёт понятное
  сообщение без аварийного завершения.
- `OPENAI_MODEL` — используемая модель, по умолчанию `gpt-5.5`.
- `OPENAI_BASE_URL` — необязательный совместимый endpoint.

OpenAI подключён через официальный современный SDK и Responses API. Сетевой
запрос ограничен таймаутом в 30 секунд.

## Тесты

```bash
python -m pytest
```

## systemd

Пример unit-файла находится в `config/jarvis.service`. Перед использованием
при необходимости измените пользователя и группу, затем скопируйте unit-файл
в системный каталог systemd вручную.
