"""Typed, serializable routing decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class RequestIntent(StrEnum):
    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"
    CURRENT_INFORMATION = "CURRENT_INFORMATION"
    WEATHER = "WEATHER"
    NEWS = "NEWS"
    FINANCE_MARKET = "FINANCE_MARKET"
    CRYPTO_BOT_RUNTIME = "CRYPTO_BOT_RUNTIME"
    SERVER_RUNTIME = "SERVER_RUNTIME"
    DOCUMENT_QUESTION = "DOCUMENT_QUESTION"
    IMAGE_QUESTION = "IMAGE_QUESTION"
    MEMORY_RECALL = "MEMORY_RECALL"
    REMINDER_ACTION = "REMINDER_ACTION"
    LOCATION_QUESTION = "LOCATION_QUESTION"
    TIMEZONE_QUESTION = "TIMEZONE_QUESTION"
    CALCULATION = "CALCULATION"
    TRANSLATION = "TRANSLATION"
    WRITING = "WRITING"
    PROMPT_CREATION = "PROMPT_CREATION"
    TECHNICAL_HELP = "TECHNICAL_HELP"
    RECOMMENDATION = "RECOMMENDATION"
    UNKNOWN = "UNKNOWN"


class RequestFreshness(StrEnum):
    STATIC = "STATIC"
    RECENT = "RECENT"
    REALTIME = "REALTIME"
    PERSONAL_RUNTIME = "PERSONAL_RUNTIME"


class AnswerGroundingStatus(StrEnum):
    MODEL_KNOWLEDGE = "MODEL_KNOWLEDGE"
    WEB_GROUNDED = "WEB_GROUNDED"
    PERSONAL_RUNTIME_GROUNDED = "PERSONAL_RUNTIME_GROUNDED"
    DOCUMENT_GROUNDED = "DOCUMENT_GROUNDED"
    MEMORY_ASSISTED = "MEMORY_ASSISTED"
    MIXED = "MIXED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class ToolExecutionPlan:
    capability: str
    required: bool = True
    fallback: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    intents: tuple[RequestIntent, ...]
    freshness: RequestFreshness
    required_capabilities: tuple[str, ...]
    plan: tuple[ToolExecutionPlan, ...]
    can_answer: bool = True
    location_source: str | None = None
    needs_location: bool = False
    grounding: AnswerGroundingStatus = AnswerGroundingStatus.MODEL_KNOWLEDGE
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def intent(self) -> RequestIntent:
        return self.intents[0] if self.intents else RequestIntent.UNKNOWN

    @property
    def requires_web_search(self) -> bool:
        return "web_search" in self.required_capabilities

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["intent"] = self.intent.value
        value["intents"] = [item.value for item in self.intents]
        value["freshness"] = self.freshness.value
        value["grounding"] = self.grounding.value
        return value
