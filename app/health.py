"""Minimal local health endpoint for production supervision."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.request import urlopen

PROCESS_STARTED_AT = time.monotonic()


def health_payload() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "jarvis",
        "uptime_seconds": round(time.monotonic() - PROCESS_STARTED_AT, 3),
    }


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
