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
    OPERATION_NOT_SUPPORTED = "OPERATION_NOT_SUPPORTED"
    OPERATION_PARAMETER_REQUIRED = "OPERATION_PARAMETER_REQUIRED"
    OPERATION_PARAMETER_FORBIDDEN = "OPERATION_PARAMETER_FORBIDDEN"
    INVALID_LINE_LIMIT = "INVALID_LINE_LIMIT"
    INVALID_PROCESS_SORT = "INVALID_PROCESS_SORT"
    INVALID_PROCESS_LIMIT = "INVALID_PROCESS_LIMIT"
    EXECUTION_PLAN_UNSAFE = "EXECUTION_PLAN_UNSAFE"
    SSH_EXECUTABLE_NOT_FOUND = "SSH_EXECUTABLE_NOT_FOUND"
    SSH_CONNECTION_REFUSED = "SSH_CONNECTION_REFUSED"
    SSH_CONNECTION_TIMEOUT = "SSH_CONNECTION_TIMEOUT"
    SSH_HOST_KEY_UNKNOWN = "SSH_HOST_KEY_UNKNOWN"
    SSH_HOST_KEY_MISMATCH = "SSH_HOST_KEY_MISMATCH"
    SSH_AUTHENTICATION_FAILED = "SSH_AUTHENTICATION_FAILED"
    SSH_REMOTE_COMMAND_FAILED = "SSH_REMOTE_COMMAND_FAILED"
    SSH_REMOTE_PERMISSION_DENIED = "SSH_REMOTE_PERMISSION_DENIED"
    SSH_COMMAND_TIMEOUT = "SSH_COMMAND_TIMEOUT"
    SSH_OUTPUT_TRUNCATED = "SSH_OUTPUT_TRUNCATED"
    SSH_PROCESS_ERROR = "SSH_PROCESS_ERROR"
    SSH_PLAN_UNSAFE = "SSH_PLAN_UNSAFE"
    SSH_SERVER_DISABLED = "SSH_SERVER_DISABLED"
    SSH_DISABLED = "SSH_DISABLED"
    SSH_ACCESS_DENIED = "SSH_ACCESS_DENIED"
    SSH_CONTEXT_INVALID = "SSH_CONTEXT_INVALID"
    SSH_RATE_LIMITED = "SSH_RATE_LIMITED"
    SSH_BUSY = "SSH_BUSY"
    SSH_PARSE_ERROR = "SSH_PARSE_ERROR"
    SSH_READY = "SSH_READY"
    SSH_CONFIG_MISSING = "SSH_CONFIG_MISSING"
    SSH_CONFIG_INVALID = "SSH_CONFIG_INVALID"
    SSH_CONFIG_PERMISSIONS_UNSAFE = "SSH_CONFIG_PERMISSIONS_UNSAFE"
    SSH_IDENTITY_FILE_MISSING = "SSH_IDENTITY_FILE_MISSING"
    SSH_IDENTITY_FILE_UNSAFE = "SSH_IDENTITY_FILE_UNSAFE"
    SSH_KNOWN_HOSTS_MISSING = "SSH_KNOWN_HOSTS_MISSING"
    SSH_KNOWN_HOSTS_UNSAFE = "SSH_KNOWN_HOSTS_UNSAFE"
    SSH_EXECUTABLE_MISSING = "SSH_EXECUTABLE_MISSING"
    SSH_STARTUP_VALIDATION_FAILED = "SSH_STARTUP_VALIDATION_FAILED"


_MESSAGES = {
    ErrorCode.CONFIG_NOT_FOUND: "Конфигурация SSH Agent не найдена.",
    ErrorCode.CONFIG_INVALID_JSON: "Конфигурация SSH Agent содержит некорректный JSON.",
    ErrorCode.CONFIG_INVALID_SCHEMA: "Схема конфигурации SSH Agent некорректна.",
    ErrorCode.CONFIG_UNSAFE: "Конфигурация SSH Agent небезопасна.",
    ErrorCode.SERVER_NOT_FOUND: "Сервер не найден.",
    ErrorCode.SERVER_DISABLED: "Сервер отключён.",
    ErrorCode.PROJECT_NOT_FOUND: "Проект не найден.",
    ErrorCode.SERVICE_NOT_ALLOWED: "Сервис не разрешён.",
    ErrorCode.OPERATION_NOT_SUPPORTED: "Операция SSH Agent не поддерживается.",
    ErrorCode.OPERATION_PARAMETER_REQUIRED: "Обязательный параметр операции не указан.",
    ErrorCode.OPERATION_PARAMETER_FORBIDDEN: "Параметр не разрешён для этой операции.",
    ErrorCode.INVALID_LINE_LIMIT: "Количество строк журнала некорректно.",
    ErrorCode.INVALID_PROCESS_SORT: "Способ сортировки процессов некорректен.",
    ErrorCode.INVALID_PROCESS_LIMIT: "Количество процессов некорректно.",
    ErrorCode.EXECUTION_PLAN_UNSAFE: "План выполнения SSH Agent небезопасен.",
    ErrorCode.SSH_EXECUTABLE_NOT_FOUND: "Системный SSH-клиент недоступен.",
    ErrorCode.SSH_CONNECTION_REFUSED: "SSH-соединение отклонено.",
    ErrorCode.SSH_CONNECTION_TIMEOUT: "Истекло время установки SSH-соединения.",
    ErrorCode.SSH_HOST_KEY_UNKNOWN: "Ключ SSH-сервера не является доверенным.",
    ErrorCode.SSH_HOST_KEY_MISMATCH: "Ключ SSH-сервера изменился.",
    ErrorCode.SSH_AUTHENTICATION_FAILED: "SSH-аутентификация не удалась.",
    ErrorCode.SSH_REMOTE_COMMAND_FAILED: "Удалённая команда завершилась с ошибкой.",
    ErrorCode.SSH_REMOTE_PERMISSION_DENIED: "Удалённая read-only операция не имеет прав на чтение.",
    ErrorCode.SSH_COMMAND_TIMEOUT: "Истекло время выполнения удалённой команды.",
    ErrorCode.SSH_OUTPUT_TRUNCATED: "Вывод удалённой команды был ограничен.",
    ErrorCode.SSH_PROCESS_ERROR: "Ошибка системного SSH-процесса.",
    ErrorCode.SSH_PLAN_UNSAFE: "План выполнения SSH Agent небезопасен.",
    ErrorCode.SSH_SERVER_DISABLED: "Сервер отключён.",
    ErrorCode.SSH_DISABLED: "SSH Agent отключён.",
    ErrorCode.SSH_ACCESS_DENIED: "Доступ к SSH Agent запрещён.",
    ErrorCode.SSH_CONTEXT_INVALID: "Контекст запроса SSH Agent некорректен.",
    ErrorCode.SSH_RATE_LIMITED: "Слишком много запросов к SSH Agent. Повторите позже.",
    ErrorCode.SSH_BUSY: "SSH Agent занят. Повторите позже.",
    ErrorCode.SSH_PARSE_ERROR: "Ответ сервера получен, но не распознан полностью.",
    ErrorCode.SSH_READY: "SSH Agent готов.",
    ErrorCode.SSH_CONFIG_MISSING: "Конфигурация SSH Agent отсутствует.",
    ErrorCode.SSH_CONFIG_INVALID: "Конфигурация SSH Agent некорректна.",
    ErrorCode.SSH_CONFIG_PERMISSIONS_UNSAFE: "Права конфигурации SSH Agent небезопасны.",
    ErrorCode.SSH_IDENTITY_FILE_MISSING: "Файл SSH identity отсутствует.",
    ErrorCode.SSH_IDENTITY_FILE_UNSAFE: "Права SSH identity небезопасны.",
    ErrorCode.SSH_KNOWN_HOSTS_MISSING: "Файл SSH known_hosts отсутствует.",
    ErrorCode.SSH_KNOWN_HOSTS_UNSAFE: "Права SSH known_hosts небезопасны.",
    ErrorCode.SSH_EXECUTABLE_MISSING: "Системный OpenSSH недоступен.",
    ErrorCode.SSH_STARTUP_VALIDATION_FAILED: "Проверка готовности SSH Agent не пройдена.",
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


class OperationPolicyError(SSHAgentError):
    pass


class ExecutionPlanError(SSHAgentError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.EXECUTION_PLAN_UNSAFE)
