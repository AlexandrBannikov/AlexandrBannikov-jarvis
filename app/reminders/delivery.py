"""Telegram reminder delivery with allowlist enforcement."""

import logging

from app.reminders.formatter import delivery_text
from app.reminders.models import Reminder

logger = logging.getLogger(__name__)


class ReminderDelivery:
    def __init__(self, bot: object, allowed_user_ids: frozenset[int], enabled: bool = True,
                 access_storage=None) -> None:
        self.bot = bot
        self.allowed_user_ids = allowed_user_ids
        self.enabled = enabled
        self.access_storage = access_storage

    async def send(self, reminder: Reminder, *, overdue: bool = False) -> None:
        principal = self.access_storage.principal(reminder.user_id) if self.access_storage else None
        allowed = reminder.user_id in self.allowed_user_ids or bool(
            principal and principal.status == "active"
        )
        if not allowed or reminder.chat_id != reminder.user_id:
            raise PermissionError("OWNER_NOT_ALLOWED")
        if not self.enabled:
            raise RuntimeError("DELIVERY_DISABLED")
        await self.bot.send_message(
            chat_id=reminder.chat_id,
            text=delivery_text(reminder, overdue=overdue),
        )
