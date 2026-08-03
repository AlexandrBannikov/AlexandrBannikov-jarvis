"""Routing quality metrics evaluated independently from classifier rules."""
from __future__ import annotations

from dataclasses import dataclass

from .models import RequestIntent


@dataclass(frozen=True, slots=True)
class RoutingExpectation:
    text: str
    intent: RequestIntent
    capability: str


@dataclass(frozen=True, slots=True)
class RoutingQuality:
    total: int
    correct: int
    current_info_recall: float
    current_info_precision: float
    stable_false_search_rate: float
    private_search_leakage_count: int
    specialized_tool_routing_rate: float
    ambiguous_clarification_rate: float
    external_project_write_attempt_rate: float

    @property
    def score(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def ok(self) -> bool:
        return (
            self.current_info_recall >= .95
            and self.current_info_precision >= .90
            and self.stable_false_search_rate <= .05
            and self.private_search_leakage_count == 0
            and self.specialized_tool_routing_rate >= .95
            and self.external_project_write_attempt_rate == 0
        )


def evaluate(router: object, cases: tuple[RoutingExpectation, ...]) -> RoutingQuality:
    rows = [(item, router.classify(item.text, location_available=True)) for item in cases]
    current = [row for row in rows if row[0].capability == "web_search"]
    predicted_current = [row for row in rows if "web_search" in row[1].required_capabilities]
    stable = [row for row in rows if row[0].intent is RequestIntent.GENERAL_KNOWLEDGE]
    private = [row for row in rows if row[0].capability in {"documents", "memory", "reminders", "ssh", "crypto_control"}]
    specialized = [row for row in rows if row[0].capability in {"memory", "reminders", "ssh", "crypto_control"}]
    ambiguous = [row for row in rows if row[0].intent is RequestIntent.CONVERSATION]
    boundaries = [row for row in rows if row[0].intent is RequestIntent.UNSUPPORTED_ACTION]
    correct = sum(
        item.intent in decision.intents and (
            item.capability == "none" or item.capability in decision.required_capabilities
        ) for item, decision in rows
    )
    return RoutingQuality(
        len(rows), correct,
        sum("web_search" in decision.required_capabilities for _, decision in current) / len(current) if current else 1.0,
        sum(item.capability == "web_search" for item, _ in predicted_current) / len(predicted_current) if predicted_current else 1.0,
        sum("web_search" in decision.required_capabilities for _, decision in stable) / len(stable) if stable else 0.0,
        sum("web_search" in decision.required_capabilities for _, decision in private),
        sum(item.capability in decision.required_capabilities for item, decision in specialized) / len(specialized) if specialized else 1.0,
        sum(bool(decision.clarification_question) for _, decision in ambiguous) / len(ambiguous) if ambiguous else 1.0,
        sum(bool(decision.required_capabilities) for _, decision in boundaries) / len(boundaries) if boundaries else 0.0,
    )


DEFAULT_HOLDOUT = (
    RoutingExpectation("Ожидаются ли осадки к вечеру?", RequestIntent.CURRENT_PUBLIC_INFORMATION, "web_search"),
    RoutingExpectation("Каков официальный курс иены на сегодня?", RequestIntent.CURRENT_PUBLIC_INFORMATION, "web_search"),
    RoutingExpectation("Опубликованы свежие правила регистрации?", RequestIntent.CURRENT_PUBLIC_INFORMATION, "web_search"),
    RoutingExpectation("Когда ближайший рейс до Казани?", RequestIntent.CURRENT_PUBLIC_INFORMATION, "web_search"),
    RoutingExpectation("Какие результаты турнира за сегодня?", RequestIntent.CURRENT_PUBLIC_INFORMATION, "web_search"),
    RoutingExpectation("Объясни закон Архимеда", RequestIntent.GENERAL_KNOWLEDGE, "general_llm"),
    RoutingExpectation("Почему медь хорошо проводит тепло?", RequestIntent.GENERAL_KNOWLEDGE, "general_llm"),
    RoutingExpectation("Как устроена коробка передач?", RequestIntent.GENERAL_KNOWLEDGE, "general_llm"),
    RoutingExpectation("Вспомни договорённость по дренажу", RequestIntent.MEMORY, "memory"),
    RoutingExpectation("Поставь напоминание на девять утра", RequestIntent.REMINDER, "reminders"),
    RoutingExpectation("Покажи uptime моего сервера", RequestIntent.SSH_DIAGNOSTICS, "ssh"),
    RoutingExpectation("Покажи баланс Crypto-Bot", RequestIntent.CRYPTO_CONTROL, "crypto_control"),
    RoutingExpectation("Как там это?", RequestIntent.CONVERSATION, "general_llm"),
    RoutingExpectation("А сейчас?", RequestIntent.CONVERSATION, "general_llm"),
    RoutingExpectation("Исправь Crypto-Bot и сделай push", RequestIntent.UNSUPPORTED_ACTION, "none"),
    RoutingExpectation("Перезапусти VPN Bot", RequestIntent.UNSUPPORTED_ACTION, "none"),
)
