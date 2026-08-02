"""Broad offline contract for the universal request router."""

import pytest

from app.routing import RequestFreshness, RequestIntent, UniversalRequestRouter
from app.routing.capabilities import CAPABILITIES, validate_capabilities
from app.routing.guard import AnswerCapabilityGuard


INTENT_CASES = (
    ("Почему небо голубое?", RequestIntent.GENERAL_KNOWLEDGE),
    ("Что такое SSH?", RequestIntent.GENERAL_KNOWLEDGE),
    ("Как заменить свечи?", RequestIntent.GENERAL_KNOWLEDGE),
    ("Какая погода?", RequestIntent.WEATHER),
    ("Погода в Москве", RequestIntent.WEATHER),
    ("Прогноз погоды в Тюмени", RequestIntent.WEATHER),
    ("Какие новости сегодня?", RequestIntent.NEWS),
    ("Последние новости России", RequestIntent.NEWS),
    ("Что сейчас с ETH?", RequestIntent.FINANCE_MARKET),
    ("Сколько сейчас стоит BTC?", RequestIntent.FINANCE_MARKET),
    ("Курс доллара сегодня", RequestIntent.FINANCE_MARKET),
    ("Проверь crypto-bot", RequestIntent.CRYPTO_BOT_RUNTIME),
    ("Какая у бота активная позиция?", RequestIntent.CRYPTO_BOT_RUNTIME),
    ("Покажи equity crypto bot", RequestIntent.CRYPTO_BOT_RUNTIME),
    ("Проверь сервер", RequestIntent.SERVER_RUNTIME),
    ("Статус моего сервера", RequestIntent.SERVER_RUNTIME),
    ("Напомни через час", RequestIntent.REMINDER_ACTION),
    ("Покажи напоминания", RequestIntent.REMINDER_ACTION),
    ("Отмени напоминание", RequestIntent.REMINDER_ACTION),
    ("Что ты помнишь?", RequestIntent.MEMORY_RECALL),
    ("Вспомни наш проект", RequestIntent.MEMORY_RECALL),
    ("Где я?", RequestIntent.LOCATION_QUESTION),
    ("Покажи мою геолокацию", RequestIntent.LOCATION_QUESTION),
    ("Какой у меня часовой пояс?", RequestIntent.TIMEZONE_QUESTION),
    ("Который час в Москве?", RequestIntent.TIMEZONE_QUESTION),
    ("Переведи на английский", RequestIntent.TRANSLATION),
    ("Сделай перевод на русский", RequestIntent.TRANSLATION),
    ("Напиши промпт для Codex", RequestIntent.PROMPT_CREATION),
    ("Создай промпт для дизайнера", RequestIntent.PROMPT_CREATION),
    ("Напиши текст письма", RequestIntent.WRITING),
    ("Составь поздравление", RequestIntent.WRITING),
    ("Посчитай площадь комнаты", RequestIntent.CALCULATION),
    ("Сколько будет два плюс два?", RequestIntent.CALCULATION),
    ("Посоветуй автомобиль", RequestIntent.RECOMMENDATION),
    ("Что лучше для отопления?", RequestIntent.RECOMMENDATION),
    ("Какая последняя версия Python?", RequestIntent.CURRENT_INFORMATION),
    ("Проверь актуальность информации", RequestIntent.CURRENT_INFORMATION),
    ("Привет, расскажи что-нибудь", RequestIntent.UNKNOWN),
)


@pytest.mark.parametrize("location_available", [False, True])
@pytest.mark.parametrize("text,expected", INTENT_CASES)
def test_intent_taxonomy_across_location_context(text, expected, location_available):
    decision = UniversalRequestRouter().classify(text, location_available=location_available)
    assert expected in decision.intents
    assert validate_capabilities(decision.required_capabilities)


FRESHNESS_CASES = (
    ("Почему небо голубое?", RequestFreshness.STATIC),
    ("Что такое SSH?", RequestFreshness.STATIC),
    ("Как заменить свечи?", RequestFreshness.STATIC),
    ("Переведи текст", RequestFreshness.STATIC),
    ("Напиши промпт", RequestFreshness.STATIC),
    ("Какая последняя версия Python?", RequestFreshness.RECENT),
    ("Кто сейчас президент страны?", RequestFreshness.RECENT),
    ("Проверь актуальность информации", RequestFreshness.RECENT),
    ("Какие текущие требования?", RequestFreshness.RECENT),
    ("Какая погода?", RequestFreshness.REALTIME),
    ("Погода завтра в Москве", RequestFreshness.REALTIME),
    ("Новости сегодня", RequestFreshness.REALTIME),
    ("Что сейчас с ETH?", RequestFreshness.REALTIME),
    ("Сколько стоит BTC?", RequestFreshness.REALTIME),
    ("Проверь crypto-bot", RequestFreshness.PERSONAL_RUNTIME),
    ("Статус моего сервера", RequestFreshness.PERSONAL_RUNTIME),
    ("Покажи напоминания", RequestFreshness.PERSONAL_RUNTIME),
    ("Какая активная позиция?", RequestFreshness.PERSONAL_RUNTIME),
)


@pytest.mark.parametrize("document_available", [False, True])
@pytest.mark.parametrize("text,expected", FRESHNESS_CASES)
def test_freshness_is_stable_across_document_context(text, expected, document_available):
    decision = UniversalRequestRouter().classify(
        text, location_available=True, document_available=document_available
    )
    assert decision.freshness is expected


@pytest.mark.parametrize("capability", sorted(CAPABILITIES))
def test_registry_capabilities_are_closed_and_named(capability):
    assert validate_capabilities([capability])
    assert CAPABILITIES[capability].description


def test_saved_location_weather_plan():
    decision = UniversalRequestRouter().classify("Как погода?", location_available=True)
    assert decision.location_source == "saved"
    assert decision.required_capabilities == ("location", "web_search")
    assert decision.can_answer


def test_missing_location_weather_plan():
    decision = UniversalRequestRouter().classify("Как погода?", location_available=False)
    assert decision.needs_location and not decision.can_answer
    assert "web_search" not in decision.required_capabilities


def test_private_runtime_never_routes_to_web():
    for text in ("Проверь crypto-bot", "Статус моего сервера", "Покажи напоминания"):
        assert "web_search" not in UniversalRequestRouter().classify(text).required_capabilities


def test_document_never_routes_to_web():
    decision = UniversalRequestRouter().classify("Кратко расскажи", document_available=True)
    assert decision.intent is RequestIntent.DOCUMENT_QUESTION
    assert decision.required_capabilities == ("documents",)


def test_multi_intent_keeps_every_part():
    decision = UniversalRequestRouter().classify(
        "Проверь crypto-bot, скажи погоду и напомни через час", location_available=True
    )
    assert {RequestIntent.CRYPTO_BOT_RUNTIME, RequestIntent.WEATHER,
            RequestIntent.REMINDER_ACTION} <= set(decision.intents)
    assert {"crypto_control", "location", "web_search", "reminders"} <= set(decision.required_capabilities)


@pytest.mark.parametrize("phrase", ["У меня нет доступа", "Я не могу помочь", "Я не умею это", "Проверьте в другом приложении"])
def test_answer_guard_retries_premature_refusal(phrase):
    decision = UniversalRequestRouter().classify("Как погода?", location_available=True)
    assert AnswerCapabilityGuard().should_retry(phrase, decision, attempted=False)
    assert not AnswerCapabilityGuard().should_retry(phrase, decision, attempted=True)
