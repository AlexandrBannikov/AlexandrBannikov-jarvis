# Развёртывание SSH Agent

SSH Agent запускается в fail-closed режиме. Jarvis продолжает работать, если
SSH выключен или его runtime-конфигурация некорректна; SSH tools возвращают
безопасную ошибку, а `/health` показывает причину. До завершения ручной
проверки не включайте feature flag.

## Runtime filesystem

Рекомендуемая структура:

```text
/etc/jarvis/
├── servers.json
└── ssh/
    ├── known_hosts
    ├── jarvis_ops_ed25519
    └── crypto_ops_ed25519
```

Владелец — service account `jarvis:jarvis`. Режимы: `/etc/jarvis` и каталог
`ssh` — `0700`; `servers.json` и private keys — `0600`; dedicated
`known_hosts` — `0600` (допустим `0644`, если это разрешено локальной
политикой). Validator ничего не исправляет автоматически и не читает
содержимое ключей.

## Переменные окружения

- `JARVIS_SSH_ENABLED=false` — SSH выключен по умолчанию.
- `JARVIS_SERVERS_CONFIG=/etc/jarvis/servers.json` — strict JSON registry.

Встроенные безопасные defaults: 10 запросов в минуту на пользователя, burst
3; concurrency global 4, per-user 2, per-server 2. Эти пределы передаются
только trusted bootstrap-кодом и не доступны model tool arguments.

Перед включением выполните:

```text
python -m app.ssh_agent.cli validate-config
python -m app.ssh_agent.cli validate-runtime
python -m app.ssh_agent.cli health
```

Команды не подключаются к серверам.

## Host-key pinning

Получите host key через доверенный административный канал и независимо
сверьте fingerprint. Добавьте точный ключ в dedicated `known_hosts`.
`ssh-keyscan` нельзя считать единственным источником доверия.
`StrictHostKeyChecking=no` запрещён. Изменившийся ключ расследуется; он не
перезаписывается автоматически.

## Private keys

Используйте отдельный ключ для каждого сервера и только для restricted
account. Не используйте личные или root keys и не коммитьте ключи. Ключ без
passphrase допустим лишь после оценки угроз для non-interactive service.
Для rotation добавьте новый проверенный ключ, обновите registry, проверьте
readiness, затем отзовите старый. При компрометации сначала отзовите public
key на сервере, выключите SSH feature и замените материал ключа.

## Включение и rollback

После подготовки выставьте `JARVIS_SSH_ENABLED=true`, перезапустите
`jarvis.service` и убедитесь, что health показывает `SSH_READY`.

Rollback: выставьте `JARVIS_SSH_ENABLED=false`, перезапустите service и
проверьте `SSH_DISABLED`. При подозрении на компрометацию удалите/отзовите и
rotate соответствующие keys. Это не затрагивает reminders, Web Search,
Project Memory и остальные tools.
