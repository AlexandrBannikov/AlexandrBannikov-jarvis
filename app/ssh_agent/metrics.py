"""Small in-memory operational state without sensitive request data."""

from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import ErrorCode


@dataclass(slots=True)
class SSHMetrics:
    active_requests: int = 0
    last_success_at: datetime | None = None
    last_error_code: ErrorCode | None = None
    total_requests: int = 0
    total_failures: int = 0

    def start(self) -> None:
        self.active_requests += 1
        self.total_requests += 1

    def finish(self, error: ErrorCode | None) -> None:
        self.active_requests = max(0, self.active_requests - 1)
        if error is None:
            self.last_success_at = datetime.now(timezone.utc)
        else:
            self.last_error_code = error
            self.total_failures += 1
