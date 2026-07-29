# Jarvis

Jarvis — персональный AI-ассистент для управления инфраструктурой пользователя.

Jarvis работает как Telegram-бот с командами `/start`, `/help`, `/ping`,
`/status` и временной диагностической командой `/tool system_info`. Обычные
текстовые сообщения передаются подключённому LLM через независимый от
конкретного поставщика слой `AIClient`.

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
Аудит удалённых операций пишется отдельно в `logs/audit.log`; INFO-записи не
содержат внутренних команд и полных stdout/stderr.
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
- `JARVIS_HOSTS_CONFIG` — необязательный путь к строгой YAML-конфигурации
  удалённых серверов; по умолчанию `/etc/jarvis/hosts.yaml`.

OpenAI подключён через официальный современный SDK и Responses API. Сетевой
запрос ограничен таймаутом в 30 секунд.

Если allowlist пуст, Jarvis откажется запускаться, пока
`ALLOW_PUBLIC_ACCESS` не будет явно установлен в `true`. Неавторизованные
пользователи не могут отправлять запросы в LLM.

## Тесты

```bash
python -m pytest
```

## Tools

Tool Framework отделяет разрешённые локальные операции от Telegram и LLM.
Автоматический вызов инструментов моделью пока не подключён. Встроенный
read-only инструмент `system_info` возвращает основные сведения о хосте и
процессе Jarvis.

Запуск из CLI:

```bash
python scripts/run_tool.py system_info
```

Для регистрации нового инструмента:

1. Создайте класс-наследник `app.tools.base.Tool`.
2. Задайте уникальные `name` и `description`.
3. Реализуйте `parameters()` и возвращающий обычный `dict` метод
   `execute(**kwargs)`.
4. Зарегистрируйте экземпляр через `ToolRegistry.register()`, затем передайте
   реестр в `ToolManager`.

`ToolManager` измеряет длительность, перехватывает исключения, журналирует
результат и всегда возвращает структурированный `ToolResult`. Повторная
регистрация одинакового имени запрещена.

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

## Безопасный удалённый мониторинг

Jarvis поддерживает только две встроенные read-only SSH-операции:
`remote_system_info` и `remote_service_status`. Произвольные shell-команды,
парольная аутентификация, SSH agent, автоматический поиск ключей и
автоматическое принятие host key не поддерживаются.

Скопируйте `config/hosts.example.yaml` в `/etc/jarvis/hosts.yaml` вручную и
замените только тестовые адреса и allowlist сервисов. Файл примера использует
зарезервированные документационные IP-адреса. Приложение и установщик не
создают production `hosts.yaml`, `known_hosts` или приватные ключи.

Рекомендуемая подготовка каждого сервера:

1. Создайте отдельного непривилегированного пользователя `jarvis-monitor`,
   запретите root-вход и не добавляйте sudoers.
2. Сгенерируйте отдельный Ed25519-ключ для каждого сервера. Никогда не
   публикуйте приватный ключ и не добавляйте его в Git.
3. Ограничьте запись в `authorized_keys` на сервере параметрами, подходящими
   вашей SSH-политике (как минимум запретите forwarding и PTY). Доступ этого
   пользователя должен оставаться read-only.
4. Получите fingerprint host key по доверенному административному каналу и
   сверьте его вручную. Только после проверки добавьте запись в
   `/etc/jarvis/known_hosts`. Jarvis принципиально не заполняет этот файл
   автоматически.
5. Установите файлы с правами:

```bash
sudo install -d -o root -g jarvis -m 0750 /etc/jarvis/keys
sudo install -o root -g jarvis -m 0640 ./host_ed25519 \
    /etc/jarvis/keys/host_ed25519
sudo chown root:jarvis /etc/jarvis/hosts.yaml /etc/jarvis/known_hosts
sudo chmod 0640 /etc/jarvis/hosts.yaml /etc/jarvis/known_hosts
```

Проверка разрешённых инструментов из CLI:

```bash
python scripts/run_tool.py system_info
python scripts/run_tool.py remote_system_info --host crypto
python scripts/run_tool.py remote_service_status \
    --host crypto --service crypto-paper.timer
```

Telegram поддерживает эквивалентные allowlisted-команды:

```text
/tool system_info
/tool remote_system_info crypto
/tool remote_service_status crypto crypto-paper.timer
```

Имя systemd unit должно одновременно соответствовать безопасному шаблону и
присутствовать в `allowed_services` выбранного хоста. Внутренняя SSH-команда,
пути ключей, конфигурация и traceback пользователю не возвращаются.
