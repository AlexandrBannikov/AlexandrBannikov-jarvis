"""Reminder business rules independent from Telegram and OpenAI."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.memory.security import contains_secret
from app.reminders.formatter import confirmation, list_text
from app.reminders.models import ParsedReminder, Reminder
from app.reminders.parser import ReminderParser
from app.reminders.recurrence import (
    deserialize_recurrence,
    next_occurrence,
    serialize_recurrence,
)
from app.reminders.storage import ReminderStorage, utc_now, utc_text

SECRET_MESSAGE = (
    "Я не буду сохранять секретный ключ в напоминании. Сохрани его в "
    "защищённом хранилище, а в напоминании укажи только безопасное описание."
)
logger = logging.getLogger(__name__)


class ReminderError(Exception):
    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message


class ReminderService:
    def __init__(
        self,
        storage: ReminderStorage,
        *,
        default_timezone: str,
        min_lead_seconds: int = 20,
        max_active_per_user: int = 100,
        max_title_length: int = 120,
        max_message_length: int = 1000,
        min_recurrence_seconds: int = 3600,
        list_limit: int = 20,
    ) -> None:
        self.storage = storage
        self.parser = ReminderParser(default_timezone)
        self.default_timezone = default_timezone
        self.min_lead_seconds = min_lead_seconds
        self.max_active_per_user = max_active_per_user
        self.max_title_length = max_title_length
        self.max_message_length = max_message_length
        self.min_recurrence_seconds = min_recurrence_seconds
        self.list_limit = list_limit
        self.storage.initialize()

    def parse_and_handle(
        self,
        text: str,
        *,
        user_id: int,
        chat_id: int,
        source_message_id: int | None,
        now: datetime | None = None,
    ) -> str | None:
        if re.search(r"(?i)\bнапомни\b", text) and contains_secret(text):
            raise ReminderError("SECRET_BLOCKED", SECRET_MESSAGE)
        parsed = self.parser.parse(text, now=now)
        if parsed is None:
            return None
        if parsed.requires_clarification:
            return parsed.clarification_question
        if parsed.action == "create":
            reminder, created = self.create(
                parsed, user_id=user_id, chat_id=chat_id,
                source_message_id=source_message_id, now=now,
            )
            return confirmation(reminder, duplicate=not created)
        if parsed.action == "list":
            return list_text(self.list(user_id))
        if parsed.action == "cancel":
            self.cancel(user_id, parsed.reminder_reference)
            return "Напоминание отменено."
        if parsed.action == "pause":
            self.pause(user_id, parsed.reminder_reference)
            return "Напоминание поставлено на паузу."
        if parsed.action == "resume":
            reminder = self.resume(user_id, parsed.reminder_reference, now=now)
            local = datetime.fromisoformat(reminder.next_run_at_utc).astimezone(
                ZoneInfo(reminder.timezone)
            )
            return (
                "Напоминание возобновлено. Следующее срабатывание: "
                f"{local.strftime('%d.%m.%Y в %H:%M')}."
            )
        if parsed.action == "update" and parsed.adjustment_seconds:
            reminder = self.resolve(user_id, parsed.reminder_reference)
            if not reminder.next_run_at_utc:
                raise ReminderError("TIME_AMBIGUOUS", "Укажите новое время.")
            current = datetime.fromisoformat(reminder.next_run_at_utc).astimezone(
                ZoneInfo(reminder.timezone)
            )
            self.update_time(
                user_id,
                parsed.reminder_reference,
                current + timedelta(seconds=parsed.adjustment_seconds),
                now=now,
            )
            return "Напоминание перенесено."
        return None

    def create(
        self,
        parsed: ParsedReminder,
        *,
        user_id: int,
        chat_id: int,
        source_message_id: int | None = None,
        now: datetime | None = None,
    ) -> tuple[Reminder, bool]:
        if user_id <= 0 or chat_id == 0:
            raise ReminderError("INVALID_OWNER", "Не удалось определить владельца напоминания.")
        if contains_secret(f"{parsed.title}\n{parsed.message}"):
            raise ReminderError("SECRET_BLOCKED", SECRET_MESSAGE)
        if not parsed.local_datetime or parsed.local_datetime.tzinfo is None:
            raise ReminderError("TIME_AMBIGUOUS", "Во сколько именно поставить напоминание?")
        current = (now or utc_now()).astimezone(timezone.utc)
        scheduled = parsed.local_datetime.astimezone(timezone.utc)
        if scheduled < current + timedelta(seconds=self.min_lead_seconds):
            raise ReminderError(
                "TIME_IN_PAST",
                "Это время уже прошло или слишком близко. Указать другое время?",
            )
        title = parsed.title.strip()
        message = parsed.message.strip()
        if not title or len(title) > self.max_title_length:
            raise ReminderError("TITLE_INVALID", "Название напоминания слишком длинное.")
        if not message or len(message) > self.max_message_length:
            raise ReminderError("MESSAGE_INVALID", "Текст напоминания слишком длинный.")
        if self.storage.active_count(user_id) >= self.max_active_per_user:
            raise ReminderError("LIMIT_REACHED", "Достигнут лимит активных напоминаний.")
        rule = serialize_recurrence(parsed.recurrence) if parsed.recurrence else None
        if parsed.recurrence:
            following = next_occurrence(parsed.recurrence, parsed.local_datetime)
            if (following - parsed.local_datetime).total_seconds() < self.min_recurrence_seconds:
                raise ReminderError("RECURRENCE_TOO_FREQUENT", "Слишком частое повторение.")
        dedup = self._dedup(
            user_id, message, "recurring" if rule else "one_time",
            scheduled.isoformat(), rule or "",
        )
        recent = self._find_dedup(user_id, dedup, current)
        if recent:
            return recent, False
        reminder, created = self.storage.create(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "title": title,
                "message": message,
                "reminder_type": "recurring" if rule else "one_time",
                "timezone": parsed.timezone,
                "scheduled_at_utc": utc_text(scheduled),
                "next_run_at_utc": utc_text(scheduled),
                "recurrence_rule": rule,
                "source_message_id": source_message_id,
                "deduplication_key": dedup,
            }
        )
        logger.info(
            "Reminder create result: reminder_id=%s user_id=%s type=%s "
            "status=%s created=%s",
            reminder.id, user_id, reminder.reminder_type, reminder.status,
            str(created).lower(),
        )
        return reminder, created

    def list(self, user_id: int) -> list[Reminder]:
        return self.storage.list_user(user_id, self.list_limit)

    def get(self, reminder_id: int, user_id: int) -> Reminder:
        reminder = self.storage.get(reminder_id, user_id)
        if reminder is None:
            raise ReminderError("NOT_FOUND", "Напоминание не найдено.")
        return reminder

    def resolve(self, user_id: int, reference: str) -> Reminder:
        if reference.strip().isdigit():
            return self.get(int(reference), user_id)
        matches = self.storage.find(user_id, reference.strip())
        if not matches:
            raise ReminderError("NOT_FOUND", "Напоминание не найдено.")
        if len(matches) > 1:
            raise ReminderError(
                "AMBIGUOUS_REFERENCE",
                "Я нашёл несколько напоминаний. Какое изменить?",
            )
        return matches[0]

    def cancel(self, user_id: int, reference: str) -> Reminder:
        reminder = self.resolve(user_id, reference)
        updated = self.storage.update_owned(
            reminder.id, user_id,
            {
                "status": "cancelled", "cancelled_at": utc_text(utc_now()),
                "is_active": 0, "lease_owner": None, "lease_until_utc": None,
            },
        )
        assert updated is not None
        logger.info(
            "Reminder status changed: reminder_id=%s user_id=%s status=cancelled",
            updated.id, user_id,
        )
        return updated

    def pause(self, user_id: int, reference: str) -> Reminder:
        reminder = self.resolve(user_id, reference)
        updated = self.storage.update_owned(
            reminder.id, user_id,
            {"status": "paused", "paused_at": utc_text(utc_now()), "lease_owner": None, "lease_until_utc": None},
        )
        assert updated is not None
        logger.info(
            "Reminder status changed: reminder_id=%s user_id=%s status=paused",
            updated.id, user_id,
        )
        return updated

    def resume(self, user_id: int, reference: str, *, now: datetime | None = None) -> Reminder:
        reminder = self.resolve(user_id, reference)
        current = now or utc_now()
        next_run = reminder.next_run_at_utc
        if reminder.recurrence_rule:
            rule = deserialize_recurrence(reminder.recurrence_rule)
            next_run = utc_text(next_occurrence(rule, current.astimezone(ZoneInfo(reminder.timezone))))
        elif next_run and datetime.fromisoformat(next_run) <= current:
            raise ReminderError("TIME_IN_PAST", "Время напоминания уже прошло. Сначала перенесите его.")
        updated = self.storage.update_owned(
            reminder.id, user_id,
            {"status": "scheduled", "paused_at": None, "next_run_at_utc": next_run},
        )
        assert updated is not None
        logger.info(
            "Reminder status changed: reminder_id=%s user_id=%s status=scheduled action=resume",
            updated.id, user_id,
        )
        return updated

    def update_time(
        self,
        user_id: int,
        reference: str,
        local_datetime: datetime,
        *,
        now: datetime | None = None,
    ) -> Reminder:
        reminder = self.resolve(user_id, reference)
        if local_datetime.tzinfo is None:
            raise ReminderError("TIME_AMBIGUOUS", "Укажите timezone.")
        current = now or utc_now()
        if local_datetime.astimezone(timezone.utc) < current.astimezone(
            timezone.utc
        ) + timedelta(seconds=self.min_lead_seconds):
            raise ReminderError("TIME_IN_PAST", "Это время уже прошло. Указать другое время?")
        next_text = utc_text(local_datetime)
        dedup = self._dedup(
            user_id, reminder.message, reminder.reminder_type,
            next_text or "", reminder.recurrence_rule or "",
        )
        updated = self.storage.update_owned(
            reminder.id, user_id,
            {
                "scheduled_at_utc": next_text, "next_run_at_utc": next_text,
                "timezone": getattr(local_datetime.tzinfo, "key", reminder.timezone),
                "deduplication_key": dedup, "status": "scheduled",
            },
        )
        assert updated is not None
        logger.info(
            "Reminder updated: reminder_id=%s user_id=%s status=%s",
            updated.id, user_id, updated.status,
        )
        return updated

    def _find_dedup(self, user_id: int, key: str, now: datetime) -> Reminder | None:
        with self.storage.connect() as db:
            row = db.execute(
                "SELECT * FROM reminders WHERE user_id=? AND deduplication_key=? "
                "AND is_active=1 AND created_at>=? ORDER BY id DESC LIMIT 1",
                (user_id, key, utc_text(now - timedelta(minutes=5))),
            ).fetchone()
        return self.storage._record(row) if row else None

    @staticmethod
    def _dedup(user_id: int, message: str, kind: str, when: str, rule: str) -> str:
        normalized = re.sub(r"\s+", " ", message.casefold()).strip()
        payload = f"{user_id}|{normalized}|{kind}|{when}|{rule}"
        return hashlib.sha256(payload.encode()).hexdigest()
