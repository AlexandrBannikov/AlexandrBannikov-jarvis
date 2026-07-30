# Persistent memory

Jarvis stores durable, owner-scoped working context in SQLite. It does not
depend on Telegram chat history or process memory. Memory is disabled by
default and production is not migrated until `MEMORY_ENABLED=true` is
deliberately rolled out.

## Architecture

`MemoryStorage` owns schema version 2 and parameterized SQL. `MemoryService`
enforces ownership, TTL, confidence, importance, normalized keys and upserts.
`MemoryExtractor` conservatively recognizes explicit facts and common project
events without requiring an LLM. `MemoryContextBuilder` creates a bounded,
grouped block which `JarvisAgent.ask` appends after the fixed system prompt.
Stored context is labelled potentially stale and never becomes a security
instruction.

Memory scopes are `environment`, `project`, `user_preference`,
`session_summary`, and separately owned `system` records. Telegram user IDs
are trusted ownership identifiers. Owner `0` is reserved for bootstrap/system
data; normal write tools receive their owner from the handler, never from
model arguments.

## SQLite schema

`memories` contains owner, scope, namespace, normalized key, JSON value,
summary, source, confidence, importance, timestamps, optional expiry and
active state. Its partial unique index makes active
`(owner_id, scope, namespace, key)` updates atomic. `projects` has a unique
`(owner_id, project_key)`. `project_events` references a project and has a
unique per-project deduplication key. Additive migration preserves legacy
records as owner `0`. Reminders remain in their independent database.

Expired records are excluded from recall and model context. Session summaries
created by automatic extraction expire after 14 days. Newer upserts replace
the same normalized fact; ranking prefers importance, confidence and recency.

## Automatic saving and secrets

Automatic extraction accepts explicit “Запомни …” statements and deterministic
forms such as project paths, `1012 passed`, `commit 35d31a1`, next steps,
`git push completed`, `working tree clean`, and `production not changed`.
Greetings, questions and arbitrary terminal lines are ignored.

API keys, secrets, tokens, passwords, cookies, authorization headers, `.env`
references and private keys are rejected before persistence. Diagnostics apply
redaction again and logs contain metadata only, never full values. There is no
ordinary-chat override for this policy.

## Configuration

The supported settings are `MEMORY_ENABLED`, `MEMORY_DB_PATH` (default
`/var/lib/jarvis/memory.db`), `MEMORY_MAX_CONTEXT_ITEMS`,
`MEMORY_MAX_CONTEXT_CHARS`, and `MEMORY_AUTO_EXTRACT_ENABLED`. Legacy
`MEMORY_MAX_CONTEXT` and `MEMORY_AUTOSAVE` remain accepted.

## Tools and Telegram

The agent has separate write/read tools: `remember_fact`, `forget_memory`,
`update_project_memory`; and `recall_memory`, `list_project_memory`,
`get_project_memory_status`. Legacy names remain for compatibility. `/memory` and
`/memory_status` show a short safe overview, `/memory_projects` lists projects,
and `/memory_forget <id>` soft-deletes only the current owner’s record.

## CLI and bootstrap

Read-only examples:

```bash
python -m app.memory.cli --db /var/lib/jarvis/memory.db status --owner-id 123
python -m app.memory.cli --db /var/lib/jarvis/memory.db list --owner-id 123
python -m app.memory.cli --db /var/lib/jarvis/memory.db project crypto-bot --owner-id 123
python -m app.memory.cli --db /var/lib/jarvis/memory.db context --owner-id 123
```

Add `--json` before the subcommand for JSON. Read-only commands refuse to
create or migrate a missing database. An administrator may explicitly seed an
offline/test database:

```bash
python -m app.memory.cli --db /tmp/memory.db bootstrap config/memory_bootstrap.json
```

Bootstrap uses the same atomic upserts and is idempotent. User data is JSON,
not hardcoded in Python.

## Backup and restore

Stop the process that writes the memory database or use SQLite’s online backup
API, then copy `memory.db` together with any `-wal`/`-shm` files. Validate the
copy with `PRAGMA integrity_check` and the CLI `status`. To restore, keep the
original as a rollback copy, restore files with the service account’s
ownership and restrictive permissions, run read-only diagnostics, then start
the service. Never replace the reminders database during this procedure.

Production rollout risks include filesystem permissions under `/var/lib`,
disk space, backup consistency, additive migration time, and assigning legacy
owner `0` records. Validate a copied database first and enable the feature in a
separate deployment step.
