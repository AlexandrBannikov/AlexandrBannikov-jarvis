"""Typed reminder domain models."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReminderType(StrEnum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"


class ReminderStatus(StrEnum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class Recurrence:
    kind: str
    interval: int = 1
    weekday: int | None = None
    monthday: int | None = None
    hour: int = 0
    minute: int = 0


@dataclass(frozen=True, slots=True)
class ParsedReminder:
    action: str
    title: str = ""
    message: str = ""
    local_datetime: datetime | None = None
    timezone: str = "UTC"
    recurrence: Recurrence | None = None
    reminder_reference: str = ""
    requires_clarification: bool = False
    clarification_question: str = ""
    confidence: float = 1.0
    adjustment_seconds: int = 0


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    user_id: int
    chat_id: int
    title: str
    message: str
    reminder_type: str
    status: str
    timezone: str
    scheduled_at_utc: str | None
    next_run_at_utc: str | None
    recurrence_rule: str | None
    last_run_at_utc: str | None
    completed_at: str | None
    cancelled_at: str | None
    paused_at: str | None
    created_at: str
    updated_at: str
    delivery_attempts: int
    last_delivery_error_code: str | None
    source_message_id: int | None
    deduplication_key: str | None
    lease_owner: str | None
    lease_until_utc: str | None
    is_active: bool
