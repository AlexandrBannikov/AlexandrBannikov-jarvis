"""Safe text/JSON formatting for memory diagnostics."""

from __future__ import annotations
import json
from typing import Any
from app.memory.security import redact_secrets


def safe_json(value: Any) -> str:
    text=json.dumps(value,ensure_ascii=False,indent=2,default=str)
    return redact_secrets(text)[0]


def safe_line(value: object) -> str:
    return redact_secrets(str(value).replace("\n"," "))[0]
