"""Final answer guard against capability-blind refusals."""

import re

from .models import RoutingDecision

_REFUSAL = re.compile(
    r"(?i)(?:у\s+меня\s+нет\s+доступа|я\s+не\s+могу|я\s+не\s+умею|"
    r"проверьте?\s+в\s+другом\s+приложении)"
)


class AnswerCapabilityGuard:
    def should_retry(self, text: str, decision: RoutingDecision, *, attempted: bool) -> bool:
        return bool(text and _REFUSAL.search(text) and decision.can_answer and not attempted)
