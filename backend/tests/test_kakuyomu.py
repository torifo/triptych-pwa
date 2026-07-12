"""Tests for the kakuyomu scraping adapter.

Network access is fully mocked via httpx.MockTransport against recorded
fixtures (see backend/tests/fixtures/kakuyomu_*); no live requests are made.
The work fixture is trimmed from a real page fetch of a well-known,
book-published and anime-adapted work: 陰の実力者になりたくて！
(https://kakuyomu.jp/works/1177354054886847987).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from backend.adapters.kakuyomu import KakuyomuAdapter, validate_work_id
from backend.cache import TTLCache
from backend.dto import SourceUnavailable
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"
WORK_ID = "1177354054886847987"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make_handler(
    robots_text: str,
    work_html: str,
    work_status: int = 200,
    on_request=None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if on_request is not None:
            on_request(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots_text)
        assert request.url.path.startswith("/works/")
        return httpx.Response(work_status, text=work_html)

    return handler


def _make_adapter(
    tmp_path: Path,
    robots_text: str,
    work_html: str,
    work_status: int = 200,
    **kwargs,
) -> KakuyomuAdapter:
    handler = _make_handler(robots_text, work_html, work_status)
    return KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        **kwargs,
    )


# --- work id validation ----------------------------------------------------


def test_validate_work_id_accepts_numeric_string():
    assert validate_work_id(WORK_ID) == WORK_ID


@pytest.mark.parametrize("bad_id", ["", "abc", "12a", "-1", "works/1", "1 "])
def test_validate_work_id_rejects_non_numeric(bad_id):
    with pytest.raises(ValueError):
        validate_work_id(bad_id)


# --- normalization -----------------------------------------------------


@pytest.mark.asyncio
async def test_work_normalizes_found_page(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    adapter = _make_adapter(tmp_path, robots_text, work_html)

    dto = await adapter.work(WORK_ID)

    assert dto.source == "kakuyomu"
    assert dto.externalId == WORK_ID
    assert dto.mediaType == "web-novel"
    assert dto.title == "陰の実力者になりたくて！"
    assert dto.author == "逢沢大介"
    assert dto.episodeCount == 130
    assert dto.url == f"https://kakuyomu.jp/works/{WORK_ID}"

    assert len(dto.releases) == 1
    release = dto.releases[0]
    assert release.kind == "episode"
    assert release.date == "2019-01-02"
    assert "130" in release.label


@pytest.mark.asyncio
async def test_work_not_found_raises_source_unavailable(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    not_found_html = _read_fixture("kakuyomu_work_not_found.html")
    adapter = _make_adapter(
        tmp_path, robots_text, not_found_html, work_status=404
    )

    with pytest.raises(SourceUnavailable) as exc_info:
        await adapter.work("99999999999999999999999")

    assert exc_info.value.source == "kakuyomu"


@pytest.mark.asyncio
async def test_unparseable_page_raises_source_unavailable(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    adapter = _make_adapter(tmp_path, robots_text, "<html><body>no data here</body></html>")

    with pytest.raises(SourceUnavailable):
        await adapter.work(WORK_ID)


# --- robots.txt ----------------------------------------------------------


@pytest.mark.asyncio
async def test_robots_disallow_raises_source_unavailable_without_fetching_work(
    tmp_path,
):
    robots_text = _read_fixture("kakuyomu_robots_disallow_all.txt")
    requested_paths: list[str] = []

    def on_request(request: httpx.Request) -> None:
        requested_paths.append(request.url.path)

    handler = _make_handler(
        robots_text, work_html="<html></html>", on_request=on_request
    )
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    with pytest.raises(SourceUnavailable) as exc_info:
        await adapter.work(WORK_ID)

    assert exc_info.value.source == "kakuyomu"
    assert requested_paths == ["/robots.txt"]  # /works/... never requested


@pytest.mark.asyncio
async def test_robots_txt_is_only_fetched_once(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    robots_requests = 0

    def on_request(request: httpx.Request) -> None:
        nonlocal robots_requests
        if request.url.path == "/robots.txt":
            robots_requests += 1

    handler = _make_handler(robots_text, work_html, on_request=on_request)
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    await adapter.work(WORK_ID)
    await adapter.work("1234567890123456789")

    assert robots_requests == 1


# --- caching ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_call_is_served_from_cache(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    work_requests = 0

    def on_request(request: httpx.Request) -> None:
        nonlocal work_requests
        if request.url.path.startswith("/works/"):
            work_requests += 1

    handler = _make_handler(robots_text, work_html, on_request=on_request)
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    await adapter.work(WORK_ID)
    await adapter.work(WORK_ID)

    assert work_requests == 1


# --- rate limiting & concurrency -------------------------------------------


def test_default_rate_is_one_request_per_second():
    adapter = KakuyomuAdapter()
    assert adapter._bucket.rate == pytest.approx(1.0)
    assert adapter._bucket.capacity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_rate_limit_is_enforced_between_requests(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    handler = _make_handler(robots_text, work_html)

    bucket = TokenBucket(rate=5, capacity=1)
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        bucket=bucket,
    )

    start = time.monotonic()
    await adapter.work(WORK_ID)  # different ids so neither hits the cache
    await adapter.work("1234567890123456789")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15  # ~1/5s minimum gap, generous margin


@pytest.mark.asyncio
async def test_concurrency_is_limited_to_one(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()  # protects the counters themselves, not the adapter

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots_text)
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return httpx.Response(200, text=work_html)

    # very fast bucket so only the concurrency lock gates overlap
    bucket = TokenBucket(rate=1000, capacity=1)
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        bucket=bucket,
    )

    await asyncio.gather(
        adapter.work(WORK_ID),
        adapter.work("1234567890123456789"),
    )

    assert max_in_flight == 1


# --- User-Agent --------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_send_expected_user_agent(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    user_agents: list[str] = []

    def on_request(request: httpx.Request) -> None:
        user_agents.append(request.headers.get("user-agent", ""))

    handler = _make_handler(robots_text, work_html, on_request=on_request)
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    await adapter.work(WORK_ID)

    assert user_agents  # at least robots + work requests recorded
    assert all(ua == "triptych/0.1 (personal tracker)" for ua in user_agents)
