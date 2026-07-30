"""Immutable trusted context and public service results."""

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from collections.abc import Mapping

from .errors import ErrorCode


@dataclass(frozen=True, slots=True)
class SSHRequestContext:
    user_id: int
    chat_id: int
    request_id: str
    requested_at: datetime
    source_message_id: int | None = None
    is_allowlisted: bool = False


@dataclass(frozen=True, slots=True)
class SSHServiceResult:
    success: bool
    operation: str
    server_alias: str | None = None
    project_alias: str | None = None
    service_name: str | None = None
    data: Mapping[str, object] = MappingProxyType({})
    message: str = ""
    error_code: ErrorCode | None = None
    duration_ms: int = 0
    truncated: bool = False
    from_cache: bool = False
    partial: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
