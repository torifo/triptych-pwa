"""Shared runtime pieces for catalog harvesters: clock, HTTP retry, results."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional, Protocol

import httpx

USER_AGENT = "triptych/0.1 (personal tracker)"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class Clock(Protocol):
    def now(self) -> datetime: ...  # aware UTC

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


@dataclass
class HarvestResult:
    source: str
    fetched: int = 0  # rows upserted
    slices_done: int = 0
    slices_skipped: int = 0  # already done in an earlier run
    slices_error: int = 0
    errors: list[str] = field(default_factory=list)


class Pacer:
    """Keeps at least `interval` seconds between request starts."""

    def __init__(self, interval: float, clock: Clock) -> None:
        self.interval = interval
        self.clock = clock
        self._last: Optional[float] = None

    async def wait(self) -> None:
        if self._last is not None:
            gap = self.interval - (self.clock.monotonic() - self._last)
            if gap > 0:
                await self.clock.sleep(gap)
        self._last = self.clock.monotonic()


def _retry_after_seconds(response: httpx.Response, default: float) -> float:
    value = response.headers.get("retry-after")
    if not value:
        return default
    try:
        return max(float(value), 0.0)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            return max((when - datetime.now(timezone.utc)).total_seconds(), 0.0)
        except (TypeError, ValueError):
            return default


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    clock: Clock,
    pacer: Optional[Pacer] = None,
    retries: int = 3,
    backoff: float = 2.0,
    log: Callable[[str], None] = print,
    **kwargs: Any,
) -> httpx.Response:
    """One HTTP request with pacing, 429/5xx/timeout retries and exponential backoff.

    Raises the last error when all attempts fail. 4xx other than 429 are
    returned to the caller as-is (they are not transient).
    """
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        if pacer is not None:
            await pacer.wait()
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            log(f"  retry {attempt + 1}/{retries}: {type(exc).__name__} {url}")
            await clock.sleep(backoff * (2**attempt))
            continue
        if response.status_code == 429:
            wait = _retry_after_seconds(response, 60.0)
            log(f"  429 rate limited, waiting {wait:.0f}s")
            await clock.sleep(wait)
            last_error = httpx.HTTPStatusError("429", request=response.request, response=response)
            continue
        if response.status_code >= 500:
            last_error = httpx.HTTPStatusError(
                str(response.status_code), request=response.request, response=response
            )
            log(f"  retry {attempt + 1}/{retries}: HTTP {response.status_code} {url}")
            await clock.sleep(backoff * (2**attempt))
            continue
        return response
    assert last_error is not None
    raise last_error
