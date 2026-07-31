from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
def utcnow() -> datetime: return datetime.now(timezone.utc)
@dataclass(frozen=True, slots=True)
class ConversationKey:
    owner_id: int; chat_id: int; thread_id: int | None = None
@dataclass(slots=True)
class PendingQuestion:
    question_id: str; text: str; expected_fields: list[str] = field(default_factory=list)
    expected_answer_type: str = "text"; asked_at: str = field(default_factory=lambda: utcnow().isoformat()); status: str = "waiting"
    def to_dict(self) -> dict[str, Any]: return {"question_id": self.question_id[:100], "text": self.text[:1000], "expected_fields": self.expected_fields[:8], "expected_answer_type": self.expected_answer_type[:50], "asked_at": self.asked_at, "status": self.status}
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PendingQuestion": return cls(str(value.get("question_id", "question")), str(value.get("text", "")), [str(x)[:100] for x in value.get("expected_fields", []) if isinstance(x, str)][:8], str(value.get("expected_answer_type", "text")), str(value.get("asked_at", "")), str(value.get("status", "waiting")))
@dataclass(slots=True)
class ConversationState:
    key: ConversationKey; active_topic: str = ""; user_goal: str = ""; pending_question: PendingQuestion | None = None
    collected_facts: dict[str, str] = field(default_factory=dict); missing_facts: list[str] = field(default_factory=list)
    last_assistant_action: str = ""; last_user_intent: str = ""; status: str = "active"; confidence: float = 0.0
    created_at: str = field(default_factory=lambda: utcnow().isoformat()); updated_at: str = field(default_factory=lambda: utcnow().isoformat()); expires_at: str = field(default_factory=lambda: utcnow().isoformat())
    def is_expired(self, now: datetime | None = None) -> bool:
        try: return datetime.fromisoformat(self.expires_at) <= (now or utcnow())
        except ValueError: return True
    def compact(self) -> dict[str, Any]: return {"active_topic": self.active_topic[:300], "user_goal": self.user_goal[:1000], "pending_question": self.pending_question.to_dict() if self.pending_question else None, "collected_facts": dict(list(self.collected_facts.items())[:12]), "missing_facts": self.missing_facts[:12], "status": self.status, "last_user_intent": self.last_user_intent[:50]}
