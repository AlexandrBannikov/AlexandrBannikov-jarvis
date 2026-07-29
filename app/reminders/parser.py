"""Conservative Russian reminder parser."""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.reminders.models import ParsedReminder, Recurrence

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
WEEKDAYS = {
    "понедельник": 0, "понедельникам": 0,
    "вторник": 1, "вторникам": 1,
    "среду": 2, "средам": 2,
    "четверг": 3, "четвергам": 3,
    "пятницу": 4, "пятницам": 4,
    "субботу": 5, "субботам": 5,
    "воскресенье": 6, "воскресеньям": 6,
}


class ReminderParser:
    def __init__(self, timezone_name: str) -> None:
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Invalid timezone") from error
        self.timezone_name = timezone_name

    def parse(self, text: str, *, now: datetime | None = None) -> ParsedReminder | None:
        raw = text.strip()
        lowered = raw.casefold()
        current = now.astimezone(self.timezone) if now else datetime.now(self.timezone)
        if re.search(r"\b(?:покажи|список|что).*напомин", lowered):
            return ParsedReminder(action="list", timezone=self.timezone_name)
        action_match = re.search(
            r"\b(отмени|забудь|перенеси|обнови|поставь.+пауз\w*|"
            r"приостанови|возобнови)\b", lowered
        )
        if action_match:
            action_word = action_match.group(1)
            action = (
                "cancel" if action_word in {"отмени", "забудь"}
                else "update" if action_word in {"перенеси", "обнови"}
                else "pause" if "пауз" in action_word or action_word == "приостанови"
                else "resume"
            )
            reference = re.sub(
                r"(?i)^.*?(?:напоминание|напомни)\s+(?:про\s+)?", "", raw
            ).strip()
            adjustment_seconds = 0
            adjustment = re.search(
                r"(?i)\s+на\s+(\d+)\s+(минут|час|дн)\w*\s+позже\s*$",
                reference,
            )
            if adjustment:
                amount = int(adjustment.group(1))
                unit = adjustment.group(2)
                adjustment_seconds = amount * (
                    60 if unit.startswith("минут")
                    else 3600 if unit.startswith("час")
                    else 86400
                )
                reference = reference[:adjustment.start()].strip()
            reference = re.sub(
                r"(?i)\s+(?:на\s+паузу|с\s+паузы)\s*$", "", reference
            ).strip()
            return ParsedReminder(
                action=action, reminder_reference=reference,
                timezone=self.timezone_name, confidence=0.9,
                adjustment_seconds=adjustment_seconds,
            )
        if not re.search(r"\bнапомни\b", lowered):
            return None
        if re.search(r"\b(?:вечером|утром|днём|ночью)\b", lowered):
            return ParsedReminder(
                action="create", timezone=self.timezone_name,
                requires_clarification=True,
                clarification_question="Во сколько именно поставить напоминание?",
            )
        recurrence = self._recurrence(lowered)
        local_dt, matched = self._datetime(lowered, current, recurrence)
        if local_dt is None:
            return ParsedReminder(
                action="create", timezone=self.timezone_name,
                requires_clarification=True,
                clarification_question="На какую дату и время поставить напоминание?",
                confidence=0.5,
            )
        message = raw
        for span in sorted(matched, reverse=True):
            message = message[:span[0]] + " " + message[span[1]:]
        message = re.sub(r"(?i)\b(?:пожалуйста\s+)?напомни\b", "", message)
        message = re.sub(r"\s+", " ", message).strip(" .,:—-")
        if not message:
            return ParsedReminder(
                action="create", timezone=self.timezone_name,
                requires_clarification=True,
                clarification_question="Что именно нужно напомнить?",
            )
        return ParsedReminder(
            action="create", title=message[:120], message=message,
            local_datetime=local_dt, timezone=self.timezone_name,
            recurrence=recurrence, confidence=0.95,
        )

    def _recurrence(self, text: str) -> Recurrence | None:
        time_match = re.search(r"\b(?:в\s*)?([01]?\d|2[0-3]):([0-5]\d)\b", text)
        hour, minute = (
            (int(time_match.group(1)), int(time_match.group(2)))
            if time_match else (0, 0)
        )
        if "каждый день" in text or "каждое утро" in text:
            return Recurrence("daily", hour=hour, minute=minute)
        if "по будням" in text:
            return Recurrence("weekdays", hour=hour, minute=minute)
        if "по выходным" in text:
            return Recurrence("weekends", hour=hour, minute=minute)
        match = re.search(r"кажд(?:ый|ую)\s+(" + "|".join(WEEKDAYS) + r")", text)
        if match:
            return Recurrence(
                "weekly", weekday=WEEKDAYS[match.group(1)],
                hour=hour, minute=minute,
            )
        match = re.search(r"каждые\s+(\d+)\s+(дн|недел)", text)
        if match:
            return Recurrence(
                "interval_weeks" if match.group(2).startswith("недел") else "interval_days",
                interval=int(match.group(1)), hour=hour, minute=minute,
            )
        match = re.search(r"каждого\s+(\d+|первого)\s+числа", text)
        if match:
            day = 1 if match.group(1) == "первого" else int(match.group(1))
            return Recurrence("monthly", monthday=day, hour=hour, minute=minute)
        return None

    def _datetime(
        self, text: str, now: datetime, recurrence: Recurrence | None
    ) -> tuple[datetime | None, list[tuple[int, int]]]:
        spans: list[tuple[int, int]] = []
        relative = re.search(r"через\s+(\d+)\s+(минут|час|дн)", text)
        if relative:
            value = int(relative.group(1))
            unit = relative.group(2)
            delta = (
                timedelta(minutes=value) if unit.startswith("минут")
                else timedelta(hours=value) if unit.startswith("час")
                else timedelta(days=value)
            )
            spans.append(relative.span())
            return now + delta, spans
        time_match = re.search(r"\b(?:в\s*)?([01]?\d|2[0-3]):([0-5]\d)\b", text)
        if time_match:
            spans.append(time_match.span())
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
        elif recurrence is not None:
            return None, spans
        else:
            return None, spans
        date = now.date()
        relative_day = re.search(r"\b(сегодня|завтра|послезавтра)\b", text)
        explicit_date = re.search(
            r"\b(\d{1,2})[.](\d{1,2})(?:[.](\d{4}))?\b", text
        )
        named_date = re.search(
            r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?\b", text
        )
        weekday = re.search(r"\b(?:в\s+)?(" + "|".join(WEEKDAYS) + r")\b", text)
        if relative_day:
            spans.append(relative_day.span())
            date += timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[relative_day.group(1)])
        elif explicit_date or named_date:
            match = explicit_date or named_date
            spans.append(match.span())
            day = int(match.group(1))
            month = int(match.group(2)) if explicit_date else MONTHS[match.group(2)]
            year = int(match.group(3)) if match.group(3) else now.year
            try:
                candidate = datetime(year, month, day, hour, minute, tzinfo=self.timezone)
            except ValueError:
                return None, spans
            if not self._valid_wall_time(candidate):
                return None, spans
            if not match.group(3) and candidate <= now:
                candidate = candidate.replace(year=year + 1)
            return candidate, spans
        elif weekday and recurrence is None:
            spans.append(weekday.span())
            offset = (WEEKDAYS[weekday.group(1)] - now.weekday()) % 7
            candidate = datetime.combine(
                date + timedelta(days=offset),
                datetime.min.time(),
                self.timezone,
            ).replace(hour=hour, minute=minute)
            if candidate <= now:
                candidate += timedelta(days=7)
            if not self._valid_wall_time(candidate):
                return None, spans
            return candidate, spans
        candidate = datetime.combine(date, datetime.min.time(), self.timezone).replace(
            hour=hour, minute=minute
        )
        if recurrence is not None:
            from app.reminders.recurrence import next_occurrence
            candidate = next_occurrence(recurrence, now - timedelta(seconds=1))
        elif candidate <= now:
            return None, spans
        if not self._valid_wall_time(candidate):
            return None, spans
        return candidate, spans

    def _valid_wall_time(self, value: datetime) -> bool:
        """Reject nonexistent and ambiguous DST wall times."""
        naive = value.replace(tzinfo=None)
        first = naive.replace(tzinfo=self.timezone, fold=0)
        second = naive.replace(tzinfo=self.timezone, fold=1)
        if first.utcoffset() != second.utcoffset():
            return False
        round_trip = first.astimezone(timezone.utc).astimezone(self.timezone)
        return round_trip.replace(tzinfo=None) == naive
