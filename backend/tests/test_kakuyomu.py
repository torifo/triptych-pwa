"""Tests for the kakuyomu scraping adapter and its router.

Network access is fully mocked via httpx.MockTransport against recorded
fixtures (see backend/tests/fixtures/kakuyomu_*); no live requests are made.
- kakuyomu_work_found.html is trimmed from a real page fetch of a well-known,
  book-published and anime-adapted work: 陰の実力者になりたくて！
  (https://kakuyomu.jp/works/1177354054886847987).
- kakuyomu_search_kage.html / kakuyomu_search_isekai.html are trimmed from the
  real `/search?q=...&order=popular` pages (2026-10-13): the first has 6 hits
  for the query above, the second is the full 20-work first page of 「異世界」
  where most works carry `hasPublication`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import errors
from backend.adapters.kakuyomu import KakuyomuAdapter, validate_work_id
from backend.api import kakuyomu as kakuyomu_api
from backend.cache import TTLCache
from backend.dto import SourceUnavailable, WebNovelDTO
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"
WORK_ID = "1177354054886847987"
OTHER_WORK_ID = "1234567890123456789"


@pytest.fixture(autouse=True)
def clean_adapter_errors():
    errors.last_adapter_errors.clear()
    yield
    errors.last_adapter_errors.clear()


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make_handler(
    robots_text: str,
    work_html: str = "<html></html>",
    work_status: int = 200,
    on_request=None,
    search_html: str = "<html></html>",
    search_status: int = 200,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if on_request is not None:
            on_request(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots_text)
        if request.url.path == "/search":
            return httpx.Response(search_status, text=search_html)
        assert request.url.path.startswith("/works/")
        return httpx.Response(work_status, text=work_html)

    return handler


def _make_adapter(
    tmp_path: Path,
    robots_text: str,
    work_html: str = "<html></html>",
    work_status: int = 200,
    search_html: str = "<html></html>",
    on_request=None,
    **kwargs,
) -> KakuyomuAdapter:
    handler = _make_handler(
        robots_text,
        work_html,
        work_status,
        on_request=on_request,
        search_html=search_html,
    )
    # Fast bucket by default so only tests about throttling wait on it.
    kwargs.setdefault("bucket", TokenBucket(rate=1000, capacity=1))
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


# --- work normalization ------------------------------------------------


@pytest.mark.asyncio
async def test_work_normalizes_found_page(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    adapter = _make_adapter(tmp_path, robots_text, work_html)

    dto = await adapter.work(WORK_ID)

    assert isinstance(dto, WebNovelDTO)
    assert dto.source == "kakuyomu"
    assert dto.externalId == WORK_ID
    assert dto.title == "陰の実力者になりたくて！"
    assert dto.author == "逢沢大介"
    assert dto.episodeCount == 130
    assert dto.url == f"https://kakuyomu.jp/works/{WORK_ID}"
    assert dto.completed is False  # serialStatus RUNNING
    assert dto.isShortStory is False
    assert dto.lastUpdate == "2019-01-02"


@pytest.mark.parametrize(
    "published_at,expected_jst_date",
    [
        ("2019-01-02T12:00:28Z", "2019-01-02"),  # 21:00 JST, same day
        ("2019-01-02T14:59:59Z", "2019-01-02"),  # 23:59:59 JST
        ("2019-01-02T15:00:00Z", "2019-01-03"),  # 00:00 JST: the next day
        ("2019-01-02T16:30:00Z", "2019-01-03"),  # 01:30 JST: the next day
    ],
)
@pytest.mark.asyncio
async def test_work_last_update_is_the_jst_calendar_date(
    tmp_path, published_at, expected_jst_date
):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html").replace(
        "2019-01-02T12:00:28Z", published_at
    )
    adapter = _make_adapter(tmp_path, robots_text, work_html)

    dto = await adapter.work(WORK_ID)

    assert dto.lastUpdate == expected_jst_date


@pytest.mark.asyncio
async def test_work_completed_when_serial_status_is_completed(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html").replace(
        '"serialStatus": "RUNNING"', '"serialStatus": "COMPLETED"'
    )
    adapter = _make_adapter(tmp_path, robots_text, work_html)

    dto = await adapter.work(WORK_ID)

    assert dto.completed is True


@pytest.mark.asyncio
async def test_work_without_last_episode_date_has_no_last_update(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html").replace(
        '"lastEpisodePublishedAt": "2019-01-02T12:00:28Z"',
        '"lastEpisodePublishedAt": null',
    )
    adapter = _make_adapter(tmp_path, robots_text, work_html)

    dto = await adapter.work(WORK_ID)

    assert dto.lastUpdate is None


@pytest.mark.asyncio
async def test_work_prefers_alternate_author_name(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html").replace(
        '"genre": "FANTASY"',
        '"alternateAuthorName": "代理投稿の著者", "genre": "FANTASY"',
    )
    adapter = _make_adapter(tmp_path, robots_text, work_html)

    dto = await adapter.work(WORK_ID)

    assert dto.author == "代理投稿の著者"


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


# --- search normalization ----------------------------------------------------


@pytest.mark.asyncio
async def test_search_normalizes_results_in_popularity_order(tmp_path):
    adapter = _make_adapter(
        tmp_path,
        _read_fixture("kakuyomu_robots.txt"),
        search_html=_read_fixture("kakuyomu_search_kage.html"),
    )

    results = await adapter.search("陰の実力者になりたくて")

    assert len(results) == 6
    first = results[0]
    assert first.source == "kakuyomu"
    assert first.externalId == WORK_ID
    assert first.mediaType == "web-novel"
    assert first.title == "陰の実力者になりたくて！"
    assert first.url == f"https://kakuyomu.jp/works/{WORK_ID}"
    assert first.cover is None
    assert first.direct is False
    assert first.subtitle == "逢沢大介"  # no publication: author only

    # The publisher-account copy shows the real author, not the account name.
    assert results[1].externalId == "16817139558493891332"
    assert results[1].subtitle == "逢沢大介"
    # Order is kept as the site returns it.
    assert [r.externalId for r in results][:3] == [
        WORK_ID,
        "16817139558493891332",
        "16818792439385430662",
    ]


@pytest.mark.asyncio
async def test_search_marks_published_works_and_strips_author_names(tmp_path):
    adapter = _make_adapter(
        tmp_path,
        _read_fixture("kakuyomu_robots.txt"),
        search_html=_read_fixture("kakuyomu_search_isekai.html"),
    )

    results = await adapter.search("異世界")

    assert len(results) == 20
    assert all(r.source == "kakuyomu" and r.mediaType == "web-novel" for r in results)
    assert all(r.url == f"https://kakuyomu.jp/works/{r.externalId}" for r in results)
    assert all(r.externalId.isdigit() for r in results)

    by_id = {r.externalId: r for r in results}
    # hasPublication -> 書籍化 appended after the author.
    assert by_id["1177354054891139802"].subtitle == "じゃがバター・書籍化"
    # A leading space in the account name is stripped.
    assert by_id["16817330666469551319"].subtitle == "次佐　駆人・書籍化"
    # No publication: author only, no marker.
    assert by_id["16818093093558400360"].subtitle == "藍敦"
    assert by_id["1177354054882961666"].subtitle == "小鳥屋エム"
    assert sum("書籍化" in (r.subtitle or "") for r in results) == 18


@pytest.mark.asyncio
async def test_search_request_uses_popular_order_and_user_agent(tmp_path):
    requests: list[httpx.Request] = []
    adapter = _make_adapter(
        tmp_path,
        _read_fixture("kakuyomu_robots.txt"),
        search_html=_read_fixture("kakuyomu_search_kage.html"),
        on_request=requests.append,
    )

    await adapter.search("陰の実力者になりたくて")

    search_requests = [r for r in requests if r.url.path == "/search"]
    assert len(search_requests) == 1
    assert dict(search_requests[0].url.params) == {
        "q": "陰の実力者になりたくて",
        "order": "popular",
    }
    assert all(
        r.headers["user-agent"] == "triptych/0.1 (personal tracker)" for r in requests
    )


@pytest.mark.asyncio
async def test_search_with_blank_query_returns_nothing_without_requests(tmp_path):
    requests: list[httpx.Request] = []
    adapter = _make_adapter(
        tmp_path, _read_fixture("kakuyomu_robots.txt"), on_request=requests.append
    )

    assert await adapter.search("   ") == []
    assert requests == []


@pytest.mark.asyncio
async def test_search_with_zero_hits_returns_empty_list(tmp_path):
    html = _read_fixture("kakuyomu_search_kage.html")
    # Keep the page structure but empty the node list.
    import json
    import re

    match = re.search(r'(<script id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)', html, re.S)
    data = json.loads(match.group(2))
    root = data["props"]["pageProps"]["__APOLLO_STATE__"]["ROOT_QUERY"]
    key = next(k for k in root if k.startswith("searchWorks("))
    root[key]["nodes"] = []
    empty_html = (
        html[: match.start(2)] + json.dumps(data, ensure_ascii=False) + html[match.end(2):]
    )
    adapter = _make_adapter(
        tmp_path, _read_fixture("kakuyomu_robots.txt"), search_html=empty_html
    )

    assert await adapter.search("該当なし") == []


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>no data here</body></html>",
        '<script id="__NEXT_DATA__" type="application/json">not json</script>',
        '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>',
        # Valid data blob, but no searchWorks(...) entry in the Apollo state.
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"__APOLLO_STATE__": {"ROOT_QUERY": {}}}}}</script>',
    ],
)
@pytest.mark.asyncio
async def test_search_unexpected_page_raises_source_unavailable(tmp_path, html):
    adapter = _make_adapter(
        tmp_path, _read_fixture("kakuyomu_robots.txt"), search_html=html
    )

    with pytest.raises(SourceUnavailable) as exc_info:
        await adapter.search("何か")

    assert exc_info.value.source == "kakuyomu"


@pytest.mark.asyncio
async def test_search_server_error_raises_source_unavailable(tmp_path):
    handler = _make_handler(
        _read_fixture("kakuyomu_robots.txt"), search_html="busy", search_status=503
    )
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    with pytest.raises(SourceUnavailable):
        await adapter.search("何か")


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
async def test_robots_disallow_blocks_search_without_fetching_it(tmp_path):
    requested_paths: list[str] = []
    adapter = _make_adapter(
        tmp_path,
        "User-agent: *\nDisallow: /search\n",
        search_html=_read_fixture("kakuyomu_search_kage.html"),
        work_html=_read_fixture("kakuyomu_work_found.html"),
        on_request=lambda request: requested_paths.append(request.url.path),
    )

    with pytest.raises(SourceUnavailable) as exc_info:
        await adapter.search("陰の実力者になりたくて")

    assert "/search" in exc_info.value.message
    assert "陰の実力者" not in exc_info.value.message  # the query is not echoed
    assert requested_paths == ["/robots.txt"]  # /search never requested

    # Paths are judged one by one: works are still allowed.
    dto = await adapter.work(WORK_ID)
    assert dto.externalId == WORK_ID


@pytest.mark.asyncio
async def test_robots_txt_is_only_fetched_once(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    robots_requests = 0

    def on_request(request: httpx.Request) -> None:
        nonlocal robots_requests
        if request.url.path == "/robots.txt":
            robots_requests += 1

    adapter = _make_adapter(
        tmp_path,
        robots_text,
        work_html,
        search_html=_read_fixture("kakuyomu_search_kage.html"),
        on_request=on_request,
    )

    await adapter.work(WORK_ID)
    await adapter.work(OTHER_WORK_ID)
    await adapter.search("陰の実力者になりたくて")

    assert robots_requests == 1


@pytest.mark.asyncio
async def test_failed_robots_fetch_is_retried_on_the_next_call(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    robots_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal robots_calls
        if request.url.path == "/robots.txt":
            robots_calls += 1
            if robots_calls == 1:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, text=robots_text)
        return httpx.Response(200, text=work_html)

    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    with pytest.raises(SourceUnavailable):
        await adapter.work(WORK_ID)
    # A transient robots.txt failure must not lock the adapter out for good.
    dto = await adapter.work(WORK_ID)

    assert dto.externalId == WORK_ID
    assert robots_calls == 2


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

    adapter = _make_adapter(tmp_path, robots_text, work_html, on_request=on_request)

    await adapter.work(WORK_ID)
    await adapter.work(WORK_ID)

    assert work_requests == 1


@pytest.mark.asyncio
async def test_work_refresh_bypasses_the_cache(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    work_requests = 0

    def on_request(request: httpx.Request) -> None:
        nonlocal work_requests
        if request.url.path.startswith("/works/"):
            work_requests += 1

    adapter = _make_adapter(tmp_path, robots_text, work_html, on_request=on_request)

    await adapter.work(WORK_ID)
    await adapter.work(WORK_ID, refresh=True)

    assert work_requests == 2


@pytest.mark.asyncio
async def test_work_ignores_cache_entry_with_old_shape(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    adapter = _make_adapter(tmp_path, robots_text, work_html)
    # A KakuyomuWorkDTO-shaped entry (pre-WebNovelDTO) under the current key.
    adapter._cache.set(
        f"kakuyomu:work:v2:{WORK_ID}",
        {"externalId": WORK_ID, "title": "x", "releases": []},
        3600,
    )

    dto = await adapter.work(WORK_ID)

    assert dto.episodeCount == 130


@pytest.mark.asyncio
async def test_search_is_cached_and_refresh_bypasses_the_cache(tmp_path):
    search_requests = 0

    def on_request(request: httpx.Request) -> None:
        nonlocal search_requests
        if request.url.path == "/search":
            search_requests += 1

    adapter = _make_adapter(
        tmp_path,
        _read_fixture("kakuyomu_robots.txt"),
        search_html=_read_fixture("kakuyomu_search_kage.html"),
        on_request=on_request,
    )

    first = await adapter.search("陰の実力者になりたくて")
    second = await adapter.search("陰の実力者になりたくて")
    assert second == first
    assert search_requests == 1

    await adapter.search("陰の実力者になりたくて", refresh=True)
    assert search_requests == 2


# --- rate limiting & concurrency -------------------------------------------


def test_default_rate_is_one_request_per_second():
    adapter = KakuyomuAdapter(cache=TTLCache(db_path=":memory:"))
    assert adapter._bucket.rate == pytest.approx(1.0)
    assert adapter._bucket.capacity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_rate_limit_is_enforced_between_requests(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")

    bucket = TokenBucket(rate=5, capacity=1)
    adapter = _make_adapter(tmp_path, robots_text, work_html, bucket=bucket)

    start = time.monotonic()
    await adapter.work(WORK_ID)  # different ids so neither hits the cache
    await adapter.work(OTHER_WORK_ID)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15  # ~1/5s minimum gap, generous margin


@pytest.mark.asyncio
async def test_rate_limit_is_shared_between_work_and_search(tmp_path):
    bucket = TokenBucket(rate=5, capacity=1)
    adapter = _make_adapter(
        tmp_path,
        _read_fixture("kakuyomu_robots.txt"),
        _read_fixture("kakuyomu_work_found.html"),
        search_html=_read_fixture("kakuyomu_search_kage.html"),
        bucket=bucket,
    )

    start = time.monotonic()
    await adapter.search("陰の実力者になりたくて")
    await adapter.work(WORK_ID)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15


@pytest.mark.asyncio
async def test_concurrency_is_limited_to_one(tmp_path):
    robots_text = _read_fixture("kakuyomu_robots.txt")
    work_html = _read_fixture("kakuyomu_work_found.html")
    search_html = _read_fixture("kakuyomu_search_kage.html")

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
        if request.url.path == "/search":
            return httpx.Response(200, text=search_html)
        return httpx.Response(200, text=work_html)

    # very fast bucket so only the concurrency lock gates overlap
    bucket = TokenBucket(rate=1000, capacity=1)
    adapter = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        bucket=bucket,
    )

    # Work and search requests share the one lock.
    await asyncio.gather(
        adapter.work(WORK_ID),
        adapter.work(OTHER_WORK_ID),
        adapter.search("陰の実力者になりたくて"),
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

    adapter = _make_adapter(tmp_path, robots_text, work_html, on_request=on_request)

    await adapter.work(WORK_ID)

    assert user_agents  # at least robots + work requests recorded
    assert all(ua == "triptych/0.1 (personal tracker)" for ua in user_agents)


# --- /api/kakuyomu router ----------------------------------------------------


class _FakeAdapter:
    def __init__(self, error: Optional[Exception] = None):
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def work(self, work_id: str, refresh: bool = False) -> WebNovelDTO:
        self.calls.append((work_id, refresh))
        if self.error is not None:
            raise self.error
        return WebNovelDTO(
            source="kakuyomu",
            externalId=work_id,
            title="陰の実力者になりたくて！",
            author="逢沢大介",
            url=f"https://kakuyomu.jp/works/{work_id}",
            episodeCount=130,
            completed=False,
            lastUpdate="2019-01-02",
        )


@pytest.fixture()
def api_client():
    return TestClient(app_module.app)


def test_router_returns_web_novel_dto(monkeypatch, api_client):
    fake = _FakeAdapter()
    monkeypatch.setattr(kakuyomu_api, "get_adapter", lambda: fake)

    response = api_client.get(f"/api/kakuyomu/work/{WORK_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "source": "kakuyomu",
        "externalId": WORK_ID,
        "title": "陰の実力者になりたくて！",
        "author": "逢沢大介",
        "url": f"https://kakuyomu.jp/works/{WORK_ID}",
        "episodeCount": 130,
        "completed": False,
        "isShortStory": False,
        "lastUpdate": "2019-01-02",
    }
    assert fake.calls == [(WORK_ID, False)]


def test_router_passes_refresh_flag(monkeypatch, api_client):
    fake = _FakeAdapter()
    monkeypatch.setattr(kakuyomu_api, "get_adapter", lambda: fake)

    assert api_client.get(f"/api/kakuyomu/work/{WORK_ID}?refresh=1").status_code == 200
    assert fake.calls == [(WORK_ID, True)]


def test_router_invalid_id_is_400(monkeypatch, tmp_path, api_client):
    requests: list[httpx.Request] = []
    # A real adapter (validates before any request) on a private cache.
    adapter = _make_adapter(
        tmp_path, _read_fixture("kakuyomu_robots.txt"), on_request=requests.append
    )
    monkeypatch.setattr(kakuyomu_api, "get_adapter", lambda: adapter)

    assert api_client.get("/api/kakuyomu/work/not-a-number").status_code == 400
    assert requests == []


def test_router_records_source_failure_for_health(monkeypatch, api_client):
    fake = _FakeAdapter(error=SourceUnavailable("kakuyomu", "work 1 not found"))
    monkeypatch.setattr(kakuyomu_api, "get_adapter", lambda: fake)

    response = api_client.get("/api/kakuyomu/work/1")

    assert response.status_code == 502
    health = api_client.get("/api/health").json()
    assert health["errors"]["kakuyomu"]["message"] == "work 1 not found"
