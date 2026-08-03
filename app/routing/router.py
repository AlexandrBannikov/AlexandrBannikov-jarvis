"""Deterministic safety envelope around semantic model/tool routing."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .capabilities import validate_capabilities
from .models import AnswerGroundingStatus, RequestFreshness, RequestIntent, RoutingDecision, ToolExecutionPlan


def _matches(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _has_explicit_place(text: str) -> bool:
    return bool(re.search(
        r"\b(?:в|во|для|на)\s+(?!(?:реке|городе|районе|улице|дороге)\b)"
        r"[\w.-]+",
        text, re.IGNORECASE,
    ))


class CurrentInformationPolicy:
    """Recognize public facts whose correct value can change over time."""

    _STABLE_FRAMING = (
        r"\b(?:что\s+такое|почему|как\s+(?:работает|устроен|рассчитать)|"
        r"объясни|история|определение|формула)\b",
    )
    _VOLATILE_FACT = (
        r"\b(?:погод|температур|дожд|снег|ливень|мороз|жарко|холодно)\w*",
        r"\b(?:новост|событи)\w*",
        r"\b(?:цен[ауы]|сто(?:ит|имость)|курс|котиров|капитализац)\w*",
        r"\b(?:доллар|евро|юан|рубл|bitcoin|биткоин|ethereum|эфир|eth|btc)\b",
        r"\b(?:уровень|сколько)\s+воды\b|\bвод[аы]\s+в\s+(?:реке|[А-ЯЁ])",
        r"\b(?:матч|игр[аы]|турнир|сч[её]т|выиграл|победил|результат)\w*",
        r"\b(?:расписани|рейс|поезд|автобус|самол[её]т)\w*",
        r"\b(?:пробк|трафик|дорог[аи]|перекрыт)\w*",
        r"\b(?:открыт|закрыт|работает\s+ли|время\s+работы)\b",
        r"\b(?:последн|свеж|новейш|актуальн)\w*\s+(?:верси|релиз|обновлен)\w*",
        r"\b(?:верси|релиз|обновлен)\w*",
        r"\b(?:в\s+наличии|доступен\s+ли|есть\s+ли\s+в\s+продаже)\b",
    )
    _TEMPORAL = (
        r"\b(?:сейчас|сегодня|завтра|вчера|последн\w*|следующ\w*|"
        r"актуальн\w*|текущ\w*|свеж\w*|на\s+данный\s+момент)\b",
    )
    _IMPLIED_CURRENT = (
        r"\bкто\s+(?:выиграл|победил)\b",
        r"\bкогда\s+следующ\w*\b",
        r"\bоткрыт\s+ли\b",
        r"\bкакая\s+температура\b",
        r"\bкакой\s+уровень\s+воды\b",
        r"\bсколько\s+воды\b",
        r"\bчто\s+рядом(?:\s+со\s+мной)?\b",
    )

    def requires_current_data(
        self, text: str, intents: Iterable[RequestIntent]
    ) -> bool:
        intent_set = set(intents)
        if intent_set & {RequestIntent.DOCUMENT_QUESTION, RequestIntent.IMAGE_QUESTION}:
            return False
        private = intent_set & {
            RequestIntent.CRYPTO_BOT_RUNTIME, RequestIntent.SERVER_RUNTIME,
            RequestIntent.MEMORY_RECALL, RequestIntent.REMINDER_ACTION,
        }
        public_specialized = intent_set & {
            RequestIntent.WEATHER, RequestIntent.NEWS, RequestIntent.FINANCE_MARKET,
        }
        if private and not public_specialized:
            return False
        if _matches(text, *self._STABLE_FRAMING) and not _matches(text, *self._TEMPORAL):
            return False
        if intent_set & {RequestIntent.WEATHER, RequestIntent.NEWS, RequestIntent.FINANCE_MARKET}:
            return True
        return _matches(text, *self._IMPLIED_CURRENT) or (
            _matches(text, *self._VOLATILE_FACT)
            and (_matches(text, *self._TEMPORAL) or self._current_value_question(text))
        )

    @staticmethod
    def _current_value_question(text: str) -> bool:
        return _matches(
            text,
            r"^(?:ка(?:кая|кой|кие)|сколько|кто|когда|где|есть\s+ли|"
            r"открыт\s+ли|будет\s+ли|что\s+с)\b",
        )

    @staticmethod
    def needs_saved_location(text: str) -> bool:
        location_domain = _matches(
            text,
            r"\b(?:погод|температур|дожд|снег|пробк|трафик)\w*|"
            r"\b(?:уровень\s+воды|сколько\s+воды|что\s+рядом|рядом\s+со\s+мной)\b",
        )
        explicit_place = _has_explicit_place(text)
        return location_domain and not explicit_place


class UniversalRequestRouter:
    """Classify meaning and freshness; execution remains in the bounded agent."""

    def classify(
        self, text: str, *, location_available: bool = False,
        document_available: bool = False, image_attached: bool = False,
    ) -> RoutingDecision:
        normalized = " ".join(str(text).strip().split())
        intents = self._intents(normalized, document_available, image_attached)
        current_policy = CurrentInformationPolicy()
        if current_policy.requires_current_data(normalized, intents):
            intents = [RequestIntent.CURRENT_INFORMATION, *intents]
        freshness = self._freshness(normalized, intents)
        capabilities: list[str] = []
        location_source = None
        needs_location = False

        if RequestIntent.CURRENT_INFORMATION in intents and current_policy.needs_saved_location(normalized):
            if location_available:
                location_source = "saved"
                capabilities.append("location")
            else:
                needs_location = True

        for intent in intents:
            if intent is RequestIntent.WEATHER:
                if RequestIntent.CURRENT_INFORMATION not in intents:
                    capabilities.append("general_llm")
                    continue
                explicit_place = _has_explicit_place(normalized)
                if explicit_place:
                    capabilities.append("web_search")
                    location_source = "explicit"
                elif location_available:
                    capabilities.extend(("location", "web_search"))
                    location_source = "saved"
                else:
                    capabilities.append("location")
                    needs_location = True
            elif intent in {RequestIntent.NEWS, RequestIntent.FINANCE_MARKET}:
                capabilities.append(
                    "web_search" if RequestIntent.CURRENT_INFORMATION in intents
                    else "general_llm"
                )
            elif intent is RequestIntent.CURRENT_INFORMATION:
                capabilities.append("web_search")
            elif intent is RequestIntent.CRYPTO_BOT_RUNTIME:
                capabilities.append("crypto_control")
            elif intent is RequestIntent.SERVER_RUNTIME:
                capabilities.append("ssh")
            elif intent in {RequestIntent.DOCUMENT_QUESTION, RequestIntent.IMAGE_QUESTION}:
                capabilities.append("documents")
            elif intent is RequestIntent.MEMORY_RECALL:
                capabilities.append("memory")
            elif intent is RequestIntent.REMINDER_ACTION:
                capabilities.append("reminders")
            elif intent in {RequestIntent.LOCATION_QUESTION, RequestIntent.TIMEZONE_QUESTION}:
                capabilities.append("location")
            else:
                capabilities.append("general_llm")

        capabilities = list(dict.fromkeys(capabilities))
        if RequestIntent.CURRENT_INFORMATION in intents:
            capabilities = [item for item in capabilities if item != "general_llm"]
        if any(item in intents for item in (RequestIntent.DOCUMENT_QUESTION, RequestIntent.IMAGE_QUESTION)):
            capabilities = ["documents"]
        if needs_location:
            capabilities = [item for item in capabilities if item != "web_search"]
        # Personal/private sources are never replaced with public search.
        fallback = {"web_search": "general_llm" if freshness is RequestFreshness.RECENT else None}
        plan = tuple(ToolExecutionPlan(name, fallback=fallback.get(name)) for name in capabilities)
        if not validate_capabilities(capabilities):
            raise ValueError("routing produced an unknown capability")
        grounding = self._grounding(capabilities)
        return RoutingDecision(
            intents=tuple(intents), freshness=freshness,
            required_capabilities=tuple(capabilities), plan=plan,
            can_answer=not needs_location, location_source=location_source,
            needs_location=needs_location, grounding=grounding,
            reason_codes=("LOCATION_REQUIRED",) if needs_location else (),
        )

    def _intents(self, text: str, document: bool, image: bool) -> list[RequestIntent]:
        found: list[RequestIntent] = []
        checks: Iterable[tuple[RequestIntent, tuple[str, ...]]] = (
            (RequestIntent.WEATHER, (
                r"\bпогод\w*", r"\bпрогноз\s+погод",
                r"\b(?:дожд|снег|ливень|мороз|жарко|холодно)\w*",
                r"\bчто\s+надеть\b",
            )),
            (RequestIntent.NEWS, (r"\bновост\w*", r"что\s+произошло\s+сегодня")),
            (RequestIntent.FINANCE_MARKET, (r"\b(?:ETH|BTC|акци\w*|курс\w*|котиров\w*)\b", r"сколько\s+(?:сейчас\s+)?стоит")),
            (RequestIntent.CRYPTO_BOT_RUNTIME, (r"\bcrypto[- ]?bot\b", r"\b(?:мой|наш)\s+бот\b", r"\b(?:позици|equity|confidence|score|pnl)\w*\b")),
            (RequestIntent.SERVER_RUNTIME, (r"\b(?:мой|моего|наш|нашего)\s+сервер\w*\b", r"\b(?:проверь|статус|состояние)\w*\s+сервер\w*\b", r"\b(?:диск|systemd|service|логи|нагрузк)\w*\b.*\bсервер")),
            (RequestIntent.REMINDER_ACTION, (r"\bнапомни\w*", r"\bнапоминани\w*", r"\bотмени\w*.*\bнапомин")),
            (RequestIntent.MEMORY_RECALL, (r"\bчто\s+ты\s+помнишь", r"\bвспомни\w*")),
            (RequestIntent.LOCATION_QUESTION, (r"\b(?:моя|мою)\s+геолокаци", r"\bгде\s+я\b")),
            (RequestIntent.TIMEZONE_QUESTION, (r"\bчасов\w*\s+пояс", r"\bкоторый\s+час\b")),
            (RequestIntent.TRANSLATION, (r"\bперевед\w*", r"\bперевод\w*\s+(?:на|с)\b")),
            (RequestIntent.PROMPT_CREATION, (r"\b(?:напиши|создай|составь)\w*\s+промпт",)),
            (RequestIntent.WRITING, (r"\b(?:напиши|составь|перепиши)\w*\s+(?:текст|письмо|поздравлен|резюме)",)),
            (RequestIntent.CALCULATION, (r"\bпосчитай\w*", r"\bсколько\s+будет\b")),
            (RequestIntent.RECOMMENDATION, (r"\b(?:посоветуй|подбери|порекомендуй|что\s+лучше)\w*",)),
        )
        for intent, patterns in checks:
            if _matches(text, *patterns): found.append(intent)
        if image:
            found.insert(0, RequestIntent.IMAGE_QUESTION)
        elif document:
            found.insert(0, RequestIntent.DOCUMENT_QUESTION)
        if not found:
            if _matches(text, r"\b(?:найди\w*|поищи\w*)\b", r"\bпроверь\w*\s+(?:информац|актуальн)", r"\b(?:сейчас|сегодня|актуальн|последн\w*)\b"):
                found.append(RequestIntent.CURRENT_INFORMATION)
            elif _matches(text, r"\b(?:что\s+такое|почему|как\s+(?:устроен|работает|сделать|заменить))\b"):
                found.append(RequestIntent.GENERAL_KNOWLEDGE)
            elif _matches(text, r"\b(?:код|python|ssh|api|программир|ошибк)\w*\b"):
                found.append(RequestIntent.TECHNICAL_HELP)
            else:
                found.append(RequestIntent.UNKNOWN)
        return list(dict.fromkeys(found))

    @staticmethod
    def _freshness(text: str, intents: list[RequestIntent]) -> RequestFreshness:
        if any(item in {RequestIntent.CRYPTO_BOT_RUNTIME, RequestIntent.SERVER_RUNTIME, RequestIntent.REMINDER_ACTION} for item in intents):
            return RequestFreshness.PERSONAL_RUNTIME
        public_specialized = any(item in {RequestIntent.WEATHER, RequestIntent.NEWS, RequestIntent.FINANCE_MARKET} for item in intents)
        stable_framing = _matches(text, *CurrentInformationPolicy._STABLE_FRAMING) and not _matches(text, *CurrentInformationPolicy._TEMPORAL)
        if public_specialized and not stable_framing:
            return RequestFreshness.REALTIME
        if RequestIntent.CURRENT_INFORMATION in intents:
            return RequestFreshness.RECENT
        if _matches(text, r"\b(?:сейчас|сегодня|актуальн|последн\w*|новейш\w*|текущ\w*)\b", r"\bпроверь\w*\s+(?:информац|актуальн)"):
            return RequestFreshness.RECENT
        return RequestFreshness.STATIC

    @staticmethod
    def _grounding(capabilities: list[str]) -> AnswerGroundingStatus:
        if len(capabilities) > 1: return AnswerGroundingStatus.MIXED
        if "web_search" in capabilities: return AnswerGroundingStatus.WEB_GROUNDED
        if "documents" in capabilities: return AnswerGroundingStatus.DOCUMENT_GROUNDED
        if any(item in capabilities for item in ("ssh", "crypto_control", "reminders", "location")):
            return AnswerGroundingStatus.PERSONAL_RUNTIME_GROUNDED
        if "memory" in capabilities: return AnswerGroundingStatus.MEMORY_ASSISTED
        return AnswerGroundingStatus.MODEL_KNOWLEDGE
