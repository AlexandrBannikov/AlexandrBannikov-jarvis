"""Bounded monotonic rate limiting and atomic concurrency admission."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from collections.abc import Callable, AsyncIterator


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(
        self, rate_per_minute: int = 10, burst: int = 3, *,
        max_users: int = 2048, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(rate_per_minute, burst, max_users) < 1:
            raise ValueError("rate limits must be positive")
        self._rate = rate_per_minute / 60.0
        self._burst = burst
        self._max_users = max_users
        self._clock = clock
        self._stale_after = max(60.0, (burst / self._rate) * 2)
        self._buckets: OrderedDict[int, _Bucket] = OrderedDict()

    @property
    def tracked_users(self) -> int:
        return len(self._buckets)

    def allow(self, user_id: int) -> bool:
        now = self._clock()
        stale = [
            key for key, value in self._buckets.items()
            if now - value.updated >= self._stale_after
        ]
        for key in stale:
            self._buckets.pop(key, None)
        bucket = self._buckets.pop(user_id, None)
        if bucket is None:
            bucket = _Bucket(float(self._burst), now)
        else:
            bucket.tokens = min(
                float(self._burst), bucket.tokens + max(0.0, now - bucket.updated) * self._rate
            )
            bucket.updated = now
        allowed = bucket.tokens >= 1.0
        if allowed:
            bucket.tokens -= 1.0
        self._buckets[user_id] = bucket
        while len(self._buckets) > self._max_users:
            self._buckets.popitem(last=False)
        return allowed


class BusyError(Exception):
    pass


class ConcurrencyLimiter:
    def __init__(self, global_limit: int = 4, per_user: int = 2, per_server: int = 2) -> None:
        if min(global_limit, per_user, per_server) < 1:
            raise ValueError("concurrency limits must be positive")
        self._limits = global_limit, per_user, per_server
        self._lock = asyncio.Lock()
        self._global = 0
        self._users: dict[int, int] = {}
        self._servers: dict[str, int] = {}

    @property
    def active(self) -> int:
        return self._global

    @asynccontextmanager
    async def permit(self, user_id: int, server_alias: str) -> AsyncIterator[None]:
        async with self._lock:
            gl, ul, sl = self._limits
            if (
                self._global >= gl
                or self._users.get(user_id, 0) >= ul
                or self._servers.get(server_alias, 0) >= sl
            ):
                raise BusyError()
            self._global += 1
            self._users[user_id] = self._users.get(user_id, 0) + 1
            self._servers[server_alias] = self._servers.get(server_alias, 0) + 1
        try:
            yield
        finally:
            async with self._lock:
                self._global -= 1
                self._decrement(self._users, user_id)
                self._decrement(self._servers, server_alias)

    @staticmethod
    def _decrement(values: dict, key: object) -> None:
        remaining = values[key] - 1
        if remaining:
            values[key] = remaining
        else:
            del values[key]
