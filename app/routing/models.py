"""Typed, serializable routing decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class RequestIntent(StrEnum):
    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"
    CURRENT_PUBLIC_INFORMATION = "CURRENT_PUBLIC_INFORMATION"
    LOCATION_AWARE_CURRENT_INFO = "LOCATION_AWARE_CURRENT_INFO"
    WEB_RESEARCH = "WEB_RESEARCH"
    MEMORY = "MEMORY"
    REMINDER = "REMINDER"
    DOCUMENT = "DOCUMENT"
    IMAGE = "IMAGE"
    SSH_DIAGNOSTICS = "SSH_DIAGNOSTICS"
    CRYPTO_CONTROL = "CRYPTO_CONTROL"
    PROJECT_ANALYSIS = "PROJECT_ANALYSIS"
    CONVERSATION = "CONVERSATION"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"

    # Compatibility aliases for integrations while production routing emits
    # only the compact source-of-truth taxonomy above.
    CURRENT_INFORMATION = CURRENT_PUBLIC_INFORMATION
    WEATHER = LOCATION_AWARE_CURRENT_INFO
    NEWS = CURRENT_PUBLIC_INFORMATION
    FINANCE_MARKET = CURRENT_PUBLIC_INFORMATION
    CRYPTO_BOT_RUNTIME = CRYPTO_CONTROL
    SERVER_RUNTIME = SSH_DIAGNOSTICS
    DOCUMENT_QUESTION = DOCUMENT
    IMAGE_QUESTION = IMAGE
    MEMORY_RECALL = MEMORY
    REMINDER_ACTION = REMINDER
    LOCATION_QUESTION = LOCATION_AWARE_CURRENT_INFO
    TIMEZONE_QUESTION = LOCATION_AWARE_CURRENT_INFO
    CALCULATION = GENERAL_KNOWLEDGE
    TRANSLATION = GENERAL_KNOWLEDGE
    WRITING = GENERAL_KNOWLEDGE
    PROMPT_CREATION = GENERAL_KNOWLEDGE
    TECHNICAL_HELP = GENERAL_KNOWLEDGE
    RECOMMENDATION = GENERAL_KNOWLEDGE
    UNKNOWN = CONVERSATION


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
    required_source_of_truth: str = "model_knowledge"
    clarification_question: str | None = None

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
