"""Privacy-preserving shadow routing audit and aggregate health metrics."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import re
from threading import Lock


_SAFE = re.compile(r"[A-Z0-9_.:+,-]{1,96}\Z")
_LOG = logging.getLogger("jarvis.routing.audit")


@dataclass(frozen=True, slots=True)
class RoutingAuditEvent:
    correlation_id: str
    predicted_intent: str
    selected_capability: str
    required_source_of_truth: str
    tool_requested: bool
    tool_executed: bool
    final_status: str
    safe_error_code: str
    duration_ms: int


class RoutingAudit:
    def __init__(self) -> None:
        self._lock = Lock()
        self._last: RoutingAuditEvent | None = None
        self.private_search_leakage_count = 0
        self.external_project_write_attempts = 0
        self.last_error_code = "NONE"

    @staticmethod
    def _safe(value: object, default: str = "NONE") -> str:
        text = str(value).upper()
        return text if _SAFE.fullmatch(text) else default

    def record(self, event: RoutingAuditEvent) -> None:
        safe = RoutingAuditEvent(
            self._safe(event.correlation_id), self._safe(event.predicted_intent),
            self._safe(event.selected_capability), self._safe(event.required_source_of_truth),
            bool(event.tool_requested), bool(event.tool_executed),
            self._safe(event.final_status), self._safe(event.safe_error_code),
            max(0, int(event.duration_ms)),
        )
        with self._lock:
            self._last = safe
            self.last_error_code = safe.safe_error_code
        _LOG.info(
            "routing_shadow correlation_id=%s predicted_intent=%s selected_capability=%s "
            "required_source=%s tool_requested=%s tool_executed=%s final_status=%s "
            "error_code=%s duration_ms=%d",
            *asdict(safe).values(),
        )

    @property
    def last(self) -> RoutingAuditEvent | None:
        with self._lock:
            return self._last
