"""Regression matrix for mandatory hosted Web Search execution evidence."""

import asyncio
from types import SimpleNamespace

import pytest

from app.ai.agent import JarvisAgent
from app.ai.provider import LLMNetworkError, LLMTimeoutError
from app.ai.openai_provider import web_search_execution_metadata
from app.handlers import _should_attach_document_context
from app.routing import AnswerCapabilityGuard, UniversalRequestRouter
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry


CURRENT_QUERIES = (
    "Как погода?", "Будет дождь?", "Какая температура?", "Какая погода завтра?",
    "Какой уровень воды в Туре в Тюмени?", "Сколько воды в Иртыше?",
    "Какая сейчас цена ETH?", "Сколько стоит BTC?", "Какой курс доллара?",
    "Сколько сейчас стоит евро?", "Какие последние новости OpenAI?",
    "Что произошло сегодня?", "Когда следующий матч?", "Кто выиграл?",
    "Какой счёт матча?", "Какая пробка сейчас?", "Открыт ли магазин?",
    "Какая версия Ubuntu последняя?", "Последняя версия PostgreSQL",
    "Свежий релиз Python", "Есть ли PlayStation в наличии?",
    "Доступен ли этот товар сейчас?", "Какое расписание поездов сегодня?",
    "Когда следующий рейс?", "Какие котировки акций сегодня?",
    "Какая капитализация Ethereum?", "Работает ли аптека сейчас?",
    "Какое время работы музея сегодня?", "Перекрыта ли дорога сейчас?",
    "Какие события сегодня?", "Какая цена нефти сегодня?",
    "Какая последняя версия OpenSSL?", "Последние результаты НХЛ",
    "Расписание рейсов сегодня", "Кто сейчас президент Франции?",
    "Какие новости технологий сегодня?",
)


@pytest.mark.parametrize("query", CURRENT_QUERIES)
def test_active_document_does_not_hijack_current_information(query):
    route = UniversalRequestRouter().classify(query, location_available=True)
    assert "web_search" in route.required_capabilities
    assert not _should_attach_document_context(route)


PRIVATE_QUERIES = (
    "Проверь crypto-bot", "Какая текущая позиция crypto bot?",
    "Статус моего сервера сейчас", "Покажи последние логи сервера",
    "Покажи напоминания на сегодня", "Что ты помнишь сейчас?",
    "Который час?", "Где я?",
)


@pytest.mark.parametrize("query", PRIVATE_QUERIES)
def test_active_document_does_not_hijack_private_capabilities(query):
    route = UniversalRequestRouter().classify(query, location_available=True)
    assert not _should_attach_document_context(route)


STABLE_DOCUMENT_QUERIES = (
    "Кратко расскажи", "Сделай краткое резюме", "Объясни содержание",
    "Перечисли основные тезисы", "Укажи противоречия в тексте",
    "Какой общий вывод?", "Что автор имеет в виду?", "Составь конспект",
    "Выдели определения", "Сделай таблицу фактов", "Переведи текст",
    "Исправь стиль текста", "Какие аргументы приведены?",
    "Сравни разделы", "Объясни простыми словами",
)


@pytest.mark.parametrize("query", STABLE_DOCUMENT_QUERIES)
def test_stable_questions_can_keep_active_document_context(query):
    route = UniversalRequestRouter().classify(query, location_available=True)
    assert _should_attach_document_context(route)


REFUSALS = (
    "У меня нет доступа", "Я не могу проверить", "Нет подключённого источника",
    "Доступного онлайн-источника нет", "Не располагаю актуальными данными",
    "Проверьте в другом приложении", "У меня сейчас нет доступа к данным",
    "Я не умею это проверять", "Нет доступа к интернету",
    "Я не могу помочь проверить актуальность",
)


@pytest.mark.parametrize("text", REFUSALS)
def test_required_search_refusal_is_rejected(text):
    decision = UniversalRequestRouter().classify(
        "Какие последние новости OpenAI?", location_available=True
    )
    assert AnswerCapabilityGuard().should_retry(text, decision, attempted=False)
    assert not AnswerCapabilityGuard().should_retry(text, decision, attempted=True)


def response(*, calls=0, citations=0, text="answer"):
    output = [{"type": "web_search_call", "status": "completed"} for _ in range(calls)]
    output.append({"type": "message", "content": [{
        "annotations": [{
            "type": "url_citation", "title": f"source-{index}",
            "url": f"https://example.com/{index}",
        } for index in range(citations)]
    }]})
    return SimpleNamespace(output=output, output_text=text)


@pytest.mark.parametrize(
    "calls,citations,text,executed,final_present",
    ((0, 0, "", False, False), (0, 0, "refusal", False, True),
     (1, 0, "answer", True, True), (1, 1, "answer", True, True),
     (2, 3, "answer", True, True), (3, 0, "", True, False)),
)
def test_execution_metadata_counts_hosted_evidence(
    calls, citations, text, executed, final_present
):
    metadata = web_search_execution_metadata(
        response(calls=calls, citations=citations, text=text), requested=True
    )
    assert metadata.web_search_requested
    assert metadata.web_search_call_count == calls
    assert metadata.citations_count == citations
    assert metadata.web_search_executed is executed
    assert metadata.final_text_present is final_present


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create_response(self, **kwargs):
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def immediate(function, *args, **kwargs):
    return function(*args, **kwargs)


@pytest.mark.parametrize("refusal", REFUSALS[:5])
def test_model_refusal_forces_one_required_search_retry(refusal):
    provider = Provider([
        SimpleNamespace(id="first", output=[], output_text=refusal),
        response(calls=0, citations=0, text=refusal),
    ])
    answer = asyncio.run(JarvisAgent(
        provider, ToolManager(ToolRegistry()), run_sync=immediate,
        web_search_enabled=True,
    ).ask("Какие последние новости OpenAI?", correlation_id="a" * 20))
    assert "WEB_SEARCH_NOT_EXECUTED" in answer
    assert len(provider.requests) == 2
    assert all(request["tool_choice"] == "required" for request in provider.requests)
    assert all(request["correlation_id"] == "a" * 20 for request in provider.requests)


@pytest.mark.parametrize("transient", [LLMTimeoutError(), LLMNetworkError()])
def test_transient_provider_failure_retries_once_with_same_correlation(transient):
    provider = Provider([transient, response(calls=1, citations=1, text="answer")])
    answer = asyncio.run(JarvisAgent(
        provider, ToolManager(ToolRegistry()), run_sync=immediate,
        web_search_enabled=True,
    ).ask("Какие последние новости OpenAI?", correlation_id="b" * 20))
    assert "answer" in answer
    assert [request["sdk_attempt"] for request in provider.requests] == [1, 2]
    assert all(request["correlation_id"] == "b" * 20 for request in provider.requests)
