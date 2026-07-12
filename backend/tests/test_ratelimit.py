import asyncio
import time

import pytest

from backend.ratelimit import TokenBucket


def test_try_acquire_consumes_tokens():
    bucket = TokenBucket(rate=10, capacity=5)
    assert bucket.try_acquire(3) is True
    assert bucket.available_tokens == pytest.approx(2, abs=0.05)


def test_try_acquire_fails_when_insufficient_tokens():
    bucket = TokenBucket(rate=10, capacity=5)
    assert bucket.try_acquire(5) is True
    assert bucket.try_acquire(1) is False


def test_tokens_recover_over_time():
    bucket = TokenBucket(rate=100, capacity=5)
    assert bucket.try_acquire(5) is True
    time.sleep(0.06)  # ~6 tokens worth of refill at rate=100/s
    assert bucket.try_acquire(1) is True


def test_defaults_capacity_to_rate():
    bucket = TokenBucket(rate=3)
    assert bucket.capacity == 3


def test_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        TokenBucket(rate=0)


def test_acquire_rejects_more_than_capacity():
    bucket = TokenBucket(rate=10, capacity=5)
    with pytest.raises(ValueError):
        asyncio.run(bucket.acquire(6))


@pytest.mark.asyncio
async def test_acquire_blocks_until_tokens_available():
    bucket = TokenBucket(rate=100, capacity=1)
    assert bucket.try_acquire(1) is True

    start = time.monotonic()
    await bucket.acquire(1)
    elapsed = time.monotonic() - start

    # Needed ~1 token at rate=100/s -> ~0.01s; allow generous margin.
    assert elapsed >= 0.005
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_acquire_consumes_available_tokens_immediately():
    bucket = TokenBucket(rate=10, capacity=5)
    start = time.monotonic()
    await bucket.acquire(5)
    elapsed = time.monotonic() - start
    assert elapsed < 0.05
