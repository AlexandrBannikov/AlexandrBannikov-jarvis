# Restricted remote account для SSH Agent

Используйте отдельного непривилегированного пользователя `jarvis-ops`.
Unrestricted root SSH не поддерживается.

Требования:

- password login и root login запрещены, только public-key authentication;
- нет unrestricted sudo и интерактивной административной роли;
- нет записи в source tree, `.env`, базы данных, ключи или token storage;
- только необходимые read/traverse permissions;
- чтение только утверждённых systemd units и нужных journal records;
- Git metadata доступна на чтение, repository не доступен на запись.

Для journal можно применить минимальную Unix group. Для repository traversal —
точечные read-only ACL. Tightly scoped sudo допустим лишь когда другого
механизма нет, после отдельного аудита конкретных фиксированных операций.

В `authorized_keys` по возможности примените:

```text
no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,no-user-rc
```

Совместимость ограничений проверьте с OpenSSH конкретной системы. Не
добавляйте forced generic shell command: он снова создаёт произвольное
исполнение. Forced command допустим только как отдельно спроектированный и
аудированный fixed-operation wrapper; этот проект его не устанавливает.

Регулярно проверяйте membership групп и ACL, rotate dedicated key и немедленно
отзывайте его при компрометации. Account не должен иметь доступа к private
keys, deployment secrets или данным других приложений.
