# Offline SSH deployment kit

`app.ssh_agent.deployment` создаёт reviewable manifest и scripts, но никогда
их не исполняет. Package не импортирует SSH transport, socket или subprocess.

```text
python -m app.ssh_agent.deployment.cli validate-inventory config/ssh-deployment.example.json
python -m app.ssh_agent.deployment.cli plan config/ssh-deployment.example.json
python -m app.ssh_agent.deployment.cli render INVENTORY --output build/ssh-deployment
python -m app.ssh_agent.deployment.cli inspect-manifest build/ssh-deployment/deployment-manifest.json
python -m app.ssh_agent.deployment.cli verify-rendered build/ssh-deployment
```

Example inventory содержит только placeholders и disabled server. Перед
реальными действиями оператор заменяет inventory своими проверенными aliases,
addresses, users, paths и service allowlists, повторно запускает validation,
читает manifest и каждый script.

Mutating scripts без флага ничего не делают. Сначала используется `--dry-run`,
после review — `--apply`. Restart выполняется только с отдельным
`--restart-service`. Remote scripts не вызывают SSH: оператор вручную копирует
их на независимо подтверждённый host.

Key generation требует явного выбора passphrase policy. Host key trust
автоматически не устанавливается: candidate fingerprint сверяется через
независимый административный канал. Rollback выключает только SSH Agent,
сохраняет остальные environment settings и не удаляет keys по умолчанию.
