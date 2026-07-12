"""Tests for backend.adapters.narou.

All network access is stubbed via httpx.MockTransport, replayed from fixtures
recorded from the real なろう小説API (see fixtures/narou_*.json). No network
access is required to run this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.adapters import narou
from backend.cache import TTLCache
from backend.dto import SourceUnavailable
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fast_rate_limiter(monkeypatch):
    """Replaces the module's 1 req/s bucket with a fast one for test speed."""
    monkeypatch.setattr(narou, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))


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


def test_normalize_novel_from_fixture():
    raw = _load("narou_novel_n4830bu.json")
    detail = narou._normalize_novel(raw[1])

    assert detail.ncode == "n4830bu"
    assert detail.title.startswith("本好きの下剋上")
    assert detail.author == "香月　美夜"
    assert detail.generalAllNo == 677
    assert detail.generalLastup == "2017-03-12"
    assert detail.completed is False
    assert detail.url == "https://ncode.syosetu.com/n4830bu/"


def test_normalize_novel_completed_flag_true():
    raw = _load("narou_novel_n4830bu.json")
    record = dict(raw[1])
    record["end"] = 1
    detail = narou._normalize_novel(record)
    assert detail.completed is True


def test_normalize_search_from_fixture():
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


def _mock_client(json_bytes: bytes, status_code: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=json_bytes)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_novel_end_to_end_uses_mock_transport(tmp_path):
    fixture_bytes = (FIXTURES / "narou_novel_n4830bu.json").read_bytes()
    client = _mock_client(fixture_bytes)
    cache = TTLCache(db_path=tmp_path / "cache.db")

    try:
        detail = await narou.novel("N4830BU", client=client, cache=cache)
    finally:
        await client.aclose()

    assert detail.ncode == "n4830bu"
    assert detail.generalAllNo == 677

    # Second call should be served from cache (mock transport not reused).
    cached_detail = await narou.novel("N4830BU", client=None, cache=cache)
    assert cached_detail == detail


@pytest.mark.asyncio
async def test_novel_raises_source_unavailable_when_not_found():
    client = _mock_client(json.dumps([{"allcount": 0}]).encode("utf-8"))
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
async def test_search_end_to_end_uses_mock_transport(tmp_path):
    fixture_bytes = (FIXTURES / "narou_search_honzuki.json").read_bytes()
    client = _mock_client(fixture_bytes)
    cache = TTLCache(db_path=tmp_path / "cache.db")

    try:
        results = await narou.search("本好きの下剋上", client=client, cache=cache)
    finally:
        await client.aclose()

    assert len(results) > 0
    assert all(r.source == "narou" for r in results)
