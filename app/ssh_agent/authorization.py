"""Authorization boundary for trusted upper application layers."""

from typing import Protocol

from .service_models import SSHRequestContext


class SSHAuthorizer(Protocol):
    def is_allowed(self, context: SSHRequestContext) -> bool: ...


class ContextAllowlistAuthorizer:
    """Adapter used until the Telegram allowlist is wired by trusted code."""

    def is_allowed(self, context: SSHRequestContext) -> bool:
        return context.is_allowlisted is True
