"""Strict local recurrence calculation."""

import json
from calendar import monthrange
from datetime import datetime, timedelta

from app.reminders.models import Recurrence


def serialize_recurrence(rule: Recurrence) -> str:
    return json.dumps(
        {
            "kind": rule.kind,
            "interval": rule.interval,
            "weekday": rule.weekday,
            "monthday": rule.monthday,
            "hour": rule.hour,
            "minute": rule.minute,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def deserialize_recurrence(value: str) -> Recurrence:
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("Invalid recurrence")
    rule = Recurrence(
        kind=str(data["kind"]),
        interval=int(data.get("interval", 1)),
        weekday=(
            int(data["weekday"]) if data.get("weekday") is not None else None
        ),
        monthday=(
            int(data["monthday"])
            if data.get("monthday") is not None
            else None
        ),
        hour=int(data.get("hour", 0)),
        minute=int(data.get("minute", 0)),
    )
    validate_recurrence(rule)
    return rule


def validate_recurrence(rule: Recurrence) -> None:
    if rule.kind not in {
        "daily", "weekdays", "weekends", "weekly", "monthly",
        "interval_days", "interval_weeks",
    }:
        raise ValueError("Unsupported recurrence")
    if rule.interval < 1 or not 0 <= rule.hour <= 23 or not 0 <= rule.minute <= 59:
        raise ValueError("Invalid recurrence values")
    if rule.kind == "weekly" and rule.weekday not in range(7):
        raise ValueError("Weekly recurrence requires weekday")
    if rule.kind == "monthly" and (
        rule.monthday is None or not 1 <= rule.monthday <= 31
    ):
        raise ValueError("Monthly recurrence requires monthday")


def next_occurrence(rule: Recurrence, after: datetime) -> datetime:
    """Return a strictly later timezone-aware occurrence.

    Monthly days absent from a month are skipped.
    """
    if after.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    validate_recurrence(rule)
    candidate = after.replace(
        hour=rule.hour, minute=rule.minute, second=0, microsecond=0
    )
    if rule.kind == "daily":
        if candidate <= after:
            candidate += timedelta(days=rule.interval)
        return candidate
    if rule.kind in {"interval_days", "interval_weeks"}:
        days = rule.interval * (7 if rule.kind == "interval_weeks" else 1)
        return candidate + timedelta(days=days)
    if rule.kind in {"weekdays", "weekends", "weekly"}:
        allowed = (
            set(range(5)) if rule.kind == "weekdays"
            else {5, 6} if rule.kind == "weekends"
            else {int(rule.weekday)}
        )
        for offset in range(0, 15):
            possible = candidate + timedelta(days=offset)
            if possible.weekday() in allowed and possible > after:
                return possible
        raise ValueError("Could not calculate recurrence")
    year, month = after.year, after.month
    for _ in range(24):
        day = int(rule.monthday)
        if day <= monthrange(year, month)[1]:
            possible = after.replace(
                year=year, month=month, day=day, hour=rule.hour,
                minute=rule.minute, second=0, microsecond=0,
            )
            if possible > after:
                return possible
        month += 1
        if month == 13:
            year += 1
            month = 1
    raise ValueError("Could not calculate monthly recurrence")
