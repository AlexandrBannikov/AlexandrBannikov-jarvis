"""Bounded incremental subprocess stream collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

READ_CHUNK_BYTES = 16 * 1024
ABSOLUTE_STREAM_HARD_CAP = 1024 * 1024


class AsyncByteReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class CapturedOutput:
    data: bytes
    observed_bytes: int
    truncated: bool


async def capture_stream(
    stream: AsyncByteReader | None,
    *,
    byte_limit: int,
    line_limit: int | None,
) -> CapturedOutput:
    """Drain a stream to EOF while retaining only bounded bytes and lines."""
    if stream is None:
        return CapturedOutput(b"", 0, False)
    retain_limit = min(byte_limit, ABSOLUTE_STREAM_HARD_CAP)
    retained = bytearray()
    observed = 0
    line_count = 0
    truncated = False
    while True:
        chunk = await stream.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        observed += len(chunk)
        if len(retained) >= retain_limit:
            truncated = True
            continue
        available = retain_limit - len(retained)
        candidate = chunk[:available]
        if line_limit is not None:
            allowed = bytearray()
            for byte in candidate:
                if line_count >= line_limit:
                    truncated = True
                    break
                allowed.append(byte)
                if byte == 10:
                    line_count += 1
            candidate = bytes(allowed)
        retained.extend(candidate)
        if len(candidate) < len(chunk) or observed > retain_limit:
            truncated = True
    return CapturedOutput(bytes(retained), observed, truncated)
