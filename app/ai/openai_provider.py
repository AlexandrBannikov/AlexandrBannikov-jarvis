"""OpenAI implementation of the LLM provider interface."""

import logging
import re
import time

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from app.ai.provider import (
    LLMBadRequestError,
    LLMConfigurationError,
    LLMAuthenticationError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMPermissionError,
    LLMProvider,
    LLMProviderError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMWebSearchUnavailableError,
    LLMWebSearchUnsupportedError,
)
from app.logging_config import sanitize_log_text

logger = logging.getLogger(__name__)
DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
RESPONSES_ENDPOINT = "/v1/responses"
OPENAI_MAX_RETRIES = 3


def _request_id(error_or_response: object) -> str:
    request_id = getattr(error_or_response, "request_id", None)
    if not request_id:
        response = getattr(error_or_response, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            request_id = headers.get("x-request-id")
    if not request_id:
        request_id = getattr(error_or_response, "_request_id", None)
    value = str(request_id or "none")
    if len(value) <= 128 and all(
        character.isalnum() or character in "-_"
        for character in value
    ):
        return value
    return "invalid"


def _status_code(error: Exception) -> int | str:
    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) else "none"


def _exception_chain(error: BaseException) -> str:
    """Return exception class names only, never exception messages."""
    names: list[str] = []
    current: BaseException | None = error
    while current is not None and len(names) < 6:
        name = type(current).__name__
        names.append(
            name
            if name.replace("_", "").isalnum() and len(name) <= 80
            else "InvalidExceptionType"
        )
        current = current.__cause__ or current.__context__
    return "->".join(names)


def _error_fields(error: APIError) -> dict[str, str]:
    """Extract bounded, sanitized OpenAI error metadata without request data."""
    body = getattr(error, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    source = body if isinstance(body, dict) else {}

    def safe(name: str, fallback: object = "") -> str:
        value = source.get(name, fallback)
        text = sanitize_log_text(value or "none")
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
        return text[:240] or "none"

    return {
        "code": safe("code", getattr(error, "code", "")),
        "type": safe("type", getattr(error, "type", "")),
        "param": safe("param", getattr(error, "param", "")),
        "message": safe("message", getattr(error, "message", "")),
    }


def _is_web_search_unsupported(error: APIError) -> bool:
    fields = _error_fields(error)
    code_and_type = f"{fields['code']} {fields['type']}".lower()
    if any(
        marker in code_and_type
        for marker in (
            "web_search_unsupported",
            "unsupported_web_search",
            "web_search_not_supported",
        )
    ):
        return True
    message = fields["message"].lower()
    param = fields["param"].lower()
    names_web_search = "web_search" in message or "web search" in message
    states_unsupported = any(
        marker in message
        for marker in ("unsupported", "not supported", "does not support")
    )
    return names_web_search and states_unsupported and (
        param == "none" or param.startswith("tools")
    )


class OpenAIProvider(LLMProvider):
    """Generate responses through the current OpenAI Responses API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._client: OpenAI | None = None
        logger.info(
            "Configured LLM provider: provider=openai model=%s endpoint=%s",
            model,
            RESPONSES_ENDPOINT,
        )

    def _get_client(self) -> OpenAI:
        if not self.api_key:
            raise LLMConfigurationError("OPENAI_API_KEY is required")
        if self._client is None:
            options: dict[str, object] = {
                "api_key": self.api_key,
                "timeout": self.timeout,
                "max_retries": OPENAI_MAX_RETRIES,
                # Override an empty OPENAI_BASE_URL inherited by the SDK.
                "base_url": self.base_url or DEFAULT_OPENAI_BASE_URL,
            }
            self._client = OpenAI(**options)
        return self._client

    def generate_response(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        request: dict[str, object] = {"input": prompt}
        if system_prompt:
            request["instructions"] = system_prompt
        response = self._create(**request)
        return response.output_text

    def create_response(
        self,
        input_items: object,
        *,
        tools: list[dict[str, object]] | None = None,
        tool_choice: str = "auto",
        previous_response_id: str | None = None,
        instructions: str | None = None,
    ) -> object:
        """Create a non-streaming Responses API response with optional tools."""
        request: dict[str, object] = {"input": input_items, "stream": False}
        if instructions:
            request["instructions"] = instructions
        if tools is not None:
            request["tools"] = tools
            request["tool_choice"] = tool_choice
            if any(tool.get("type") == "web_search" for tool in tools):
                request["include"] = ["web_search_call.action.sources"]
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        return self._create(**request)

    def _create(self, **request: object) -> object:
        """Call Responses API with safe telemetry and a model fallback."""
        started_at = time.monotonic()
        model = self.model
        tools = request.get("tools")
        web_search_enabled = isinstance(tools, list) and any(
            isinstance(tool, dict) and tool.get("type") == "web_search"
            for tool in tools
        )
        try:
            try:
                response = self._get_client().responses.create(
                    model=model, **request
                )
            except (PermissionDeniedError, NotFoundError) as error:
                if model == DEFAULT_OPENAI_MODEL:
                    raise
                logger.warning(
                    "OpenAI model fallback: provider=openai model=%s "
                    "fallback_model=%s status=%s request_id=%s "
                    "error_type=%s",
                    model,
                    DEFAULT_OPENAI_MODEL,
                    _status_code(error),
                    _request_id(error),
                    type(error).__name__,
                )
                model = DEFAULT_OPENAI_MODEL
                response = self._get_client().responses.create(
                    model=model, **request
                )
                self.model = model
            logger.info(
                "OpenAI request succeeded: provider=openai model=%s "
                "endpoint=%s status=200 request_id=%s duration_ms=%.3f",
                model,
                RESPONSES_ENDPOINT,
                _request_id(response),
                (time.monotonic() - started_at) * 1_000,
            )
            return response
        except LLMConfigurationError:
            logger.error(
                "OpenAI request failed: provider=openai model=%s "
                "endpoint=%s status=none request_id=none "
                "error_type=LLMConfigurationError duration_ms=%.3f",
                model,
                RESPONSES_ENDPOINT,
                (time.monotonic() - started_at) * 1_000,
            )
            raise
        except APIError as error:
            fields = _error_fields(error)
            logger.error(
                "OpenAI request failed: provider=openai model=%s "
                "endpoint=%s status=%s request_id=%s error_type=%s "
                "error_chain=%s error_code=%s api_error_type=%s "
                "error_param=%s error_message=%s duration_ms=%.3f",
                model,
                RESPONSES_ENDPOINT,
                _status_code(error),
                _request_id(error),
                type(error).__name__,
                _exception_chain(error),
                fields["code"],
                fields["type"],
                fields["param"],
                fields["message"],
                (time.monotonic() - started_at) * 1_000,
            )
            if (
                web_search_enabled
                and isinstance(
                    error,
                    (PermissionDeniedError, NotFoundError, BadRequestError),
                )
                and _is_web_search_unsupported(error)
            ):
                translated = LLMWebSearchUnsupportedError(
                    "Hosted web search is unsupported"
                )
            elif web_search_enabled and isinstance(
                error,
                (
                    RateLimitError,
                    APITimeoutError,
                    APIConnectionError,
                    InternalServerError,
                ),
            ):
                translated = LLMWebSearchUnavailableError(
                    "Hosted web search is unavailable"
                )
            elif isinstance(error, AuthenticationError):
                translated = LLMAuthenticationError(
                    "OpenAI authentication failed"
                )
            elif isinstance(error, PermissionDeniedError):
                translated = LLMPermissionError("OpenAI permission denied")
            elif isinstance(error, NotFoundError):
                translated = LLMModelUnavailableError(
                    "OpenAI model unavailable"
                )
            elif isinstance(error, BadRequestError):
                translated = LLMBadRequestError("OpenAI request rejected")
            elif isinstance(error, RateLimitError):
                translated = LLMRateLimitError("OpenAI rate limit reached")
            elif isinstance(error, APITimeoutError):
                translated = LLMTimeoutError("OpenAI request timed out")
            elif isinstance(error, APIConnectionError):
                translated = LLMNetworkError("Could not connect to OpenAI")
            elif isinstance(error, InternalServerError):
                translated = LLMProviderError(
                    "OpenAI internal server error"
                )
            else:
                translated = LLMProviderError(
                    "OpenAI API request failed"
                )
            raise translated from error
