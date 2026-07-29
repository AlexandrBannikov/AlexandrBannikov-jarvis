# Jarvis

Jarvis — персональный AI-ассистент для управления инфраструктурой пользователя.

Jarvis работает как Telegram-бот с командами `/start`, `/help`, `/ping`,
`/status`, `/tools` и диагностической командой `/tool`. Обычные текстовые
сообщения обрабатывает ограниченный `JarvisAgent` через OpenAI Responses API.
Модель может выбирать только зарегистрированные read-only инструменты;
произвольные shell-команды ей не предоставляются.

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
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.5
OPENAI_BASE_URL=
MAX_TOOL_ROUNDS=4
JARVIS_WEB_SEARCH_ENABLED=false
JARVIS_WEB_SEARCH_CONTEXT_SIZE=medium
LOG_LEVEL=INFO
TELEGRAM_ALLOWED_USER_IDS=123456789
ALLOW_PUBLIC_ACCESS=false
JARVIS_HOSTS_CONFIG=/etc/jarvis/hosts.yaml
JARVIS_SSH_MODE=mock
HEALTH_HOST=127.0.0.1
HEALTH_PORT=8090
TELEGRAM_STARTUP_NOTIFICATION=false
```

Переменные окружения:

- `TELEGRAM_BOT_TOKEN` — обязательный токен Telegram-бота.
- `LLM_PROVIDER` — поставщик LLM; сейчас поддерживается `openai`.
- `OPENAI_API_KEY` — ключ OpenAI. Если он отсутствует, бот вернёт понятное
  сообщение без аварийного завершения.
- `OPENAI_MODEL` — используемая модель, по умолчанию `gpt-5.5`.
- `OPENAI_BASE_URL` — необязательный совместимый endpoint.
- `MAX_TOOL_ROUNDS` — максимальное число последовательных раундов вызова
  инструментов, по умолчанию `4` (допустимо 1–10).
- `JARVIS_WEB_SEARCH_ENABLED` — явно включает встроенный OpenAI web search;
  по умолчанию `false`, отдельный поисковый API-ключ не нужен.
- `JARVIS_WEB_SEARCH_CONTEXT_SIZE` — объём поискового контекста: `low`,
  `medium` или `high`; по умолчанию `medium`.
- `LOG_LEVEL` — уровень журналирования: `DEBUG`, `INFO`, `WARNING`, `ERROR`
  или `CRITICAL`.
- `TELEGRAM_ALLOWED_USER_IDS` — разделённый запятыми список разрешённых
  Telegram user ID.
- `ALLOW_PUBLIC_ACCESS` — явное разрешение публичного доступа. По умолчанию
  `false`; включать его следует только осознанно.
- `JARVIS_HOSTS_CONFIG` — необязательный путь к строгой YAML-конфигурации
  удалённых серверов; по умолчанию `/etc/jarvis/hosts.yaml`.
- `JARVIS_SSH_MODE` — `mock` для первого запуска без SSH или `real` после
  ручной установки и проверки ключей и `known_hosts`.
- `HEALTH_HOST`, `HEALTH_PORT` — локальный health endpoint.
- `TELEGRAM_STARTUP_NOTIFICATION` — одно короткое уведомление первому ID из
  allowlist после успешного старта.

OpenAI подключён через официальный современный SDK и Responses API. Сетевой
запрос ограничен таймаутом в 30 секунд.

Если allowlist пуст, Jarvis откажется запускаться, пока
`ALLOW_PUBLIC_ACCESS` не будет явно установлен в `true`. Неавторизованные
пользователи не могут отправлять запросы в LLM.

## Agent loop и OpenAI tools

`JarvisAgent` отправляет пользовательский текст, системные инструкции и
строгие JSON Schema инструментов в Responses API с `tool_choice="auto"`.
Если модель возвращает `function_call`, Jarvis:

1. повторно проверяет имя инструмента и JSON-аргументы локально;
2. отклоняет лишние поля, пропущенные параметры, пустые строки и неверные типы;
3. повторно проверяет host alias и локальный allowlist systemd unit;
4. запускает инструмент через `ToolManager`;
5. возвращает компактный безопасный `function_call_output` с тем же `call_id`;
6. запрашивает итоговый краткий ответ модели.

Поддерживаются несколько вызовов в одном ответе. После `MAX_TOOL_ROUNDS` цикл
принудительно прекращается. Вывод инструмента ограничен по размеру и не
содержит traceback, SSH-команд, путей ключей, env или секретов. Streaming на
этом этапе не используется.

Модели доступны только:

- `system_info` — сведения о локальном хосте и процессе Jarvis;
- `remote_system_info` — read-only сведения о настроенном удалённом хосте;
- `remote_service_status` — read-only свойства разрешённого systemd unit.

При `JARVIS_WEB_SEARCH_ENABLED=true` в тот же Responses API запрос добавляется
встроенный hosted tool `web_search`. Модель выбирает его с
`tool_choice="auto"` только для актуальной внешней информации или по явной
просьбе пользователя. Для обычной беседы и локальной диагностики поиск не
используется. Ответы поиска содержат видимые названия и HTTP(S)-ссылки
источников. Запросы с признаками ключей, Bearer-токенов, паролей или приватных
ключей не получают доступ к web search.

Параметров `command`, `shell` или произвольного запроса в схемах нет. Модель
не может изменять состояние серверов. Если подходящего инструмента нет, она
должна честно сообщить об ограничении.

Примеры обычных сообщений:

```text
Покажи информацию о сервере Jarvis
Проверь сервер crypto
Работает ли crypto-paper.timer?
```

Удалённые ответы зависят от настроенных `/etc/jarvis/hosts.yaml`,
`/etc/jarvis/known_hosts` и `/etc/jarvis/keys/`. Команда `/tools` показывает
только имена и краткие описания. `/tool` остаётся ручным диагностическим
интерфейсом.

## Тесты

```bash
python -m pytest
```

## Tools

Tool Framework отделяет разрешённые операции от Telegram и модели. Каждый
инструмент имеет строгую схему, локальную валидацию и возвращает
структурированный `ToolResult`.

Запуск из CLI:

```bash
python scripts/run_tool.py system_info
```

Локальная демонстрация agent flow без OpenAI и SSH:

```bash
python scripts/demo_agent_flow.py
```

Скрипт использует fake OpenAI provider и локальный `system_info`; сетевые
запросы не выполняются.

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
`root:jarvis` и правами `640`. Каталог `/etc/jarvis` имеет владельца
`root:jarvis` и права `750`.
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

## Контролируемый первый production-запуск

Секреты вводятся только непосредственно на сервере. Не отправляйте их в чат,
issue, журнал или Git. ChatGPT Plus не включает OpenAI API: API имеет отдельный
биллинг и лимиты.

1. Создайте Telegram-бота через BotFather.
2. Узнайте свой числовой Telegram user ID.
3. Создайте OpenAI API key в кабинете OpenAI.
4. Выполните `sudo python scripts/production_rollout.py prepare`.
5. Вручную откройте `/etc/jarvis/jarvis.env`.
6. Введите секреты непосредственно на сервере.
7. Выполните `sudo python scripts/production_rollout.py validate`.
8. Выполните `sudo python scripts/production_rollout.py install`.
9. Запустите `python scripts/smoke_test.py --offline`.
10. При желании явно выполните `python scripts/smoke_test.py --live`.
11. Выполните `sudo python scripts/production_rollout.py start`.
12. Проверьте `python scripts/production_rollout.py status`.
13. Напишите боту `/health`, затем отправьте обычное сообщение.

Дополнительные безопасные команды:

```bash
sudo python scripts/production_rollout.py rollback
python scripts/check_secrets.py
```

`prepare` не перезаписывает настройки и не создаёт SSH-ключи. `install` не
запускает сервис. `rollback` сохраняет env, hosts, known_hosts, ключи, данные
и логи.

Для первого запуска оставьте `JARVIS_SSH_MODE=mock`. Включайте `real` только
после ручной установки private keys и проверки fingerprints доверенным
каналом. Сначала рекомендуется проверить Telegram и OpenAI, и лишь затем
отдельно переключать SSH в real mode.

Live smoke test никогда не запускается unit-тестами или rollout автоматически.
Он делает минимальные Telegram `getMe` и OpenAI-запрос, а SSH-проверку — только
при явном `--live`, real mode и указанном host alias.

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
