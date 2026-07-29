"""Safe Russian Telegram formatting for reminders."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.reminders.models import Reminder
from app.reminders.recurrence import deserialize_recurrence


def local_datetime(reminder: Reminder) -> datetime | None:
    value = reminder.next_run_at_utc or reminder.scheduled_at_utc
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(ZoneInfo(reminder.timezone))


def recurrence_label(reminder: Reminder) -> str:
    if not reminder.recurrence_rule:
        return "однократно"
    rule = deserialize_recurrence(reminder.recurrence_rule)
    labels = {
        "daily": "каждый день",
        "weekdays": "по будням",
        "weekends": "по выходным",
        "weekly": "каждую неделю",
        "monthly": "каждый месяц",
        "interval_days": f"каждые {rule.interval} дн.",
        "interval_weeks": f"каждые {rule.interval} нед.",
    }
    return f"{labels[rule.kind]} в {rule.hour:02d}:{rule.minute:02d}"


def confirmation(reminder: Reminder, *, duplicate: bool = False) -> str:
    if duplicate:
        return "Такое напоминание уже существует."
    local = local_datetime(reminder)
    when = local.strftime("%d.%m.%Y в %H:%M") if local else "в назначенное время"
    return f"Напоминание №{reminder.id} создано.\nГотово. Напомню {when}:\n{reminder.message}"


def delivery_text(reminder: Reminder, *, overdue: bool = False) -> str:
    local = local_datetime(reminder)
    prefix = "🔔 Запоздавшее напоминание" if overdue else "🔔 Напоминание"
    lines = [prefix, "", reminder.message]
    if local:
        lines.extend(["", "Запланировано:", local.strftime("%d.%m.%Y, %H:%M"), reminder.timezone])
    if reminder.reminder_type == "recurring":
        lines.extend(["", "Повтор:", recurrence_label(reminder)])
    return "\n".join(lines)


def list_text(reminders: list[Reminder]) -> str:
    if not reminders:
        return "Активных напоминаний нет."
    lines = ["Ваши напоминания:"]
    for index, reminder in enumerate(reminders, 1):
        local = local_datetime(reminder)
        when = local.strftime("%d %m %Y, %H:%M") if local else "без даты"
        suffix = "На паузе" if reminder.status == "paused" else recurrence_label(reminder).capitalize()
        lines.extend(["", f"{index}. {reminder.title}", f"   {when}", f"   {suffix}"])
    return "\n".join(lines)
