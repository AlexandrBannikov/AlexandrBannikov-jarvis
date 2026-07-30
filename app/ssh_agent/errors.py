"""Stable domain errors for SSH agent configuration and lookup."""

from enum import StrEnum


class ErrorCode(StrEnum):
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_INVALID_JSON = "CONFIG_INVALID_JSON"
    CONFIG_INVALID_SCHEMA = "CONFIG_INVALID_SCHEMA"
    CONFIG_UNSAFE = "CONFIG_UNSAFE"
    SERVER_NOT_FOUND = "SERVER_NOT_FOUND"
    SERVER_DISABLED = "SERVER_DISABLED"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    SERVICE_NOT_ALLOWED = "SERVICE_NOT_ALLOWED"


_MESSAGES = {
    ErrorCode.CONFIG_NOT_FOUND: "Конфигурация SSH Agent не найдена.",
    ErrorCode.CONFIG_INVALID_JSON: "Конфигурация SSH Agent содержит некорректный JSON.",
    ErrorCode.CONFIG_INVALID_SCHEMA: "Схема конфигурации SSH Agent некорректна.",
    ErrorCode.CONFIG_UNSAFE: "Конфигурация SSH Agent небезопасна.",
    ErrorCode.SERVER_NOT_FOUND: "Сервер не найден.",
    ErrorCode.SERVER_DISABLED: "Сервер отключён.",
    ErrorCode.PROJECT_NOT_FOUND: "Проект не найден.",
    ErrorCode.SERVICE_NOT_ALLOWED: "Сервис не разрешён.",
}


class SSHAgentError(Exception):
    """Base error whose public message never contains input or filesystem details."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


class ConfigError(SSHAgentError):
    pass


class ServerNotFoundError(SSHAgentError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.SERVER_NOT_FOUND)


class ServerDisabledError(SSHAgentError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.SERVER_DISABLED)


class ProjectNotFoundError(SSHAgentError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.PROJECT_NOT_FOUND)


class ServiceNotAllowedError(SSHAgentError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.SERVICE_NOT_ALLOWED)
