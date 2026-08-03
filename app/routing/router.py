"""Compositional source-of-truth router with fail-closed project boundaries."""
from __future__ import annotations

from collections.abc import Iterable
import re

from .capabilities import validate_capabilities
from .models import AnswerGroundingStatus, RequestFreshness, RequestIntent, RoutingDecision, ToolExecutionPlan
from .policy import policy_for


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-яё0-9+#.-]+", text.casefold()))


def _stem(words: set[str], *prefixes: str) -> bool:
    return any(word.startswith(prefix) for word in words for prefix in prefixes)


def _contains(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


class SemanticFeatures:
    """General linguistic/domain features, independent of regression phrases."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.words = _words(text)

    @property
    def temporal(self) -> bool:
        return _stem(self.words, "сейчас", "сегодня", "завтра", "вчера", "текущ", "актуальн", "свеж", "последн", "ближайш", "новейш")

    @property
    def stable_framing(self) -> bool:
        return _stem(self.words, "почему", "объясн", "определ", "истори", "формул", "измер") or _contains(self.text, r"\b(?:что такое|как (?:работает|устроен|рассчитать|сделать))\b")

    @property
    def explicit_research(self) -> bool:
        return _stem(self.words, "найд", "поищ", "исслед", "источник") or _contains(self.text, r"\b(?:в интернете|по открытым источникам)\b")

    @property
    def public_dynamic_domain(self) -> bool:
        return (
            _stem(self.words, "погод", "температур", "осад", "дожд", "снег", "гроз", "бур", "гидролог", "уров", "вод", "переправ", "пробк", "рядом")
            or _stem(self.words, "цен", "курс", "котиров", "капитализац", "акци", "валют")
            or bool(self.words & {"eth", "btc", "bitcoin", "ethereum", "доллар", "евро", "юань"})
            or _stem(self.words, "новост", "событ", "произош", "матч", "игр", "турнир", "счёт", "спорт", "результат", "выигр", "побед", "расписан", "рейс", "поезд")
            or _stem(self.words, "верси", "релиз", "обновлен", "закон", "правил", "требован", "президент", "тариф", "налог", "регламент")
            or _stem(self.words, "открыт", "закрыт", "работ", "налич", "доступ", "перекрыт")
        )

    @property
    def location_dependent(self) -> bool:
        return _stem(self.words, "погод", "температур", "осад", "дожд", "снег", "гроз", "бур", "гидролог", "уров", "вод", "переправ", "пробк", "рядом")

    @property
    def realtime_domain(self) -> bool:
        return (
            self.location_dependent
            or _stem(self.words, "новост", "событ", "матч", "игр", "турнир", "счёт", "результат", "выигр", "побед")
            or _stem(self.words, "цен", "курс", "котиров", "капитализац")
            or bool(self.words & {"eth", "btc", "bitcoin", "ethereum", "доллар", "евро", "юань"})
        )

    @property
    def explicit_place(self) -> bool:
        return bool(re.search(r"\b(?:в|во|на|для)\s+[А-ЯЁA-Z][\w.-]+", self.text))

    @property
    def write_action(self) -> bool:
        return _stem(self.words, "исправ", "измени", "запиш", "удал", "созда", "примен", "перезапуст", "рестарт", "задепло", "deploy", "commit", "push", "chmod", "chown", "setfacl")

    @property
    def external_project(self) -> str | None:
        normalized = self.text.casefold()
        if re.search(r"\bcrypto[- ]?bot\b", normalized): return "crypto-bot"
        if re.search(r"\b(?:fin[- ]?)?vpn[- ]?bot\b", normalized): return "fin-vpn-bot"
        return None

    @property
    def crypto_runtime(self) -> bool:
        project = self.external_project == "crypto-bot"
        runtime = _stem(self.words, "состоян", "статус", "позици", "баланс", "equity", "pnl", "score", "confidence", "candidate", "production", "сделк", "свеч", "runtime", "health", "решен")
        bot_context = project or _stem(self.words, "бот")
        return (bot_context and (runtime or _stem(self.words, "проверь", "покаж"))) or _stem(self.words, "equity", "pnl", "confidence") or (_stem(self.words, "позици") and _stem(self.words, "активн"))

    @property
    def direct_location(self) -> bool:
        return _stem(self.words, "геолокац", "часов") or _contains(self.text, r"\bгде я\b", r"\bкоторый час\b")

    @property
    def server_runtime(self) -> bool:
        if self.stable_framing and not self.temporal:
            return False
        action = _stem(self.words, "покаж", "проверь", "статус", "состоян", "нагруз", "груз", "использ", "uptime")
        target = _stem(self.words, "сервер", "systemd", "сервис", "процесс", "памят", "cpu", "диск")
        return (action and target) or (_stem(self.words, "лог") and _stem(self.words, "покаж", "проверь", "последн"))

    @property
    def ambiguous(self) -> bool:
        short = len(self.words) <= 5
        deictic = bool(self.words & {"это", "там", "туда", "так", "бот"}) or _stem(
            self.words, "эт", "там", "вот"
        )
        underspecified_subject = _stem(self.words, "дел") and _stem(self.words, "бот")
        continuation = self.text.casefold().strip() in {"а завтра?", "а сейчас?", "и что?"}
        return short and (deictic or underspecified_subject or continuation) and not self.crypto_runtime


class CurrentInformationPolicy:
    """Compatibility facade over semantic freshness/source features."""

    def requires_current_data(self, text: str, intents: Iterable[RequestIntent]) -> bool:
        features = SemanticFeatures(text)
        private = set(intents) & {RequestIntent.DOCUMENT, RequestIntent.IMAGE, RequestIntent.MEMORY, RequestIntent.REMINDER, RequestIntent.SSH_DIAGNOSTICS, RequestIntent.CRYPTO_CONTROL}
        return not private and (features.explicit_research or (features.public_dynamic_domain and (features.temporal or not features.stable_framing)))

    @staticmethod
    def needs_saved_location(text: str) -> bool:
        features = SemanticFeatures(text)
        return features.location_dependent and not features.explicit_place


class UniversalRequestRouter:
    """Select required sources by meaning; never delegate safety to model prose."""

    def classify(self, text: str, *, location_available: bool = False,
                 document_available: bool = False, image_attached: bool = False) -> RoutingDecision:
        normalized = " ".join(str(text).strip().split())
        features = SemanticFeatures(normalized)
        semantic_intents = self._intents(features, False, False)
        intents = self._intents(features, document_available, image_attached)
        primary = intents[0]
        location_intent = RequestIntent.LOCATION_AWARE_CURRENT_INFO in intents
        needs_location = location_intent and not features.explicit_place and not location_available
        clarification = "Уточните, о каком объекте или источнике идёт речь." if primary is RequestIntent.CONVERSATION and features.ambiguous else None
        capabilities: list[str] = []
        sources: list[str] = []
        for intent in intents:
            policy = policy_for(intent)
            capabilities.extend(policy.capabilities)
            sources.append(policy.source)
        capabilities = list(dict.fromkeys(capabilities))
        if "location" in capabilities and "web_search" in capabilities:
            capabilities = ["location", "web_search", *(
                item for item in capabilities if item not in {"location", "web_search"}
            )]
        if primary in {RequestIntent.DOCUMENT, RequestIntent.IMAGE}:
            capabilities = list(policy_for(primary).capabilities)
        if features.direct_location and primary is RequestIntent.LOCATION_AWARE_CURRENT_INFO:
            capabilities = ["location"]
            needs_location = False
        if needs_location:
            capabilities = [item for item in capabilities if item != "web_search"]
        if not validate_capabilities(capabilities):
            raise ValueError("routing produced an unknown capability")
        can_answer = not needs_location and clarification is None
        plan = tuple(ToolExecutionPlan(name, required=True, fallback=None) for name in capabilities)
        semantic_primary = semantic_intents[0]
        freshness = (
            RequestFreshness.REALTIME
            if RequestIntent.LOCATION_AWARE_CURRENT_INFO in semantic_intents or features.realtime_domain and semantic_primary is RequestIntent.CURRENT_PUBLIC_INFORMATION
            else self._freshness(semantic_primary)
        )
        return RoutingDecision(
            intents=tuple(intents), freshness=freshness,
            required_capabilities=tuple(capabilities), plan=plan,
            can_answer=can_answer,
            location_source=("explicit" if features.explicit_place and location_intent else "saved" if location_available and location_intent else None),
            needs_location=needs_location, grounding=self._grounding(capabilities),
            reason_codes=(("EXTERNAL_PROJECT_WRITE_BLOCKED",) if primary is RequestIntent.UNSUPPORTED_ACTION else ("CLARIFICATION_REQUIRED",) if clarification else ("LOCATION_REQUIRED",) if needs_location else ()),
            required_source_of_truth="+".join(dict.fromkeys(sources)),
            clarification_question=clarification,
        )

    @staticmethod
    def _intents(features: SemanticFeatures, document: bool, image: bool) -> list[RequestIntent]:
        if image: return [RequestIntent.IMAGE]
        if document: return [RequestIntent.DOCUMENT]
        if features.external_project and features.write_action:
            return [RequestIntent.UNSUPPORTED_ACTION]
        found: list[RequestIntent] = []
        words = features.words
        if features.crypto_runtime: found.append(RequestIntent.CRYPTO_CONTROL)
        elif features.server_runtime: found.append(RequestIntent.SSH_DIAGNOSTICS)
        if _stem(words, "напомн", "напомин") or (_stem(words, "отмен", "перенес", "покаж") and _stem(words, "напомин")):
            found.append(RequestIntent.REMINDER)
        if _stem(words, "вспомн", "помн") or _contains(features.text, r"\bчто (?:мы|ты) решили\b"):
            found.append(RequestIntent.MEMORY)
        if features.direct_location:
            found.append(RequestIntent.LOCATION_AWARE_CURRENT_INFO)
        private_source_selected = bool(set(found) & {
            RequestIntent.CRYPTO_CONTROL,
            RequestIntent.SSH_DIAGNOSTICS,
            RequestIntent.REMINDER,
            RequestIntent.MEMORY,
        })
        explicit_multi_source = bool(words & {"и", "а"}) and features.public_dynamic_domain
        current_public = (
            features.public_dynamic_domain
            and (not private_source_selected or explicit_multi_source)
            or (features.temporal and not features.ambiguous and not private_source_selected)
        ) and not (features.stable_framing and not features.temporal)
        if current_public:
            found.append(RequestIntent.CURRENT_PUBLIC_INFORMATION)
            if features.location_dependent:
                found.append(RequestIntent.LOCATION_AWARE_CURRENT_INFO)
        elif features.explicit_research and not private_source_selected:
            found.append(RequestIntent.WEB_RESEARCH)
        if features.external_project and not found:
            found.append(RequestIntent.PROJECT_ANALYSIS)
        if not found:
            conversation = features.ambiguous or _stem(words, "привет", "спасибо", "поговор")
            found.append(RequestIntent.CONVERSATION if conversation else RequestIntent.GENERAL_KNOWLEDGE)
        return list(dict.fromkeys(found))

    @staticmethod
    def _freshness(intent: RequestIntent) -> RequestFreshness:
        if intent in {RequestIntent.CRYPTO_CONTROL, RequestIntent.SSH_DIAGNOSTICS, RequestIntent.REMINDER}:
            return RequestFreshness.PERSONAL_RUNTIME
        if intent is RequestIntent.LOCATION_AWARE_CURRENT_INFO:
            return RequestFreshness.REALTIME
        if intent in {RequestIntent.CURRENT_PUBLIC_INFORMATION, RequestIntent.WEB_RESEARCH}:
            return RequestFreshness.RECENT
        return RequestFreshness.STATIC

    @staticmethod
    def _grounding(capabilities: list[str]) -> AnswerGroundingStatus:
        if len(capabilities) > 1: return AnswerGroundingStatus.MIXED
        if "web_search" in capabilities: return AnswerGroundingStatus.WEB_GROUNDED
        if "documents" in capabilities: return AnswerGroundingStatus.DOCUMENT_GROUNDED
        if any(item in capabilities for item in ("ssh", "crypto_control", "reminders", "location")): return AnswerGroundingStatus.PERSONAL_RUNTIME_GROUNDED
        if "memory" in capabilities: return AnswerGroundingStatus.MEMORY_ASSISTED
        return AnswerGroundingStatus.MODEL_KNOWLEDGE
