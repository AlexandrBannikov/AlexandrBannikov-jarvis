"""Reminder schema migration entry point."""

from app.reminders.storage import SCHEMA_VERSION, ReminderStorage


def migrate(storage: ReminderStorage) -> int:
    storage.initialize()
    if not storage.validate_schema():
        raise RuntimeError("Reminder schema validation failed")
    return SCHEMA_VERSION
