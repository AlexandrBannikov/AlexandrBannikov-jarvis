"""Regression matrix for semantic current-information routing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.ai.agent import JarvisAgent
from app.routing import RequestIntent, UniversalRequestRouter
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry


CURRENT_CASES = (
    "Как погода?", "Будет дождь?", "Какая температура?",
    "Какой курс доллара?", "Сколько стоит ETH?", "Какая цена биткоина?",
    "Какие новости OpenAI?", "Что произошло сегодня?",
    "Какой уровень воды в Туре?", "Сколько воды в Иртыше?",
    "Когда следующий матч?", "Кто выиграл?", "Какая пробка сейчас?",
    "Открыт ли магазин?", "Какая версия Ubuntu последняя?",
    "Последняя версия PostgreSQL", "Свежий релиз Python",
    "Есть ли PlayStation в наличии?", "Доступен ли этот товар сейчас?",
    "Какое расписание поездов сегодня?", "Когда следующий рейс?",
    "Какой счёт матча?", "Кто победил в турнире?",
    "Какие котировки акций сегодня?", "Сколько сейчас стоит евро?",
    "Что сейчас с BTC?", "Какая капитализация Ethereum?",
    "Работает ли аптека сейчас?", "Какое время работы музея сегодня?",
    "Перекрыта ли дорога сейчас?", "Какие события сегодня?",
    "Какая погода завтра?",
)


@pytest.mark.parametrize("question", CURRENT_CASES)
def test_current_information_is_primary_and_requires_web(question):
    decision = UniversalRequestRouter().classify(question, location_available=True)
    assert decision.intent is RequestIntent.CURRENT_INFORMATION
    assert "web_search" in decision.required_capabilities
    assert "general_llm" not in decision.required_capabilities


GENERAL_CASES = (
    "Почему небо голубое?", "Что такое SSH?", "Как работает HTTP?",
    "Объясни теорему Пифагора", "Как рассчитать площадь круга?",
    "История языка Python", "Что означает инфляция?",
    "Как устроен двигатель?", "Почему идёт дождь?",
    "Что такое уровень воды?", "Как измеряют температуру?",
    "Как работает валютный курс?", "Что такое биржевая котировка?",
    "Напиши письмо", "Переведи текст", "Посчитай два плюс два",
    "Создай промпт", "Как заменить свечи?", "Объясни правила футбола",
    "Что такое операционная система Ubuntu?",
)


@pytest.mark.parametrize("question", GENERAL_CASES)
def test_stable_knowledge_does_not_use_web(question):
    decision = UniversalRequestRouter().classify(question, location_available=True)
    assert RequestIntent.CURRENT_INFORMATION not in decision.intents
    assert "web_search" not in decision.required_capabilities


LOCATION_CASES = (
    "Как погода?", "Будет дождь?", "Какая температура?",
    "Какой уровень воды?", "Сколько воды в реке?",
    "Какая пробка сейчас?", "Что рядом?", "Что рядом со мной?",
)


@pytest.mark.parametrize("question", LOCATION_CASES)
def test_current_location_question_uses_saved_location(question):
    decision = UniversalRequestRouter().classify(question, location_available=True)
    assert decision.intent is RequestIntent.CURRENT_INFORMATION
    assert decision.location_source == "saved"
    assert not decision.needs_location
    assert {"location", "web_search"} <= set(decision.required_capabilities)


PRIVATE_CASES = (
    ("Что ты помнишь сейчас?", "memory"),
    ("Вспомни последние настройки", "memory"),
    ("Проверь crypto-bot сейчас", "crypto_control"),
    ("Какая текущая позиция crypto bot?", "crypto_control"),
    ("Статус моего сервера сейчас", "ssh"),
    ("Покажи последние логи сервера", "ssh"),
    ("Покажи напоминания на сегодня", "reminders"),
    ("Отмени следующее напоминание", "reminders"),
)


@pytest.mark.parametrize(("question", "capability"), PRIVATE_CASES)
def test_private_runtime_vetoes_public_web_search(question, capability):
    decision = UniversalRequestRouter().classify(question, location_available=True)
    assert RequestIntent.CURRENT_INFORMATION not in decision.intents
    assert capability in decision.required_capabilities
    assert "web_search" not in decision.required_capabilities


class Provider:
    def __init__(self):
        self.requests = []

    def create_response(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            id="response",
            output_text="Актуальный ответ.",
            output=[
                {"type": "web_search_call", "status": "completed"},
                {"type": "message", "content": [{
                    "type": "output_text",
                    "annotations": [{
                        "type": "url_citation", "title": "Источник",
                        "url": "https://example.com/current",
                    }],
                }]},
            ],
        )


async def immediate(function, *args, **kwargs):
    return function(*args, **kwargs)


@pytest.mark.parametrize("question", CURRENT_CASES[:8])
def test_agent_offers_only_hosted_search_for_current_information(question):
    provider = Provider()
    location = Mock()
    location.get.return_value = None
    location.context.return_value = "Confirmed user location: saved city."
    answer = asyncio.run(JarvisAgent(
        provider, ToolManager(ToolRegistry()), run_sync=immediate,
        web_search_enabled=True, location_service=location,
    ).ask(question, user_id=1))
    assert "Актуальный ответ" in answer
    assert provider.requests[0]["tools"] == [
        {"type": "web_search", "search_context_size": "medium"}
    ]
    assert provider.requests[0]["tool_choice"] == "required"


@pytest.mark.parametrize("document_kind", ["document", "image"])
def test_private_document_context_never_routes_to_web(document_kind):
    decision = UniversalRequestRouter().classify(
        "Какие последние новости в этом файле?",
        document_available=document_kind == "document",
        image_attached=document_kind == "image",
        location_available=True,
    )
    assert decision.required_capabilities == ("documents",)
    assert "web_search" not in decision.required_capabilities
