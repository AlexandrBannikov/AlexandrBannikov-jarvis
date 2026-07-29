"""Strict reminder tools; ownership comes only from trusted application context."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.reminders.formatter import local_datetime
from app.reminders.models import ParsedReminder, Recurrence
from app.reminders.service import ReminderError, ReminderService
from app.tools.base import Tool
from app.tools.registry import ToolRegistry


def schema(properties: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }


class ReminderTool(Tool):
    action = ""

    def __init__(self, service: ReminderService) -> None:
        self.service = service

    def safe(self, operation: Any) -> dict[str, Any]:
        try:
            return operation()
        except ReminderError as error:
            return {"success": False, "error_code": error.code, "user_message": error.user_message}


class CreateReminderTool(ReminderTool):
    action = "create"
    name = "create_reminder"
    description = (
        "Create one validated reminder. Never include user_id or chat_id; "
        "ownership is supplied by trusted Telegram context."
    )

    def parameters(self) -> dict[str, Any]:
        return schema(
            {
                "title": {"type": "string", "maxLength": 120},
                "message": {"type": "string", "maxLength": 1000},
                "local_datetime": {"type": "string"},
                "timezone": {"type": "string"},
                "recurrence_kind": {
                    "type": "string",
                    "enum": ["none", "daily", "weekdays", "weekends", "weekly", "monthly", "interval_days", "interval_weeks"],
                },
                "interval": {"type": "integer", "minimum": 1},
                "weekday": {"type": "integer", "minimum": -1, "maximum": 6},
                "monthday": {"type": "integer", "minimum": 0, "maximum": 31},
            },
            ["title", "message", "local_datetime", "timezone", "recurrence_kind", "interval", "weekday", "monthday"],
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            try:
                zone = ZoneInfo(str(kwargs["timezone"]))
                local = datetime.fromisoformat(str(kwargs["local_datetime"]))
            except (ValueError, ZoneInfoNotFoundError) as error:
                raise ReminderError("TIME_INVALID", "Не удалось распознать дату или timezone.") from error
            if local.tzinfo is None:
                local = local.replace(tzinfo=zone)
            kind = str(kwargs["recurrence_kind"])
            recurrence = None if kind == "none" else Recurrence(
                kind=kind, interval=int(kwargs["interval"]),
                weekday=int(kwargs["weekday"]) if int(kwargs["weekday"]) >= 0 else None,
                monthday=int(kwargs["monthday"]) or None,
                hour=local.hour, minute=local.minute,
            )
            parsed = ParsedReminder(
                action="create", title=str(kwargs["title"]), message=str(kwargs["message"]),
                local_datetime=local, timezone=zone.key, recurrence=recurrence,
            )
            reminder, created = self.service.create(
                parsed, user_id=int(kwargs["trusted_user_id"]),
                chat_id=int(kwargs["trusted_chat_id"]),
                source_message_id=kwargs.get("trusted_source_message_id"),
            )
            return {
                "success": True, "created": created, "reminder_id": reminder.id,
                "type": reminder.reminder_type, "status": reminder.status,
                "local_datetime": local_datetime(reminder).isoformat(),
                "timezone": reminder.timezone,
            }
        return self.safe(operation)


class ListRemindersTool(ReminderTool):
    name = "list_reminders"
    description = "List only the current Telegram user's active reminders."

    def parameters(self) -> dict[str, Any]:
        return schema({}, [])

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        records = self.service.list(int(kwargs["trusted_user_id"]))
        return {
            "success": True,
            "reminders": [
                {
                    "number": item.id, "title": item.title, "status": item.status,
                    "type": item.reminder_type,
                    "local_datetime": local_datetime(item).isoformat() if local_datetime(item) else None,
                    "timezone": item.timezone,
                }
                for item in records
            ],
        }


class ReferenceTool(ReminderTool):
    def parameters(self) -> dict[str, Any]:
        return schema({"reminder_reference": {"type": "string"}}, ["reminder_reference"])

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            reminder = getattr(self.service, self.action)(
                int(kwargs["trusted_user_id"]), str(kwargs["reminder_reference"])
            )
            messages = {
                "cancel": "Напоминание отменено.",
                "pause": "Напоминание поставлено на паузу.",
                "resume": "Напоминание возобновлено.",
            }
            return {
                "success": True, "reminder_id": reminder.id,
                "status": reminder.status, "user_message": messages[self.action],
            }
        return self.safe(operation)


class GetReminderTool(ReferenceTool):
    name = "get_reminder"
    description = "Get one reminder owned by the current Telegram user."
    action = "resolve"

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            reminder = self.service.resolve(
                int(kwargs["trusted_user_id"]),
                str(kwargs["reminder_reference"]),
            )
            return {
                "success": True, "reminder_id": reminder.id,
                "title": reminder.title, "status": reminder.status,
                "type": reminder.reminder_type,
                "local_datetime": (
                    local_datetime(reminder).isoformat()
                    if local_datetime(reminder) else None
                ),
                "timezone": reminder.timezone,
            }
        return self.safe(operation)


class CancelReminderTool(ReferenceTool):
    name = "cancel_reminder"
    description = "Cancel one unambiguous reminder owned by the current user."
    action = "cancel"


class PauseReminderTool(ReferenceTool):
    name = "pause_reminder"
    description = "Pause one unambiguous recurring or one-time reminder."
    action = "pause"


class ResumeReminderTool(ReferenceTool):
    name = "resume_reminder"
    description = "Resume one paused reminder and calculate a future run."
    action = "resume"


class UpdateReminderTool(ReminderTool):
    name = "update_reminder"
    description = "Move one owned reminder to an explicit timezone-aware local datetime."

    def parameters(self) -> dict[str, Any]:
        return schema(
            {
                "reminder_reference": {"type": "string"},
                "local_datetime": {"type": "string"},
                "timezone": {"type": "string"},
            },
            ["reminder_reference", "local_datetime", "timezone"],
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            try:
                zone = ZoneInfo(str(kwargs["timezone"]))
                value = datetime.fromisoformat(str(kwargs["local_datetime"]))
            except (ValueError, ZoneInfoNotFoundError) as error:
                raise ReminderError("TIME_INVALID", "Не удалось распознать дату или timezone.") from error
            if value.tzinfo is None:
                value = value.replace(tzinfo=zone)
            reminder = self.service.update_time(
                int(kwargs["trusted_user_id"]), str(kwargs["reminder_reference"]), value
            )
            return {
                "success": True, "reminder_id": reminder.id,
                "status": reminder.status,
                "local_datetime": local_datetime(reminder).isoformat(),
                "timezone": reminder.timezone,
                "user_message": "Напоминание перенесено.",
            }
        return self.safe(operation)


def register_reminder_tools(registry: ToolRegistry, service: ReminderService) -> None:
    for tool in (
        CreateReminderTool(service), ListRemindersTool(service),
        GetReminderTool(service), CancelReminderTool(service),
        UpdateReminderTool(service), PauseReminderTool(service),
        ResumeReminderTool(service),
    ):
        registry.register(tool)
