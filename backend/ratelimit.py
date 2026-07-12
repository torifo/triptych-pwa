"""Async token-bucket rate limiter.

One instance is expected per source adapter (e.g. anilist: 60/min, narou: 1
req/s, scrape sources: 1 req/s + concurrency 1) — see specs/mvp-core/design.md,
"レート制御・キャッシュ".
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional


class TokenBucket:
    """Classic token bucket: `capacity` tokens max, refilled at `rate` tokens/sec."""

    def __init__(self, rate: float, capacity: Optional[float] = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        self._tokens = self.capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._updated_at = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block (async) until `tokens` are available, then consume them."""
        if tokens > self.capacity:
            raise ValueError("cannot acquire more tokens than bucket capacity")
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait_time = deficit / self.rate
            await asyncio.sleep(wait_time)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking attempt to consume `tokens`. Returns False if unavailable."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens
