"""Tests for backend.adapters.narou and the `/api/narou` router.

All network access is stubbed via httpx.MockTransport, replayed from fixtures
recorded from the real なろう小説API (see fixtures/narou_*.json). No network
access is required to run this file.

Fixtures (recorded 2026-10-13 with the request parameters the adapter sends):
- narou_search_slime.json   「転生したらスライムだった件」 title=1 / type=re / order=hyoka
- narou_novel_n4830bu_of.json / narou_novel_n6316bn.json   of=t-n-w-ga-gl-e-nt
- narou_search_short.json   two short stories (noveltype=2)
- narou_novel_n4830bu.json / narou_search_honzuki.json   older, full-field payloads
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import errors
from backend.adapters import narou
from backend.api import narou as narou_api
from backend.cache import TTLCache
from backend.dto import SourceUnavailable, WebNovelDTO
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fast_rate_limiter(monkeypatch):
    """Replaces the module's 1 req/s bucket with a fast one for test speed."""
    monkeypatch.setattr(narou, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))


@pytest.fixture(autouse=True)
def clean_adapter_errors():
    errors.last_adapter_errors.clear()
    yield
    errors.last_adapter_errors.clear()


# --- ncode validation ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("n4830bu", "n4830bu"),
        ("N4830BU", "n4830bu"),
        ("N4830Bu", "n4830bu"),
        ("n0001a", "n0001a"),
    ],
)
def test_validate_ncode_accepts_well_formed(raw, expected):
    assert narou.validate_ncode(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "4830bu",  # missing leading n
        "n483bu",  # only 3 digits
        "n4830buu",  # 3 trailing letters
        "n4830b1",  # digit instead of letter
        "",
        "n48301",  # no trailing letters at all
    ],
)
def test_validate_ncode_rejects_malformed(raw):
    with pytest.raises(ValueError):
        narou.validate_ncode(raw)


# --- normalization -----------------------------------------------------------


def test_normalize_novel_n4830bu_is_completed():
    # 本好きの下剋上 (n4830bu) finished serializing: end=0 means completed.
    raw = _load("narou_novel_n4830bu_of.json")
    dto = narou._normalize_novel(raw[1])

    assert isinstance(dto, WebNovelDTO)
    assert dto.source == "narou"
    assert dto.externalId == "n4830bu"
    assert dto.title.startswith("本好きの下剋上")
    assert dto.author == "香月　美夜"
    assert dto.episodeCount == 677
    assert dto.lastUpdate == "2017-03-12"
    assert dto.completed is True
    assert dto.isShortStory is False
    assert dto.url == "https://ncode.syosetu.com/n4830bu/"


def test_normalize_novel_n6316bn_from_fixture():
    raw = _load("narou_novel_n6316bn.json")
    dto = narou._normalize_novel(raw[1])

    assert dto.externalId == "n6316bn"
    assert dto.title == "転生したらスライムだった件"
    assert dto.author == "伏瀬"
    assert dto.episodeCount == 304
    assert dto.lastUpdate == "2020-07-04"
    assert dto.completed is True
    assert dto.isShortStory is False


def test_normalize_novel_accepts_full_payload_field_names():
    # Without `of=` the API spells the type field `novel_type`.
    raw = _load("narou_novel_n4830bu.json")
    dto = narou._normalize_novel(raw[1])

    assert dto.externalId == "n4830bu"
    assert dto.episodeCount == 677
    assert dto.lastUpdate == "2017-03-12"
    assert dto.completed is True
    assert dto.isShortStory is False


def test_normalize_novel_end_one_is_still_serializing():
    record = dict(_load("narou_novel_n4830bu_of.json")[1])
    record["end"] = 1
    assert narou._normalize_novel(record).completed is False


def test_normalize_novel_missing_end_is_not_reported_completed():
    record = dict(_load("narou_novel_n4830bu_of.json")[1])
    del record["end"]
    assert narou._normalize_novel(record).completed is False


def test_normalize_novel_short_story():
    raw = _load("narou_search_short.json")
    dto = narou._normalize_novel(raw[1])

    assert dto.isShortStory is True
    assert dto.episodeCount == 1
    # end=0 covers "completed serial or short story": a short story is complete.
    assert dto.completed is True
    assert dto.lastUpdate == "2026-04-30"


def test_normalize_novel_short_story_via_novel_type_key():
    record = dict(_load("narou_novel_n4830bu.json")[1])
    record["novel_type"] = 2
    assert narou._normalize_novel(record).isShortStory is True


def test_normalize_search_puts_the_real_work_first():
    raw = _load("narou_search_slime.json")
    results = narou._normalize_search(raw)

    assert [r.externalId for r in results][0] == "n6316bn"
    first = results[0]
    assert first.title == "転生したらスライムだった件"
    assert first.subtitle == "伏瀬"
    assert first.source == "narou"
    assert first.mediaType == "web-novel"
    assert first.url == "https://ncode.syosetu.com/n6316bn/"
    assert first.direct is False
    # Spin-offs by other authors come after, with their own author as subtitle.
    assert len(results) == len(raw) - 1 == 4
    assert results[1].subtitle == "キューブック"


def test_normalize_search_from_older_fixture():
    raw = _load("narou_search_honzuki.json")
    results = narou._normalize_search(raw)

    assert len(results) == len(raw) - 1
    assert len(results) > 0
    for item in results:
        assert item.source == "narou"
        assert item.mediaType == "web-novel"
        assert item.externalId == item.externalId.lower()
        assert item.url == f"https://ncode.syosetu.com/{item.externalId}/"


# --- end-to-end via mocked HTTP transport -----------------------------------


class _Recorder:
    """MockTransport handler that replays `payload` and records each request."""

    def __init__(self, payload: bytes, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status_code, content=self.payload)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _recorder(fixture: str, status_code: int = 200) -> _Recorder:
    return _Recorder((FIXTURES / fixture).read_bytes(), status_code)


@pytest.mark.asyncio
async def test_novel_end_to_end_uses_mock_transport(tmp_path):
    rec = _recorder("narou_novel_n4830bu_of.json")
    client = rec.client()
    cache = TTLCache(db_path=tmp_path / "cache.db")

    try:
        detail = await narou.novel("N4830BU", client=client, cache=cache)
    finally:
        await client.aclose()

    assert detail.externalId == "n4830bu"
    assert detail.episodeCount == 677
    assert detail.completed is True

    # Second call should be served from cache (mock transport not reused).
    cached_detail = await narou.novel("N4830BU", client=None, cache=cache)
    assert cached_detail == detail
    assert len(rec.requests) == 1


@pytest.mark.asyncio
async def test_novel_request_asks_only_for_the_needed_fields():
    rec = _recorder("narou_novel_n6316bn.json")
    client = rec.client()
    try:
        await narou.novel("n6316bn", client=client, cache=None)
    finally:
        await client.aclose()

    request = rec.requests[0]
    params = dict(request.url.params)
    assert params == {"ncode": "n6316bn", "of": "t-n-w-ga-gl-e-nt", "out": "json"}
    assert request.headers["user-agent"] == "triptych/0.1 (personal tracker)"
    assert request.headers["accept-encoding"] == "gzip"


@pytest.mark.asyncio
async def test_novel_refresh_bypasses_cache(tmp_path):
    rec = _recorder("narou_novel_n6316bn.json")
    client = rec.client()
    cache = TTLCache(db_path=tmp_path / "cache.db")
    try:
        await narou.novel("n6316bn", client=client, cache=cache)
        await narou.novel("n6316bn", client=client, cache=cache)
        assert len(rec.requests) == 1
        await narou.novel("n6316bn", client=client, cache=cache, refresh=True)
        assert len(rec.requests) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_novel_ignores_cache_entry_with_old_shape(tmp_path):
    rec = _recorder("narou_novel_n6316bn.json")
    client = rec.client()
    cache = TTLCache(db_path=tmp_path / "cache.db")
    # A NarouNovelDetail-shaped entry (pre-WebNovelDTO) under the current key.
    cache.set(
        "narou:novel:v2:n6316bn",
        {"ncode": "n6316bn", "generalAllNo": 304, "completed": False},
        3600,
    )
    try:
        dto = await narou.novel("n6316bn", client=client, cache=cache)
    finally:
        await client.aclose()
    assert dto.externalId == "n6316bn"
    assert len(rec.requests) == 1


@pytest.mark.asyncio
async def test_novel_raises_source_unavailable_when_not_found():
    client = _Recorder(json.dumps([{"allcount": 0}]).encode("utf-8")).client()
    try:
        with pytest.raises(SourceUnavailable):
            await narou.novel("n9999zz", client=client, cache=None)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_novel_raises_source_unavailable_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceUnavailable):
            await narou.novel("n4830bu", client=client, cache=None)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_novel_rejects_invalid_ncode_without_network():
    with pytest.raises(ValueError):
        await narou.novel("not-an-ncode", client=None, cache=None)


@pytest.mark.asyncio
async def test_search_end_to_end_ranks_the_real_work_first(tmp_path):
    rec = _recorder("narou_search_slime.json")
    client = rec.client()
    cache = TTLCache(db_path=tmp_path / "cache.db")

    try:
        results = await narou.search(
            "転生したらスライムだった件", client=client, cache=cache
        )
    finally:
        await client.aclose()

    assert [r.externalId for r in results][0] == "n6316bn"
    assert all(r.source == "narou" for r in results)


@pytest.mark.asyncio
async def test_search_sends_title_serial_hyoka_parameters():
    rec = _recorder("narou_search_slime.json")
    client = rec.client()
    try:
        await narou.search("転生したらスライムだった件", client=client, cache=None)
    finally:
        await client.aclose()

    request = rec.requests[0]
    assert dict(request.url.params) == {
        "word": "転生したらスライムだった件",
        "title": "1",  # title only, not the synopsis
        "type": "re",  # serials only
        "order": "hyoka",  # best rated first
        "lim": "20",
        "of": "t-n-w-ga-gl-e-nt",
        "out": "json",
    }
    assert request.headers["user-agent"] == "triptych/0.1 (personal tracker)"


@pytest.mark.asyncio
async def test_search_is_cached_and_refresh_bypasses_the_cache(tmp_path):
    rec = _recorder("narou_search_slime.json")
    client = rec.client()
    cache = TTLCache(db_path=tmp_path / "cache.db")
    try:
        first = await narou.search("スライム", client=client, cache=cache)
        second = await narou.search("スライム", client=client, cache=cache)
        assert second == first
        assert len(rec.requests) == 1
        await narou.search("スライム", client=client, cache=cache, refresh=True)
        assert len(rec.requests) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_with_no_hits_returns_empty_list():
    client = _Recorder(json.dumps([{"allcount": 0}]).encode("utf-8")).client()
    try:
        assert await narou.search("該当なし", client=client, cache=None) == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_raises_source_unavailable_on_server_error():
    client = _Recorder(b"oops", status_code=503).client()
    try:
        with pytest.raises(SourceUnavailable):
            await narou.search("何か", client=client, cache=None)
    finally:
        await client.aclose()


# --- /api/narou router --------------------------------------------------------


@pytest.fixture()
def api_client():
    return TestClient(app_module.app)


def _web_novel() -> WebNovelDTO:
    return WebNovelDTO(
        source="narou",
        externalId="n4830bu",
        title="本好きの下剋上",
        author="香月　美夜",
        url="https://ncode.syosetu.com/n4830bu/",
        episodeCount=677,
        completed=True,
        lastUpdate="2017-03-12",
    )


def test_router_returns_web_novel_dto(monkeypatch, api_client):
    seen = {}

    async def fake_novel(ncode, *, client=None, cache=None, refresh=False):
        seen["ncode"] = ncode
        seen["refresh"] = refresh
        return _web_novel()

    monkeypatch.setattr(narou_api.narou, "novel", fake_novel)

    response = api_client.get("/api/narou/novel/n4830bu")

    assert response.status_code == 200
    assert response.json() == {
        "source": "narou",
        "externalId": "n4830bu",
        "title": "本好きの下剋上",
        "author": "香月　美夜",
        "url": "https://ncode.syosetu.com/n4830bu/",
        "episodeCount": 677,
        "completed": True,
        "isShortStory": False,
        "lastUpdate": "2017-03-12",
    }
    assert seen == {"ncode": "n4830bu", "refresh": False}


def test_router_passes_refresh_flag(monkeypatch, api_client):
    seen = {}

    async def fake_novel(ncode, *, client=None, cache=None, refresh=False):
        seen["refresh"] = refresh
        return _web_novel()

    monkeypatch.setattr(narou_api.narou, "novel", fake_novel)

    assert api_client.get("/api/narou/novel/n4830bu?refresh=1").status_code == 200
    assert seen["refresh"] is True


def test_router_invalid_ncode_is_400(api_client):
    response = api_client.get("/api/narou/novel/not-an-ncode")
    assert response.status_code == 400


def test_router_records_source_failure_for_health(monkeypatch, api_client):
    async def fake_novel(ncode, *, client=None, cache=None, refresh=False):
        raise SourceUnavailable("narou", "timeout")

    monkeypatch.setattr(narou_api.narou, "novel", fake_novel)

    response = api_client.get("/api/narou/novel/n4830bu")

    assert response.status_code == 502
    health = api_client.get("/api/health").json()
    assert health["errors"]["narou"]["message"] == "timeout"
    assert health["errors"]["narou"]["source"] == "narou"
