"""Tests for backend.adapters.ndl.

All network access is stubbed via httpx.MockTransport, replayed from
fixtures/ndl_search_honzuki.xml (trimmed from a real NDL Search OpenSearch
response) and fixtures/openbd_*.json for the follow-up detail calls. No
network access is required to run this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.adapters import ndl, openbd
from backend.cache import TTLCache
from backend.dto import SourceUnavailable
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"
NDL_XML = (FIXTURES / "ndl_search_honzuki.xml").read_bytes()
OPENBD_9784864723428 = (FIXTURES / "openbd_9784864723428.json").read_bytes()


@pytest.fixture(autouse=True)
def fast_rate_limiters(monkeypatch):
    monkeypatch.setattr(ndl, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))
    monkeypatch.setattr(openbd, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))


# --- pure XML parsing --------------------------------------------------------


def test_parse_candidates_extracts_isbn_and_volume():
    candidates = ndl.parse_candidates(NDL_XML)
    assert len(candidates) > 0

    with_isbn = [c for c in candidates if c.isbn]
    assert with_isbn, "fixture should contain at least one ISBN-bearing item"
    sample = next(c for c in with_isbn if c.isbn == "9784864723428")
    assert sample.issued == "2015-02-01"


def test_parse_candidates_skips_items_without_title():
    xml = b"""<rss><channel>
      <item><dcndl:volume xmlns:dcndl="http://ndl.go.jp/dcndl/terms/">1</dcndl:volume></item>
    </channel></rss>"""
    assert ndl.parse_candidates(xml) == []


def test_parse_candidates_items_without_isbn_have_none():
    candidates = ndl.parse_candidates(NDL_XML)
    without_isbn = [c for c in candidates if c.isbn is None]
    assert without_isbn, "fixture should also contain non-book items (no ISBN)"


def test_parse_candidates_raises_source_unavailable_on_malformed_xml():
    with pytest.raises(SourceUnavailable):
        ndl.parse_candidates(b"not xml at all <<<")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2018.11", "2018-11-01"),
        ("2018.1-2020.1", "2018-01-01"),
        ("2015.2.10", "2015-02-10"),
        ("2019.-", "2019-01-01"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_issued(raw, expected):
    assert ndl._normalize_issued(raw) == expected


# --- end-to-end via mocked HTTP transport (NDL + openBD) --------------------


def _mock_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "ndlsearch.ndl.go.jp/api/opensearch" in str(request.url):
            return httpx.Response(200, content=NDL_XML)
        if "api.openbd.jp" in str(request.url):
            isbn = request.url.params.get("isbn")
            if isbn == "9784864723428":
                return httpx.Response(200, content=OPENBD_9784864723428)
            return httpx.Response(200, content=json.dumps([None]).encode("utf-8"))
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_series_end_to_end_details_via_openbd(tmp_path):
    client = _mock_client()
    cache = TTLCache(db_path=tmp_path / "cache.db")

    try:
        releases = await ndl.series("本好きの下剋上", client=client, cache=cache)
    finally:
        await client.aclose()

    assert len(releases) > 0
    assert all(r.source == "ndl" for r in releases)
    assert all(r.kind == "volume" for r in releases)

    match = next(r for r in releases if r.externalId == "9784864723428")
    assert match.date == "2015-02-01"
    assert match.label  # non-empty: either NDL volume label or openBD title


@pytest.mark.asyncio
async def test_series_uses_cached_xml_on_second_call(tmp_path):
    calls = {"ndl": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "ndlsearch.ndl.go.jp/api/opensearch" in str(request.url):
            calls["ndl"] += 1
            return httpx.Response(200, content=NDL_XML)
        isbn = request.url.params.get("isbn")
        if isbn == "9784864723428":
            return httpx.Response(200, content=OPENBD_9784864723428)
        return httpx.Response(200, content=json.dumps([None]).encode("utf-8"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = TTLCache(db_path=tmp_path / "cache.db")

    try:
        await ndl.series("本好きの下剋上", client=client, cache=cache)
        await ndl.series("本好きの下剋上", client=client, cache=cache)
    finally:
        await client.aclose()

    assert calls["ndl"] == 1


@pytest.mark.asyncio
async def test_series_raises_source_unavailable_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceUnavailable):
            await ndl.series("本好きの下剋上", client=client, cache=None)
    finally:
        await client.aclose()
