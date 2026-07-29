"""Minimal local health endpoint for production supervision."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.request import urlopen

PROCESS_STARTED_AT = time.monotonic()
_REMINDER_PROVIDER = None
_REMINDER_STORAGE = None
_REMINDERS_ENABLED = False


def set_reminder_health_provider(provider, *, enabled: bool, storage=None) -> None:
    global _REMINDER_PROVIDER, _REMINDER_STORAGE, _REMINDERS_ENABLED
    _REMINDER_PROVIDER = provider
    _REMINDER_STORAGE = storage
    _REMINDERS_ENABLED = enabled


def health_payload() -> dict[str, object]:
    payload = {
        "status": "ok",
        "service": "jarvis",
        "uptime_seconds": round(time.monotonic() - PROCESS_STARTED_AT, 3),
        "reminders_enabled": _REMINDERS_ENABLED,
        "reminder_scheduler_running": False,
        "reminder_database_ok": not _REMINDERS_ENABLED,
        "active_reminders_count": 0,
        "due_reminders_count": 0,
        "failed_reminders_count": 0,
        "last_scheduler_tick": None,
        "last_successful_delivery": None,
        "last_scheduler_error_code": None,
    }
    if _REMINDERS_ENABLED and _REMINDER_STORAGE is not None:
        try:
            metrics = _REMINDER_STORAGE.metrics()
            scheduler = _REMINDER_PROVIDER() if _REMINDER_PROVIDER else None
            payload.update(
                {
                    "reminder_scheduler_running": bool(
                        scheduler and scheduler.running
                    ),
                    "reminder_database_ok": _REMINDER_STORAGE.validate_schema(),
                    "active_reminders_count": metrics["active"],
                    "due_reminders_count": metrics["due"],
                    "failed_reminders_count": metrics["failed"],
                    "last_scheduler_tick": getattr(scheduler, "last_tick", None),
                    "last_successful_delivery": getattr(
                        scheduler, "last_successful_delivery", None
                    ),
                    "last_scheduler_error_code": getattr(
                        scheduler, "last_error_code", None
                    ),
                }
            )
        except Exception:
            payload["reminder_database_ok"] = False
            payload["last_scheduler_error_code"] = "HEALTH_DATABASE_ERROR"
    return payload


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps(health_payload()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class HealthServer:
    def __init__(self, host: str, port: int) -> None:
        self.server = ThreadingHTTPServer((host, port), _HealthHandler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="jarvis-health",
            daemon=True,
        )

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def probe_health(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with urlopen(
            f"http://{host}:{port}/health", timeout=timeout
        ) as response:
            payload = json.load(response)
        return response.status == 200 and payload.get("status") == "ok"
    except (OSError, ValueError):
        return False
