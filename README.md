# Jarvis

Jarvis — персональный AI-ассистент для управления инфраструктурой пользователя.

Jarvis работает как Telegram-бот с командами `/start`, `/help`, `/ping` и
`/status`. Обычные текстовые сообщения передаются подключённому LLM через
независимый от конкретного поставщика слой `AIClient`.

## Требования

- Python 3.12+
- Telegram Bot Token, полученный у [@BotFather](https://t.me/BotFather)
- API-ключ OpenAI

API OpenAI оплачивается отдельно от подписки ChatGPT. Проверяйте актуальные
тарифы и лимиты в кабинете OpenAI.

## Создание Telegram-бота

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram.
2. Выполните `/newbot` и следуйте инструкциям.
3. Сохраните выданный токен только в защищённом environment-файле.
4. Получите свой числовой Telegram user ID, например через информационного
   бота, которому вы доверяете, или через Telegram Bot API после отправки
   сообщения новому боту.

Не публикуйте Telegram-токен или API-ключ, не отправляйте их в чаты и никогда
не добавляйте в Git.

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

Пример локальной конфигурации OpenAI:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-5.5
OPENAI_BASE_URL=
LOG_LEVEL=INFO
TELEGRAM_ALLOWED_USER_IDS=123456789
ALLOW_PUBLIC_ACCESS=false
```

Переменные окружения:

- `TELEGRAM_BOT_TOKEN` — обязательный токен Telegram-бота.
- `LLM_PROVIDER` — поставщик LLM; сейчас поддерживается `openai`.
- `OPENAI_API_KEY` — ключ OpenAI. Если он отсутствует, бот вернёт понятное
  сообщение без аварийного завершения.
- `OPENAI_MODEL` — используемая модель, по умолчанию `gpt-5.5`.
- `OPENAI_BASE_URL` — необязательный совместимый endpoint.
- `LOG_LEVEL` — уровень журналирования: `DEBUG`, `INFO`, `WARNING`, `ERROR`
  или `CRITICAL`.
- `TELEGRAM_ALLOWED_USER_IDS` — разделённый запятыми список разрешённых
  Telegram user ID.
- `ALLOW_PUBLIC_ACCESS` — явное разрешение публичного доступа. По умолчанию
  `false`; включать его следует только осознанно.

OpenAI подключён через официальный современный SDK и Responses API. Сетевой
запрос ограничен таймаутом в 30 секунд.

Если allowlist пуст, Jarvis откажется запускаться, пока
`ALLOW_PUBLIC_ACCESS` не будет явно установлен в `true`. Неавторизованные
пользователи не могут отправлять запросы в LLM.

## Тесты

```bash
python -m pytest
```

## systemd

Production-конфигурация хранится в `/etc/jarvis/jarvis.env` с владельцем
`root:root` и правами `600`. Каталог `/etc/jarvis` имеет права `750`.
Создайте файл вручную по образцу `.env.example` или запустите установщик,
который создаст только шаблон с пустыми секретами:

```bash
sudo scripts/install_service.sh
```

Установщик создаёт отдельного системного пользователя `jarvis`, каталоги
`logs` и `data`, устанавливает unit-файл и выполняет `systemctl daemon-reload`.
Существующий `/etc/jarvis/jarvis.env` не перезаписывается. Сервис никогда не
запускается установщиком автоматически.

После заполнения production-конфигурации проверьте её без вывода значений:

```bash
/opt/jarvis/venv/bin/python /opt/jarvis/scripts/check_config.py
```

Затем сервис можно запустить и включить в автозагрузку:

```bash
sudo systemctl start jarvis
sudo systemctl enable jarvis
```

Команды диагностики:

```bash
systemctl status jarvis
journalctl -u jarvis -n 100 --no-pager
tail -n 100 /opt/jarvis/logs/jarvis.log
```

Unit запускается без root-доступа и использует systemd hardening. Запись
разрешена только в `/opt/jarvis/logs` и `/opt/jarvis/data`.
