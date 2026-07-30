"""Production-safe orchestration over registry, policy and OpenSSH transport."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
import logging
import os
import re
import time

from .errors import ErrorCode, SSHAgentError
from .authorization import ContextAllowlistAuthorizer, SSHAuthorizer
from .execution_plan import (
    CompositeExecutionPlan, ExecutionPlan, ProjectListResult, ServerListResult,
)
from .formatter import format_result
from .limits import BusyError, ConcurrencyLimiter, RateLimiter
from .metrics import SSHMetrics
from .operations import OperationName
from .parsers import PARSERS
from .policy import CommandPolicy
from .registry import ServerRegistry
from .service_models import SSHRequestContext, SSHServiceResult
from .transport import execute
from .transport_models import ExecutionResult

Transport = Callable[[object, ExecutionPlan], Awaitable[ExecutionResult]]
_REQUEST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_LOG = logging.getLogger("jarvis.ssh_agent.service")
_SAFE_MESSAGES = {
    ErrorCode.SSH_DISABLED: "SSH Agent отключён.",
    ErrorCode.SSH_ACCESS_DENIED: "Доступ к SSH Agent запрещён.",
    ErrorCode.SSH_CONTEXT_INVALID: "Некорректный контекст SSH-запроса.",
    ErrorCode.SSH_RATE_LIMITED: "Слишком много запросов. Повторите позже.",
    ErrorCode.SSH_BUSY: "SSH Agent занят. Повторите позже.",
    ErrorCode.SERVER_NOT_FOUND: "Сервер не найден в разрешённой конфигурации.",
    ErrorCode.SERVER_DISABLED: "Сервер временно отключён.",
    ErrorCode.PROJECT_NOT_FOUND: "Проект не найден на выбранном сервере.",
    ErrorCode.SERVICE_NOT_ALLOWED: "Этот сервис не разрешён для проекта.",
    ErrorCode.SSH_CONNECTION_REFUSED: "Сервер недоступен.",
    ErrorCode.SSH_CONNECTION_TIMEOUT: "Сервер недоступен.",
    ErrorCode.SSH_HOST_KEY_UNKNOWN: "SSH host key не подтверждён.",
    ErrorCode.SSH_HOST_KEY_MISMATCH: "Ключ сервера изменился. Подключение заблокировано.",
    ErrorCode.SSH_AUTHENTICATION_FAILED: "SSH-аутентификация не прошла.",
    ErrorCode.SSH_COMMAND_TIMEOUT: "Проверка заняла слишком много времени и была остановлена.",
    ErrorCode.SSH_REMOTE_COMMAND_FAILED: "Удалённая проверка завершилась с ошибкой.",
    ErrorCode.SSH_OUTPUT_TRUNCATED: "Часть вывода была сокращена.",
    ErrorCode.SSH_PROCESS_ERROR: "SSH-проверка временно недоступна.",
}


def parse_feature_flag(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def ssh_enabled_from_environment(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Read only the explicit JARVIS_SSH_ENABLED flag; missing/invalid is false."""
    values = os.environ if environ is None else environ
    return parse_feature_flag(values.get("JARVIS_SSH_ENABLED"))


class SSHService:
    def __init__(
        self, registry: ServerRegistry, *, enabled: bool = False,
        rate_limiter: RateLimiter | None = None,
        concurrency_limiter: ConcurrencyLimiter | None = None,
        transport: Transport = execute, metrics: SSHMetrics | None = None,
        authorizer: SSHAuthorizer | None = None,
    ) -> None:
        self._registry = registry
        self._policy = CommandPolicy(registry)
        self._enabled = enabled is True
        self._rate = rate_limiter or RateLimiter()
        self._concurrency = concurrency_limiter or ConcurrencyLimiter()
        self._transport = transport
        self._authorizer = authorizer or ContextAllowlistAuthorizer()
        self.metrics = metrics or SSHMetrics()

    async def list_servers(self, context: SSHRequestContext) -> SSHServiceResult:
        return await self._request(context, OperationName.LIST_SERVERS)

    async def list_projects(self, context: SSHRequestContext, server_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.LIST_PROJECTS, server_alias)

    async def get_server_summary(self, context: SSHRequestContext, server_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.SERVER_SUMMARY, server_alias)

    async def get_disk_usage(self, context: SSHRequestContext, server_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.DISK_USAGE, server_alias)

    async def get_memory_usage(self, context: SSHRequestContext, server_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.MEMORY_USAGE, server_alias)

    async def get_load_average(self, context: SSHRequestContext, server_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.LOAD_AVERAGE, server_alias)

    async def get_uptime(self, context: SSHRequestContext, server_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.UPTIME, server_alias)

    async def get_service_status(self, context: SSHRequestContext, server_alias: str,
                                 project_alias: str, service_name: str) -> SSHServiceResult:
        return await self._request(context, OperationName.SERVICE_STATUS, server_alias,
                                   project_alias=project_alias, service_name=service_name)

    async def get_service_recent_logs(self, context: SSHRequestContext, server_alias: str,
                                      project_alias: str, service_name: str,
                                      lines: int = 50) -> SSHServiceResult:
        return await self._request(context, OperationName.SERVICE_RECENT_LOGS, server_alias,
                                   project_alias=project_alias, service_name=service_name, lines=lines)

    async def get_project_status(self, context: SSHRequestContext, server_alias: str,
                                 project_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.PROJECT_GIT_STATUS, server_alias,
                                   project_alias=project_alias)

    async def get_project_last_commit(self, context: SSHRequestContext, server_alias: str,
                                      project_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.PROJECT_LAST_COMMIT, server_alias,
                                   project_alias=project_alias)

    async def get_project_summary(self, context: SSHRequestContext, server_alias: str,
                                  project_alias: str) -> SSHServiceResult:
        return await self._request(context, OperationName.PROJECT_SUMMARY, server_alias,
                                   project_alias=project_alias)

    async def _request(self, context: object, operation: str, server_alias: str | None = None,
                       **parameters: object) -> SSHServiceResult:
        started = time.monotonic()
        self.metrics.start()
        error = self._preflight(context)
        if error:
            result = self._failure(str(operation), error, started)
            self.metrics.finish(error)
            return result
        assert type(context) is SSHRequestContext
        result: SSHServiceResult
        try:
            try:
                plan = self._policy.build_plan(operation, server_alias, **parameters)
            except SSHAgentError as exc:
                result = self._failure(str(operation), exc.code, started)
            else:
                result = await self._dispatch(context, plan, server_alias, parameters, started)
        except asyncio.CancelledError:
            self.metrics.finish(ErrorCode.SSH_PROCESS_ERROR)
            raise
        except Exception:
            result = self._failure(str(operation), ErrorCode.SSH_PROCESS_ERROR, started)
        self.metrics.finish(result.error_code)
        _LOG.info("ssh_service_request", extra={
            "request_id": context.request_id, "server_alias": result.server_alias or "",
            "operation": result.operation,
            "result_code": result.error_code.value if result.error_code else "OK",
            "duration_ms": result.duration_ms, "truncated": result.truncated,
            "timed_out": result.error_code is ErrorCode.SSH_COMMAND_TIMEOUT,
        })
        return result

    def _preflight(self, context: object) -> ErrorCode | None:
        if type(context) is not SSHRequestContext:
            return ErrorCode.SSH_CONTEXT_INVALID
        if (
            isinstance(context.user_id, bool) or context.user_id <= 0
            or isinstance(context.chat_id, bool) or context.chat_id == 0
            or type(context.requested_at) is not datetime or context.requested_at.tzinfo is None
            or not isinstance(context.request_id, str)
            or _REQUEST_RE.fullmatch(context.request_id) is None
        ):
            return ErrorCode.SSH_CONTEXT_INVALID
        try:
            allowed = self._authorizer.is_allowed(context)
        except Exception:
            allowed = False
        if allowed is not True:
            return ErrorCode.SSH_ACCESS_DENIED
        if not self._enabled:
            return ErrorCode.SSH_DISABLED
        if not self._rate.allow(context.user_id):
            return ErrorCode.SSH_RATE_LIMITED
        return None

    async def _dispatch(self, context: SSHRequestContext, plan: object,
                        server_alias: str | None, parameters: Mapping[str, object],
                        started: float) -> SSHServiceResult:
        if type(plan) is ServerListResult:
            data = {"items": tuple({"alias": x.alias, "enabled": x.enabled,
                                    "project_count": x.project_count} for x in plan.servers)}
            return self._success("list_servers", data, started)
        if type(plan) is ProjectListResult:
            data = {"items": tuple({"alias": x.alias, "services": x.services} for x in plan.projects)}
            return self._success("list_projects", data, started, server_alias=plan.server_alias)
        if type(plan) is ExecutionPlan:
            return await self._execute_one(context, plan, parameters, started)
        if type(plan) is CompositeExecutionPlan:
            children = []
            for child in plan.children:
                children.append(await self._execute_one(context, child, parameters, started))
            partial = any(not child.success for child in children)
            success = any(child.success for child in children)
            return SSHServiceResult(
                success, str(plan.operation), plan.server_alias,
                parameters.get("project_alias") if isinstance(parameters.get("project_alias"), str) else None,
                data={"results": tuple(children)}, message="Данные получены частично." if partial else "",
                error_code=None if success else ErrorCode.SSH_PROCESS_ERROR,
                duration_ms=int((time.monotonic() - started) * 1000),
                truncated=any(child.truncated for child in children), partial=partial,
            )
        return self._failure("", ErrorCode.SSH_PLAN_UNSAFE, started)

    async def _execute_one(self, context: SSHRequestContext, plan: ExecutionPlan,
                           parameters: Mapping[str, object], started: float) -> SSHServiceResult:
        try:
            async with self._concurrency.permit(context.user_id, plan.server_alias):
                server = self._registry.get_server(plan.server_alias)
                raw = await self._transport(server, plan)
        except BusyError:
            return self._failure(plan.operation, ErrorCode.SSH_BUSY, started,
                                 server_alias=plan.server_alias)
        except SSHAgentError as exc:
            return self._failure(plan.operation, exc.code, started)
        if not raw.success:
            return self._failure(plan.operation, raw.error_code or ErrorCode.SSH_PROCESS_ERROR,
                                 started, server_alias=plan.server_alias,
                                 truncated=raw.truncated, duration_ms=raw.duration_ms)
        parser = PARSERS.get(plan.operation)
        try:
            data = parser(raw.stdout) if parser else {"summary": raw.stdout[:4096]}
            warning = ""
        except (ValueError, KeyError, OverflowError):
            data = {"summary": raw.stdout[:4096]}
            warning = "Ответ получен, но распознан не полностью."
        return SSHServiceResult(
            True, plan.operation, plan.server_alias,
            parameters.get("project_alias") if isinstance(parameters.get("project_alias"), str) else
                plan.metadata.get("project") if isinstance(plan.metadata.get("project"), str) else None,
            parameters.get("service_name") if isinstance(parameters.get("service_name"), str) else
                plan.metadata.get("service") if isinstance(plan.metadata.get("service"), str) else None,
            data, warning, ErrorCode.SSH_PARSE_ERROR if warning else None,
            raw.duration_ms, raw.truncated,
        )

    @staticmethod
    def _success(operation: str, data: Mapping[str, object], started: float,
                 server_alias: str | None = None) -> SSHServiceResult:
        return SSHServiceResult(True, operation, server_alias, data=data,
                                duration_ms=int((time.monotonic() - started) * 1000))

    @staticmethod
    def _failure(operation: str, code: ErrorCode, started: float, *,
                 server_alias: str | None = None, truncated: bool = False,
                 duration_ms: int | None = None) -> SSHServiceResult:
        return SSHServiceResult(
            False, operation, server_alias, message=_SAFE_MESSAGES.get(code, "SSH-запрос не выполнен."),
            error_code=code, duration_ms=duration_ms if duration_ms is not None else
                int((time.monotonic() - started) * 1000), truncated=truncated,
        )

    @staticmethod
    def format(result: SSHServiceResult) -> str:
        return format_result(result)
