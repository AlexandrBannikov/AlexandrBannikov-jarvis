"""Persistent polling scheduler with atomic SQLite leases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import socket
import uuid
from zoneinfo import ZoneInfo

from app.reminders.delivery import ReminderDelivery
from app.reminders.models import Reminder
from app.reminders.recurrence import deserialize_recurrence, next_occurrence
from app.reminders.storage import ReminderStorage, utc_now, utc_text

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(
        self, storage: ReminderStorage, delivery: ReminderDelivery, *,
        poll_interval: int = 10, lease_seconds: int = 120,
        max_attempts: int = 5, retry_base_seconds: int = 30,
        overdue_grace_seconds: int = 86400,
    ) -> None:
        self.storage = storage
        self.delivery = delivery
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.overdue_grace_seconds = overdue_grace_seconds
        self.owner = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
        self.task: asyncio.Task[None] | None = None
        self.running = False
        self.last_tick: str | None = None
        self.last_successful_delivery: str | None = None
        self.last_error_code: str | None = None

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="reminder-scheduler")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.running = False

    async def run(self) -> None:
        self.running = True
        logger.info("Reminder scheduler started")
        try:
            while True:
                try:
                    await self.tick()
                except Exception as error:
                    self.last_error_code = type(error).__name__
                    logger.error("Reminder scheduler tick failed: error_type=%s", type(error).__name__)
                await asyncio.sleep(self.poll_interval)
        finally:
            self.running = False
            logger.info("Reminder scheduler stopped")

    async def tick(self, now: datetime | None = None) -> None:
        current = now or utc_now()
        self.last_tick = utc_text(current)
        due = self.storage.claim_due(
            current, owner=self.owner, lease_seconds=self.lease_seconds,
        )
        for reminder in due:
            try:
                await self._process(reminder, current)
            except Exception as error:
                logger.error(
                    "Reminder processing failed: reminder_id=%s user_id=%s error_type=%s",
                    reminder.id, reminder.user_id, type(error).__name__,
                )

    async def _process(self, reminder: Reminder, now: datetime) -> None:
        scheduled = datetime.fromisoformat(reminder.next_run_at_utc or reminder.scheduled_at_utc or "")
        overdue_seconds = max(0, (now - scheduled).total_seconds())
        if reminder.reminder_type == "one_time" and overdue_seconds > self.overdue_grace_seconds:
            self.storage.update_owned(
                reminder.id, reminder.user_id,
                {
                    "status": "failed", "is_active": 0,
                    "last_delivery_error_code": "OVERDUE_EXPIRED",
                    "lease_owner": None, "lease_until_utc": None,
                },
            )
            return
        try:
            await self.delivery.send(reminder, overdue=overdue_seconds > self.poll_interval)
        except Exception as error:
            self._retry(reminder, now, type(error).__name__)
            return
        self.last_successful_delivery = utc_text(now)
        self.last_error_code = None
        if reminder.reminder_type == "one_time":
            self.storage.update_owned(
                reminder.id, reminder.user_id,
                {
                    "status": "completed", "completed_at": utc_text(now),
                    "last_run_at_utc": utc_text(now), "is_active": 0,
                    "lease_owner": None, "lease_until_utc": None,
                    "last_delivery_error_code": None,
                },
            )
        else:
            rule = deserialize_recurrence(reminder.recurrence_rule or "")
            local_now = now.astimezone(ZoneInfo(reminder.timezone))
            following = next_occurrence(rule, local_now)
            self.storage.update_owned(
                reminder.id, reminder.user_id,
                {
                    "status": "scheduled", "last_run_at_utc": utc_text(now),
                    "next_run_at_utc": utc_text(following), "delivery_attempts": 0,
                    "last_delivery_error_code": None, "lease_owner": None,
                    "lease_until_utc": None,
                },
            )

    def _retry(self, reminder: Reminder, now: datetime, error_code: str) -> None:
        attempts = reminder.delivery_attempts + 1
        safe_code = error_code[:64] if error_code.isidentifier() else "DELIVERY_ERROR"
        if attempts >= self.max_attempts:
            if reminder.reminder_type == "one_time":
                fields = {"status": "failed", "is_active": 0}
            else:
                rule = deserialize_recurrence(reminder.recurrence_rule or "")
                fields = {
                    "status": "scheduled",
                    "next_run_at_utc": utc_text(next_occurrence(rule, now.astimezone(ZoneInfo(reminder.timezone)))),
                    "delivery_attempts": 0,
                }
        else:
            fields = {
                "status": "scheduled",
                "delivery_attempts": attempts,
                "next_run_at_utc": utc_text(now + timedelta(seconds=self.retry_base_seconds * 2 ** (attempts - 1))),
            }
        fields.update(
            {
                "last_delivery_error_code": safe_code,
                "lease_owner": None, "lease_until_utc": None,
            }
        )
        self.storage.update_owned(reminder.id, reminder.user_id, fields)
        self.last_error_code = safe_code
        logger.warning(
            "Reminder delivery deferred: reminder_id=%s user_id=%s attempts=%s error_code=%s",
            reminder.id, reminder.user_id, attempts, safe_code,
        )
