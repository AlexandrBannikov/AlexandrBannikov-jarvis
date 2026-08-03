"""Closed intent-to-source-of-truth policy for trustworthy routing."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .models import RequestIntent


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    source: str
    capabilities: tuple[str, ...]
    tool_required: bool
    web_search_allowed: bool


SOURCE_OF_TRUTH = MappingProxyType({
    RequestIntent.GENERAL_KNOWLEDGE: SourcePolicy("model_knowledge", ("general_llm",), False, False),
    RequestIntent.CURRENT_PUBLIC_INFORMATION: SourcePolicy("public_web", ("web_search",), True, True),
    RequestIntent.LOCATION_AWARE_CURRENT_INFO: SourcePolicy("saved_location_and_public_web", ("location", "web_search"), True, True),
    RequestIntent.WEB_RESEARCH: SourcePolicy("public_web", ("web_search",), True, True),
    RequestIntent.MEMORY: SourcePolicy("project_memory", ("memory",), True, False),
    RequestIntent.REMINDER: SourcePolicy("reminder_store", ("reminders",), True, False),
    RequestIntent.DOCUMENT: SourcePolicy("document_store", ("documents",), True, False),
    RequestIntent.IMAGE: SourcePolicy("private_image", ("documents",), True, False),
    RequestIntent.SSH_DIAGNOSTICS: SourcePolicy("ssh_read_only", ("ssh",), True, False),
    RequestIntent.CRYPTO_CONTROL: SourcePolicy("crypto_remote_cli", ("crypto_control",), True, False),
    RequestIntent.PROJECT_ANALYSIS: SourcePolicy("read_only_project_diagnostics", ("general_llm",), False, False),
    RequestIntent.CONVERSATION: SourcePolicy("conversation_context", ("general_llm",), False, False),
    RequestIntent.SAFETY_BLOCKED: SourcePolicy("safety_policy", (), False, False),
    RequestIntent.UNSUPPORTED_ACTION: SourcePolicy("project_boundary", (), False, False),
})


def policy_for(intent: RequestIntent) -> SourcePolicy:
    return SOURCE_OF_TRUTH[intent]
