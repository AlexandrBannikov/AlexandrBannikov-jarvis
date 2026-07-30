"""Bounded OpenAI Responses API agent loop for read-only Jarvis tools."""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.ai.prompts import JARVIS_SYSTEM_PROMPT
from app.ai.provider import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMConfigurationError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMPermissionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMWebSearchUnavailableError,
    LLMWebSearchUnsupportedError,
)
from app.ai.tool_adapter import (
    ToolAdapter,
    ToolCallValidationError,
    serialize_tool_result,
)
from app.tools.manager import ToolManager
from app.tools.result import ToolResult
from app.memory.manager import MemoryManager
from app.ssh_agent.service_models import SSHRequestContext
from app.ssh_agent.tools import SSHServiceTool

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("jarvis.audit")
MAX_TOOL_ROUNDS = 4
TOOL_ROUND_LIMIT_MESSAGE = (
    "Не удалось завершить запрос: превышен лимит вызовов инструментов."
)
EMPTY_RESPONSE_MESSAGE = "AI-сервис вернул пустой ответ."
WEB_SEARCH_DISABLED_MESSAGE = "Поиск в интернете сейчас отключён."
WEB_SEARCH_UNAVAILABLE_MESSAGE = (
    "Веб-поиск временно недоступен. Попробуйте позже."
)
WEB_SEARCH_UNSUPPORTED_MESSAGE = (
    "Текущая модель не поддерживает веб-поиск."
)
WEB_SEARCH_EMPTY_MESSAGE = (
    "Не удалось найти надёжную актуальную информацию по этому запросу."
)
WEB_SEARCH_SECRET_MESSAGE = (
    "Запрос содержит потенциальный секрет. Удалите или замените его перед "
    "поиском в интернете."
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+\S+"),
    re.compile(
        r"(?i)\b(?:TELEGRAM_BOT_TOKEN|OPENAI_API_KEY|PASSWORD)\s*[=:]\s*\S+"
    ),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
)
_EXPLICIT_WEB_SEARCH = re.compile(
    r"(?i)\b(?:найди(?:те)?\s+в\s+интернете|поищи(?:те)?|"
    r"проверь(?:те)?\s+актуальн|что\s+нового|какая\s+сейчас\s+версия|"
    r"последн(?:ие|яя|юю)\s+новост)"
)


class CitationParsingError(ValueError):
    """A hosted-search citation could not be rendered safely."""


def _item_value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _safe_log_value(value: object) -> str:
    text = str(value)
    if len(text) <= 128 and re.fullmatch(r"[A-Za-z0-9_.@:-]+", text):
        return text
    return "invalid"


class JarvisAgent:
    """Let OpenAI select from locally validated, read-only tools."""

    def __init__(
        self,
        provider: object,
        tool_manager: ToolManager,
        *,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        run_sync: Callable[..., Awaitable[Any]] = asyncio.to_thread,
        web_search_enabled: bool = False,
        web_search_context_size: str = "medium",
        memory_manager: MemoryManager | None = None,
    ) -> None:
        if max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be positive")
        self.provider = provider
        self.tool_manager = tool_manager
        self.adapter = ToolAdapter(tool_manager.registry)
        self.max_tool_rounds = max_tool_rounds
        self._run_sync = run_sync
        self.web_search_enabled = web_search_enabled
        self.web_search_context_size = web_search_context_size
        self.memory_manager = memory_manager

    async def ask(
        self,
        user_text: str,
        user_id: int | None = None,
        chat_id: int | None = None,
        source_message_id: int | None = None,
        is_allowlisted: bool = False,
    ) -> str:
        started_at = time.monotonic()
        rounds = 0
        success = False
        error_type = "none"
        web_search_was_used = False
        ssh_context = self._ssh_context(
            user_id, chat_id, source_message_id, is_allowlisted
        )
        audit_logger.info(
            "agent_request_started user_id=%s text_length=%d "
            "web_search_enabled=%s",
            user_id,
            len(user_text),
            str(self.web_search_enabled).lower(),
        )
        contains_secret = self._contains_potential_secret(user_text)
        explicit_search = bool(_EXPLICIT_WEB_SEARCH.search(user_text))
        if explicit_search and not self.web_search_enabled:
            audit_logger.info(
                "agent_request_finished user_id=%s tool_rounds=0 "
                "success=false error_type=web_search_disabled "
                "duration_ms=%.3f",
                user_id,
                (time.monotonic() - started_at) * 1_000,
            )
            return WEB_SEARCH_DISABLED_MESSAGE
        if explicit_search and contains_secret:
            audit_logger.info(
                "agent_request_finished user_id=%s tool_rounds=0 "
                "success=false error_type=web_search_secret_blocked "
                "duration_ms=%.3f",
                user_id,
                (time.monotonic() - started_at) * 1_000,
            )
            return WEB_SEARCH_SECRET_MESSAGE
        try:
            instructions = JARVIS_SYSTEM_PROMPT
            if self.memory_manager is not None:
                await self._run_sync(
                    self.memory_manager.autosave, user_text, owner_id=user_id or 0
                )
                memory_context = await self._run_sync(
                    self.memory_manager.relevant_context, user_text,
                    owner_id=user_id or 0
                )
                if memory_context:
                    instructions = (
                        JARVIS_SYSTEM_PROMPT + "\n\n" + memory_context
                    )
            response = await self._create_response(
                input_items=[
                    {"role": "user", "content": user_text},
                ],
                tools=self._tool_schemas(allow_web=not contains_secret),
                tool_choice="auto",
                instructions=instructions,
            )
            while True:
                raw_response_id = str(
                    getattr(response, "id", "unknown")
                )
                response_id = _safe_log_value(raw_response_id)
                calls = self._function_calls(response)
                web_search_was_used = (
                    web_search_was_used
                    or self._web_search_used(response)
                )
                audit_logger.info(
                    "agent_response user_id=%s response_id=%s "
                    "tool_rounds=%d tool_calls=%d",
                    user_id,
                    response_id,
                    rounds,
                    len(calls),
                )
                if not calls:
                    sources: list[tuple[str, str]] = []
                    if web_search_was_used:
                        try:
                            sources = self._web_sources(response)
                        except CitationParsingError:
                            error_type = "web_search_citation_error"
                            self._log_web_search(
                                status="citation_error",
                                sources=0,
                                duration_ms=(
                                    time.monotonic() - started_at
                                )
                                * 1_000,
                            )
                            return WEB_SEARCH_EMPTY_MESSAGE
                        if not sources:
                            error_type = "web_search_empty"
                            self._log_web_search(
                                status="empty",
                                sources=0,
                                duration_ms=(
                                    time.monotonic() - started_at
                                )
                                * 1_000,
                            )
                            return WEB_SEARCH_EMPTY_MESSAGE
                        self._log_web_search(
                            status="completed",
                            sources=len(sources),
                            duration_ms=(
                                time.monotonic() - started_at
                            )
                            * 1_000,
                        )
                    text = str(getattr(response, "output_text", "") or "").strip()
                    if text:
                        success = True
                        return self._format_sources(text, sources)
                    error_type = "empty_response"
                    return EMPTY_RESPONSE_MESSAGE
                if rounds >= self.max_tool_rounds:
                    error_type = "tool_round_limit"
                    return TOOL_ROUND_LIMIT_MESSAGE

                rounds += 1
                outputs = []
                for call in calls:
                    outputs.append(
                        await self._execute_call(
                            call,
                            user_id=user_id,
                            chat_id=chat_id,
                            source_message_id=source_message_id,
                            ssh_context=ssh_context,
                        )
                    )
                response = await self._create_response(
                    input_items=outputs,
                    tools=self._tool_schemas(
                        allow_web=not contains_secret
                    ),
                    tool_choice="auto",
                    previous_response_id=raw_response_id,
                    instructions=instructions,
                )
        except LLMWebSearchUnsupportedError:
            error_type = "web_search_unsupported"
            self._log_web_search(
                status="unsupported",
                sources=0,
                duration_ms=(time.monotonic() - started_at) * 1_000,
            )
            return WEB_SEARCH_UNSUPPORTED_MESSAGE
        except LLMWebSearchUnavailableError:
            error_type = "web_search_unavailable"
            self._log_web_search(
                status="unavailable",
                sources=0,
                duration_ms=(time.monotonic() - started_at) * 1_000,
            )
            if explicit_search:
                return WEB_SEARCH_UNAVAILABLE_MESSAGE
            return await self._fallback_without_web(
                user_text, user_id=user_id
            )
        except LLMConfigurationError:
            error_type = "configuration_error"
            return "AI-сервис не настроен. Обратитесь к администратору."
        except LLMAuthenticationError:
            error_type = "authentication_error"
            return "Ошибка авторизации AI-сервиса. Обратитесь к администратору."
        except LLMPermissionError:
            error_type = "permission_denied"
            return "Выбранная модель недоступна для этого проекта."
        except (LLMBadRequestError, LLMModelUnavailableError):
            error_type = "model_unavailable"
            return "Выбранная модель недоступна. Обратитесь к администратору."
        except LLMRateLimitError:
            error_type = "rate_limit"
            return "Превышен лимит OpenAI. Попробуйте позже."
        except (LLMTimeoutError, LLMNetworkError, LLMProviderError):
            error_type = "timeout"
            return "Временная ошибка OpenAI. Попробуйте позже."
        except Exception:
            error_type = "internal_error"
            logger.exception("Unexpected Jarvis agent error")
            return "Произошла внутренняя ошибка. Попробуйте позже."
        finally:
            audit_logger.info(
                "agent_request_finished user_id=%s tool_rounds=%d "
                "success=%s error_type=%s duration_ms=%.3f",
                user_id,
                rounds,
                str(success).lower(),
                error_type,
                (time.monotonic() - started_at) * 1_000,
            )

    async def _create_response(self, **kwargs: Any) -> object:
        instructions = kwargs.pop("instructions", JARVIS_SYSTEM_PROMPT)
        return await self._run_sync(
            self.provider.create_response,
            instructions=instructions,
            **kwargs,
        )

    def _tool_schemas(self, *, allow_web: bool) -> list[dict[str, Any]]:
        tools = self.adapter.schemas()
        if self.web_search_enabled and allow_web:
            tools.append(
                {
                    "type": "web_search",
                    "search_context_size": self.web_search_context_size,
                }
            )
        return tools

    async def _fallback_without_web(
        self, user_text: str, *, user_id: int | None
    ) -> str:
        """Allow a stable-knowledge answer after hosted search failure."""
        try:
            response = await self._create_response(
                input_items=[{"role": "user", "content": user_text}],
                tools=self.adapter.schemas(),
                tool_choice="auto",
                instructions=(
                    JARVIS_SYSTEM_PROMPT
                    + "\nВеб-поиск временно недоступен. Отвечай только если "
                    "вопрос не требует актуальных сведений; иначе сообщи, что "
                    "актуальность не проверена."
                ),
            )
            text = str(getattr(response, "output_text", "") or "").strip()
            return text or WEB_SEARCH_UNAVAILABLE_MESSAGE
        except Exception as error:
            logger.warning(
                "Fallback without web search failed: error_type=%s",
                type(error).__name__,
            )
            return WEB_SEARCH_UNAVAILABLE_MESSAGE

    @staticmethod
    def _contains_potential_secret(text: str) -> bool:
        return any(pattern.search(text) for pattern in _SECRET_PATTERNS)

    @staticmethod
    def _web_search_used(response: object) -> bool:
        return any(
            _item_value(item, "type") == "web_search_call"
            for item in (getattr(response, "output", None) or [])
        )

    @staticmethod
    def _web_sources(response: object) -> list[tuple[str, str]]:
        sources: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in (getattr(response, "output", None) or []):
            if _item_value(item, "type") != "message":
                continue
            for content in (_item_value(item, "content", []) or []):
                for annotation in (
                    _item_value(content, "annotations", []) or []
                ):
                    if _item_value(annotation, "type") != "url_citation":
                        continue
                    citation = _item_value(
                        annotation, "url_citation", annotation
                    )
                    raw_url = str(_item_value(citation, "url", "") or "")
                    title = str(
                        _item_value(citation, "title", "Источник")
                        or "Источник"
                    ).strip()
                    try:
                        parsed = urlsplit(raw_url)
                    except ValueError as error:
                        raise CitationParsingError from error
                    if (
                        parsed.scheme not in {"http", "https"}
                        or not parsed.netloc
                    ):
                        raise CitationParsingError
                    url = urlunsplit(
                        (
                            parsed.scheme,
                            parsed.netloc,
                            parsed.path,
                            parsed.query,
                            "",
                        )
                    )
                    if url not in seen:
                        seen.add(url)
                        sources.append((title[:200], url))
        return sources

    @staticmethod
    def _format_sources(
        text: str, sources: list[tuple[str, str]]
    ) -> str:
        if not sources:
            return text
        lines = [text, "", "Источники:"]
        lines.extend(
            f"{index}. {title} — {url}"
            for index, (title, url) in enumerate(sources, start=1)
        )
        return "\n".join(lines)

    def _log_web_search(
        self, *, status: str, sources: int, duration_ms: float
    ) -> None:
        audit_logger.info(
            "web_search web_search_enabled=%s web_search_requested=true "
            "web_search_used=%s status=%s number_of_sources=%d "
            "duration_ms=%.3f",
            str(self.web_search_enabled).lower(),
            str(status in {"completed", "empty", "citation_error"}).lower(),
            status,
            sources,
            duration_ms,
        )

    @staticmethod
    def _function_calls(response: object) -> list[object]:
        return [
            item
            for item in (getattr(response, "output", None) or [])
            if _item_value(item, "type") == "function_call"
        ]

    async def _execute_call(
        self,
        call: object,
        *,
        user_id: int | None,
        chat_id: int | None = None,
        source_message_id: int | None = None,
        ssh_context: SSHRequestContext | None = None,
    ) -> dict[str, str]:
        call_id = str(_item_value(call, "call_id", ""))
        tool_name = str(_item_value(call, "name", ""))
        raw_arguments = _item_value(call, "arguments", "")
        safe_metadata: dict[str, Any] = {}
        try:
            arguments = self.adapter.parse_and_validate(
                tool_name, raw_arguments
            )
            safe_metadata = {
                name: arguments[name]
                for name in ("host_alias", "service_name")
                if name in arguments
            }
            execution_arguments = dict(arguments)
            if tool_name.startswith("remote_"):
                execution_arguments["initiator_user_id"] = user_id
            if tool_name.endswith("_reminder") or tool_name == "list_reminders":
                execution_arguments.update(
                    {
                        "trusted_user_id": user_id,
                        "trusted_chat_id": chat_id,
                        "trusted_source_message_id": source_message_id,
                    }
                )
            if tool_name in {
                "remember", "forget", "update_memory", "search_memory",
                "list_project_memory", "remember_fact", "recall_memory",
                "forget_memory", "update_project_memory", "get_project_status",
            }:
                execution_arguments["trusted_owner_id"] = user_id or 0
            tool = self.tool_manager.registry.get(tool_name)
            if isinstance(tool, SSHServiceTool):
                if ssh_context is None:
                    result = ToolResult(
                        success=False, tool=tool_name, data={},
                        message="Контекст Telegram для SSH-инструмента недоступен.",
                        duration_ms=0, error="SSH_CONTEXT_INVALID",
                    )
                else:
                    result = await tool.execute_trusted(
                        ssh_context, execution_arguments
                    )
            else:
                result = await self._run_sync(
                    self.tool_manager.execute,
                    tool_name,
                    **execution_arguments,
                )
        except ToolCallValidationError as error:
            result = ToolResult(
                success=False,
                tool=tool_name or "unknown",
                data={},
                message="Tool call was rejected by local validation.",
                duration_ms=0,
                error=error.code,
            )

        audit_logger.info(
            "agent_tool_call user_id=%s tool=%s host=%s service=%s "
            "success=%s error_type=%s",
            user_id,
            _safe_log_value(tool_name or "unknown"),
            safe_metadata.get("host_alias"),
            safe_metadata.get("service_name"),
            str(result.success).lower(),
            _safe_log_value(result.error or "none"),
        )
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": serialize_tool_result(result),
        }

    @staticmethod
    def _ssh_context(
        user_id: int | None, chat_id: int | None,
        source_message_id: int | None, is_allowlisted: bool,
    ) -> SSHRequestContext | None:
        if (
            isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0
            or isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0
            or is_allowlisted is not True
        ):
            return None
        suffix = source_message_id if isinstance(source_message_id, int) else 0
        return SSHRequestContext(
            user_id=user_id, chat_id=chat_id,
            request_id=f"telegram-{user_id}-{chat_id}-{suffix}",
            source_message_id=source_message_id if isinstance(source_message_id, int) else None,
            is_allowlisted=True, requested_at=datetime.now(timezone.utc),
        )
