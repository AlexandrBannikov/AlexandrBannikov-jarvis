"""Reminder subsystem tests without network access."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.health import health_payload, set_reminder_health_provider
from app.memory.manager import MemoryManager
from app.memory.storage import MemoryStorage
from app.memory.tools import register_memory_tools
from app.reminders.delivery import ReminderDelivery
from app.reminders.formatter import confirmation, delivery_text, list_text
from app.reminders.models import ParsedReminder, Recurrence
from app.reminders.parser import ReminderParser
from app.reminders.recurrence import (
    deserialize_recurrence,
    next_occurrence,
    serialize_recurrence,
)
from app.reminders.scheduler import ReminderScheduler
from app.reminders.service import ReminderError, ReminderService
from app.reminders.storage import ReminderStorage, utc_text
from app.reminders.tools import register_reminder_tools
from app.tools.registry import ToolRegistry

TZ = ZoneInfo("Asia/Yekaterinburg")
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=TZ)


@pytest.fixture
def storage(tmp_path: Path) -> ReminderStorage:
    value = ReminderStorage(tmp_path / "reminders.db")
    value.initialize()
    return value


@pytest.fixture
def service(storage: ReminderStorage) -> ReminderService:
    return ReminderService(
        storage, default_timezone="Asia/Yekaterinburg", min_lead_seconds=20
    )


def parsed(
    message: str = "Проверить Jarvis",
    when: datetime | None = None,
    recurrence: Recurrence | None = None,
) -> ParsedReminder:
    return ParsedReminder(
        action="create", title=message[:120], message=message,
        local_datetime=when or NOW + timedelta(hours=1),
        timezone="Asia/Yekaterinburg", recurrence=recurrence,
    )


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        ("Напомни через 20 минут проверить", timedelta(minutes=20)),
        ("Напомни через 2 часа проверить", timedelta(hours=2)),
        ("Напомни через 3 дня проверить", timedelta(days=3)),
    ],
)
def test_parser_relative(text: str, delta: timedelta) -> None:
    result = ReminderParser(TZ.key).parse(text, now=NOW)
    assert result and result.local_datetime == NOW + delta


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Напомни завтра в 8:30 проверить", datetime(2026, 7, 30, 8, 30, tzinfo=TZ)),
        ("Напомни послезавтра в 9:15 проверить", datetime(2026, 7, 31, 9, 15, tzinfo=TZ)),
        ("Напомни 15.08.2026 в 12:00 проверить", datetime(2026, 8, 15, 12, 0, tzinfo=TZ)),
        ("Напомни 15 августа в 12:00 проверить", datetime(2026, 8, 15, 12, 0, tzinfo=TZ)),
        ("Напомни в пятницу в 19:00 проверить", datetime(2026, 7, 31, 19, 0, tzinfo=TZ)),
    ],
)
def test_parser_dates(text: str, expected: datetime) -> None:
    result = ReminderParser(TZ.key).parse(text, now=NOW)
    assert result and result.local_datetime == expected


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Каждый день в 8:00 напомни проверить", "daily"),
        ("По будням в 7:30 напомни проверить", "weekdays"),
        ("По выходным в 9:00 напомни проверить", "weekends"),
        ("Каждый понедельник в 9:00 напомни проверить", "weekly"),
        ("Каждую пятницу в 18:00 напомни проверить", "weekly"),
        ("Каждого первого числа в 10:00 напомни проверить", "monthly"),
        ("Каждые 3 дня в 18:00 напомни проверить", "interval_days"),
        ("Каждые 2 недели в 18:00 напомни проверить", "interval_weeks"),
    ],
)
def test_parser_recurrence(text: str, kind: str) -> None:
    result = ReminderParser(TZ.key).parse(text, now=NOW)
    assert result and result.recurrence and result.recurrence.kind == kind


@pytest.mark.parametrize("word", ["вечером", "утром", "днём", "ночью"])
def test_parser_ambiguous_part_of_day(word: str) -> None:
    result = ReminderParser(TZ.key).parse(f"Напомни {word} проверить", now=NOW)
    assert result and result.requires_clarification


def test_parser_time_today_in_past_requires_clarification() -> None:
    result = ReminderParser(TZ.key).parse("Напомни в 08:00 проверить", now=NOW)
    assert result and result.requires_clarification


def test_parser_non_reminder_returns_none() -> None:
    assert ReminderParser(TZ.key).parse("Что такое SQLite?", now=NOW) is None


def test_parser_invalid_timezone() -> None:
    with pytest.raises(ValueError):
        ReminderParser("Invalid/Timezone")


@pytest.mark.parametrize(
    ("rule", "after", "expected"),
    [
        (Recurrence("daily", hour=8), datetime(2026, 7, 29, 7, tzinfo=TZ), datetime(2026, 7, 29, 8, tzinfo=TZ)),
        (Recurrence("daily", hour=8), datetime(2026, 7, 29, 9, tzinfo=TZ), datetime(2026, 7, 30, 8, tzinfo=TZ)),
        (Recurrence("weekdays", hour=7), datetime(2026, 7, 31, 8, tzinfo=TZ), datetime(2026, 8, 3, 7, tzinfo=TZ)),
        (Recurrence("weekends", hour=9), datetime(2026, 7, 31, 8, tzinfo=TZ), datetime(2026, 8, 1, 9, tzinfo=TZ)),
        (Recurrence("weekly", weekday=0, hour=9), NOW, datetime(2026, 8, 3, 9, tzinfo=TZ)),
        (Recurrence("monthly", monthday=31, hour=10), datetime(2026, 8, 31, 11, tzinfo=TZ), datetime(2026, 10, 31, 10, tzinfo=TZ)),
        (Recurrence("interval_days", interval=3, hour=18), NOW, datetime(2026, 8, 1, 18, tzinfo=TZ)),
        (Recurrence("interval_weeks", interval=2, hour=18), NOW, datetime(2026, 8, 12, 18, tzinfo=TZ)),
    ],
)
def test_next_occurrence(rule: Recurrence, after: datetime, expected: datetime) -> None:
    assert next_occurrence(rule, after) == expected


@pytest.mark.parametrize(
    "rule",
    [
        Recurrence("daily", hour=8, minute=30),
        Recurrence("weekdays", hour=7),
        Recurrence("weekends", hour=9),
        Recurrence("weekly", weekday=4, hour=18),
        Recurrence("monthly", monthday=1, hour=10),
        Recurrence("interval_days", interval=3, hour=18),
        Recurrence("interval_weeks", interval=2, hour=18),
    ],
)
def test_recurrence_round_trip(rule: Recurrence) -> None:
    assert deserialize_recurrence(serialize_recurrence(rule)) == rule


@pytest.mark.parametrize(
    "rule",
    [
        Recurrence("invalid"),
        Recurrence("weekly", weekday=9),
        Recurrence("monthly", monthday=0),
        Recurrence("daily", interval=0),
        Recurrence("daily", hour=24),
    ],
)
def test_invalid_recurrence_rejected(rule: Recurrence) -> None:
    with pytest.raises(ValueError):
        serialize_recurrence(rule)
        deserialize_recurrence(serialize_recurrence(rule))


def test_storage_schema_and_idempotent_migration(storage: ReminderStorage) -> None:
    storage.initialize()
    assert storage.validate_schema()


def test_create_persist_restart(service: ReminderService, storage: ReminderStorage) -> None:
    item, created = service.create(parsed(), user_id=1, chat_id=1, source_message_id=10, now=NOW)
    assert created and ReminderStorage(storage.path).get(item.id, 1) == item


def test_duplicate_source_message(service: ReminderService) -> None:
    first, created = service.create(parsed(), user_id=1, chat_id=1, source_message_id=10, now=NOW)
    second, created_again = service.create(parsed("Different"), user_id=1, chat_id=1, source_message_id=10, now=NOW)
    assert created and not created_again and first.id == second.id


def test_duplicate_normalized_request(service: ReminderService) -> None:
    first, _ = service.create(parsed("Check   Jarvis"), user_id=1, chat_id=1, now=NOW)
    second, created = service.create(parsed(" check jarvis "), user_id=1, chat_id=1, now=NOW)
    assert not created and first.id == second.id


@pytest.mark.parametrize(
    "variation",
    [
        parsed("Проверить", NOW + timedelta(hours=2)),
        parsed("Проверить", recurrence=Recurrence("daily", hour=13)),
    ],
)
def test_not_duplicate_for_time_or_type(service: ReminderService, variation: ParsedReminder) -> None:
    service.create(parsed("Проверить"), user_id=1, chat_id=1, now=NOW)
    _, created = service.create(variation, user_id=1, chat_id=1, now=NOW)
    assert created


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "TEST_SECRET_VALUE",
        "Bearer abcdefghijklmnop",
        "123456789:abcdefghijklmnopqrstuvwxyz",
        "eyJabcdefghijk.abcdefghijk.signature",
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        "OPENAI_API_KEY=value",
        ".env OPENAI_API_KEY=value",
    ],
)
def test_secret_blocked(service: ReminderService, secret: str) -> None:
    with pytest.raises(ReminderError, match="SECRET_BLOCKED"):
        service.create(parsed(secret), user_id=1, chat_id=1, now=NOW)
    assert service.storage.active_count() == 0


def test_secret_blocked_before_time_clarification(service: ReminderService) -> None:
    with pytest.raises(ReminderError, match="SECRET_BLOCKED"):
        service.parse_and_handle(
            "Напомни завтра использовать ключ sk-" + "TEST_SECRET_VALUE",
            user_id=1, chat_id=1, source_message_id=99, now=NOW,
        )


def test_min_lead_enforced(service: ReminderService) -> None:
    with pytest.raises(ReminderError, match="TIME_IN_PAST"):
        service.create(parsed(when=NOW + timedelta(seconds=5)), user_id=1, chat_id=1, now=NOW)


def test_active_limit(storage: ReminderStorage) -> None:
    service = ReminderService(storage, default_timezone=TZ.key, max_active_per_user=1)
    service.create(parsed("One"), user_id=1, chat_id=1, now=NOW)
    with pytest.raises(ReminderError, match="LIMIT_REACHED"):
        service.create(parsed("Two", NOW + timedelta(hours=2)), user_id=1, chat_id=1, now=NOW)


def test_ownership_get_cancel_update(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    with pytest.raises(ReminderError, match="NOT_FOUND"):
        service.get(item.id, 2)
    with pytest.raises(ReminderError, match="NOT_FOUND"):
        service.cancel(2, str(item.id))
    with pytest.raises(ReminderError, match="NOT_FOUND"):
        service.update_time(2, str(item.id), NOW + timedelta(hours=3))


@pytest.mark.parametrize(("action", "status"), [("pause", "paused"), ("resume", "scheduled"), ("cancel", "cancelled")])
def test_management_actions(service: ReminderService, action: str, status: str) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    if action == "resume":
        service.pause(1, str(item.id))
    result = (
        service.resume(1, str(item.id), now=NOW)
        if action == "resume"
        else getattr(service, action)(1, str(item.id))
    )
    assert result.status == status


def test_ambiguous_reference(service: ReminderService) -> None:
    service.create(parsed("Документы один"), user_id=1, chat_id=1, now=NOW)
    service.create(parsed("Документы два", NOW + timedelta(hours=2)), user_id=1, chat_id=1, now=NOW)
    with pytest.raises(ReminderError, match="AMBIGUOUS_REFERENCE"):
        service.cancel(1, "Документы")


def test_atomic_claim_two_workers(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    due = NOW + timedelta(hours=2)
    first = service.storage.claim_due(due, owner="a", lease_seconds=120)
    second = service.storage.claim_due(due, owner="b", lease_seconds=120)
    assert [x.id for x in first] == [item.id] and second == []


def test_expired_lease_reclaimed(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    first = service.storage.claim_due(NOW + timedelta(hours=2), owner="a", lease_seconds=10)[0]
    second = service.storage.claim_due(NOW + timedelta(hours=2, seconds=11), owner="b", lease_seconds=10)
    assert first.id == second[0].id


class FakeBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.send_message = AsyncMock(side_effect=error)


def make_scheduler(service: ReminderService, bot: FakeBot, **kwargs) -> ReminderScheduler:
    return ReminderScheduler(
        service.storage, ReminderDelivery(bot, frozenset({1})),
        poll_interval=1, retry_base_seconds=30, **kwargs,
    )


def test_scheduler_delivery_complete_once(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    bot = FakeBot()
    scheduler = make_scheduler(service, bot)
    asyncio.run(scheduler.tick(NOW + timedelta(hours=1, seconds=1)))
    asyncio.run(scheduler.tick(NOW + timedelta(hours=1, seconds=2)))
    assert bot.send_message.await_count == 1
    assert service.get(item.id, 1).status == "completed"


def test_scheduler_overdue_inside_grace(service: ReminderService) -> None:
    service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    bot = FakeBot()
    asyncio.run(make_scheduler(service, bot).tick(NOW + timedelta(hours=2)))
    assert "Запоздавшее" in bot.send_message.await_args.kwargs["text"]


def test_scheduler_overdue_expired(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    bot = FakeBot()
    asyncio.run(make_scheduler(service, bot, overdue_grace_seconds=60).tick(NOW + timedelta(hours=2)))
    assert bot.send_message.await_count == 0
    assert service.get(item.id, 1).status == "failed"


@pytest.mark.parametrize(("attempts", "seconds"), [(0, 30), (1, 60), (2, 120), (3, 240)])
def test_scheduler_exponential_retry(service: ReminderService, attempts: int, seconds: int) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    service.storage.update_owned(item.id, 1, {"delivery_attempts": attempts})
    scheduler = make_scheduler(service, FakeBot(RuntimeError("temporary")), max_attempts=5)
    current = NOW + timedelta(hours=1)
    asyncio.run(scheduler.tick(current))
    updated = service.get(item.id, 1)
    assert datetime.fromisoformat(updated.next_run_at_utc) == current.astimezone(timezone.utc) + timedelta(seconds=seconds)


def test_scheduler_max_attempts_one_time_failed(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    service.storage.update_owned(item.id, 1, {"delivery_attempts": 4})
    asyncio.run(make_scheduler(service, FakeBot(RuntimeError()), max_attempts=5).tick(NOW + timedelta(hours=1)))
    assert service.get(item.id, 1).status == "failed"


def test_scheduler_recurring_failure_keeps_active(service: ReminderService) -> None:
    rule = Recurrence("daily", hour=13)
    item, _ = service.create(parsed(recurrence=rule), user_id=1, chat_id=1, now=NOW)
    service.storage.update_owned(item.id, 1, {"delivery_attempts": 4})
    asyncio.run(make_scheduler(service, FakeBot(RuntimeError()), max_attempts=5).tick(NOW + timedelta(hours=1)))
    updated = service.get(item.id, 1)
    assert updated.status == "scheduled" and updated.is_active


def test_delivery_allowlist_and_chat_ownership(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=2, now=NOW)
    delivery = ReminderDelivery(FakeBot(), frozenset({1}))
    with pytest.raises(PermissionError):
        asyncio.run(delivery.send(item))


def test_formatter_does_not_expose_internal_fields(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    text = delivery_text(item)
    assert "lease" not in text and "dedup" not in text and "🔔" in text
    assert "Напоминание №" in confirmation(item)
    assert "Ваши напоминания" in list_text([item])


def test_tools_do_not_accept_owner_fields(service: ReminderService) -> None:
    registry = ToolRegistry()
    register_reminder_tools(registry, service)
    for tool in registry.list_tools():
        assert "user_id" not in tool.parameters()["properties"]
        assert "chat_id" not in tool.parameters()["properties"]


def test_all_seven_tools_registered(service: ReminderService) -> None:
    registry = ToolRegistry()
    register_reminder_tools(registry, service)
    assert {tool.name for tool in registry.list_tools()} == {
        "create_reminder", "list_reminders", "get_reminder",
        "cancel_reminder", "update_reminder", "pause_reminder",
        "resume_reminder",
    }


def test_reminder_does_not_create_memory(service: ReminderService, tmp_path: Path) -> None:
    memory = MemoryManager(MemoryStorage(tmp_path / "memory.db"))
    memory.storage.initialize()
    before = memory.storage.list_active()
    service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    assert memory.storage.list_active() == before


def test_reminder_and_memory_tools_coexist(service: ReminderService, tmp_path: Path) -> None:
    registry = ToolRegistry()
    memory = MemoryManager(MemoryStorage(tmp_path / "memory.db"))
    register_memory_tools(registry, memory)
    register_reminder_tools(registry, service)
    names = {tool.name for tool in registry.list_tools()}
    assert "create_reminder" in names and "remember" in names


def test_local_reminder_flow_needs_no_llm(service: ReminderService) -> None:
    response = service.parse_and_handle(
        "Напомни через 30 минут проверить Jarvis",
        user_id=1, chat_id=1, source_message_id=55, now=NOW,
    )
    assert response and "Готово" in response
    assert service.storage.active_count(1) == 1


def test_list_local_flow(service: ReminderService) -> None:
    service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    response = service.parse_and_handle(
        "Покажи мои напоминания",
        user_id=1, chat_id=1, source_message_id=56, now=NOW,
    )
    assert response and "Ваши напоминания" in response


def test_local_pause_resume_cancel_flow(service: ReminderService) -> None:
    service.create(parsed("Проверить Jarvis"), user_id=1, chat_id=1, now=NOW)
    assert "паузу" in service.parse_and_handle(
        "Поставь напоминание проверить Jarvis на паузу",
        user_id=1, chat_id=1, source_message_id=1, now=NOW,
    )
    assert "возобновлено" in service.parse_and_handle(
        "Возобнови напоминание проверить Jarvis",
        user_id=1, chat_id=1, source_message_id=2, now=NOW,
    )
    assert "отменено" in service.parse_and_handle(
        "Отмени напоминание про проверить Jarvis",
        user_id=1, chat_id=1, source_message_id=3, now=NOW,
    )


def test_local_move_later(service: ReminderService) -> None:
    item, _ = service.create(parsed("Проверить Jarvis"), user_id=1, chat_id=1, now=NOW)
    before = datetime.fromisoformat(item.next_run_at_utc)
    response = service.parse_and_handle(
        "Перенеси напоминание проверить Jarvis на 5 минут позже",
        user_id=1, chat_id=1, source_message_id=4, now=NOW,
    )
    after = datetime.fromisoformat(service.get(item.id, 1).next_run_at_utc)
    assert response == "Напоминание перенесено."
    assert after - before == timedelta(minutes=5)


def test_logs_exclude_reminder_text(service: ReminderService, caplog: pytest.LogCaptureFixture) -> None:
    secret_phrase = "UNIQUE_REMINDER_CONTENT"
    with caplog.at_level(logging.INFO):
        item, _ = service.create(parsed(secret_phrase), user_id=1, chat_id=1, now=NOW)
        asyncio.run(make_scheduler(service, FakeBot(RuntimeError())).tick(NOW + timedelta(hours=1)))
    assert secret_phrase not in caplog.text
    assert str(item.id) in caplog.text


def test_sql_injection_is_plain_text(service: ReminderService) -> None:
    item, _ = service.create(parsed("'; DROP TABLE reminders; --"), user_id=1, chat_id=1, now=NOW)
    assert service.storage.validate_schema() and item.id > 0


@pytest.mark.parametrize("message", ["$(touch /tmp/x)", "`id`", "; rm -rf /tmp/x"])
def test_shell_text_not_executed(service: ReminderService, tmp_path: Path, message: str) -> None:
    service.create(parsed(message), user_id=1, chat_id=1, now=NOW)
    assert not (tmp_path / "x").exists()


def test_health_disabled_is_ok() -> None:
    set_reminder_health_provider(None, enabled=False)
    payload = health_payload()
    assert payload["status"] == "ok" and not payload["reminders_enabled"]


def test_health_enabled_fields(service: ReminderService) -> None:
    scheduler = SimpleNamespace(
        running=True, last_tick="tick", last_successful_delivery=None,
        last_error_code=None,
    )
    set_reminder_health_provider(lambda: scheduler, enabled=True, storage=service.storage)
    payload = health_payload()
    assert payload["reminder_database_ok"]
    assert payload["reminder_scheduler_running"]
    assert "active_reminders_count" in payload


def test_dst_nonexistent_requires_clarification() -> None:
    parser = ReminderParser("Europe/Berlin")
    now = datetime(2026, 3, 28, 12, tzinfo=ZoneInfo("Europe/Berlin"))
    result = parser.parse("Напомни завтра в 02:30 проверить", now=now)
    assert result and result.requires_clarification


def test_dst_ambiguous_requires_clarification() -> None:
    parser = ReminderParser("Europe/Berlin")
    now = datetime(2026, 10, 24, 12, tzinfo=ZoneInfo("Europe/Berlin"))
    result = parser.parse("Напомни завтра в 02:30 проверить", now=now)
    assert result and result.requires_clarification


def test_all_stored_timestamps_are_utc(service: ReminderService) -> None:
    item, _ = service.create(parsed(), user_id=1, chat_id=1, now=NOW)
    assert datetime.fromisoformat(item.next_run_at_utc).utcoffset() == timedelta(0)
