"""Tests for backend.adapters.openbd.

All network access is stubbed via httpx.MockTransport, replayed from fixtures
recorded from the real openBD API (see fixtures/openbd_*.json). No network
access is required to run this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.adapters import openbd
from backend.cache import TTLCache
from backend.dto import SourceUnavailable
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fast_rate_limiter(monkeypatch):
    monkeypatch.setattr(openbd, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))


# --- ISBN-13 checksum validation --------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9784864723428", "9784864723428"),
        ("978-4-86472-342-8", "9784864723428"),
        ("9784867940228", "9784867940228"),
    ],
)
def test_validate_isbn13_accepts_valid(raw, expected):
    assert openbd.validate_isbn13(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "1234567890123",  # bad checksum
        "978486472342",  # 12 digits
        "97848647234288",  # 14 digits
        "978486472342X",  # non-digit
        "",
    ],
)
def test_validate_isbn13_rejects_invalid(raw):
    with pytest.raises(ValueError):
        openbd.validate_isbn13(raw)


# --- normalization -----------------------------------------------------------


def test_normalize_book_from_fixture_without_cover():
    raw = _load("openbd_9784864723428.json")
    detail = openbd._normalize_book(raw[0])

    assert detail.isbn == "9784864723428"
    assert "本好きの下剋上" in detail.title
    assert detail.publisher == "TOブックス"
    assert detail.pubdate == "2015-02-01"
    assert detail.cover is None
    assert detail.url == "https://ndlsearch.ndl.go.jp/books/9784864723428"


def test_normalize_book_pubdate_full_date():
    raw = _load("openbd_9784864723428.json")
    record = json.loads(json.dumps(raw[0]))
    record["summary"]["pubdate"] = "20150210"
    detail = openbd._normalize_book(record)
    assert detail.pubdate == "2015-02-10"


def test_normalize_book_pubdate_year_only():
    raw = _load("openbd_9784864723428.json")
    record = json.loads(json.dumps(raw[0]))
    record["summary"]["pubdate"] = "2015"
    detail = openbd._normalize_book(record)
    assert detail.pubdate == "2015-01-01"


def test_normalize_book_cover_present():
    raw = _load("openbd_9784864723428.json")
    record = json.loads(json.dumps(raw[0]))
    record["summary"]["cover"] = "https://cover.example/9784864723428.jpg"
    detail = openbd._normalize_book(record)
    assert detail.cover == "https://cover.example/9784864723428.jpg"


# --- end-to-end via mocked HTTP transport -----------------------------------


def _mock_client(json_bytes: bytes, status_code: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=json_bytes)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_book_end_to_end_uses_mock_transport(tmp_path):
    fixture_bytes = (FIXTURES / "openbd_9784864723428.json").read_bytes()
    client = _mock_client(fixture_bytes)
    cache = TTLCache(db_path=tmp_path / "cache.db")

    try:
        detail = await openbd.book("9784864723428", client=client, cache=cache)
    finally:
        await client.aclose()

    assert detail.isbn == "9784864723428"

    cached_detail = await openbd.book("9784864723428", client=None, cache=cache)
    assert cached_detail == detail


@pytest.mark.asyncio
async def test_book_raises_source_unavailable_when_not_found():
    client = _mock_client(json.dumps([None]).encode("utf-8"))
    try:
        with pytest.raises(SourceUnavailable):
            await openbd.book("9784864723428", client=client, cache=None)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_book_rejects_invalid_isbn_without_network():
    with pytest.raises(ValueError):
        await openbd.book("not-an-isbn", client=None, cache=None)


@pytest.mark.asyncio
async def test_book_raises_source_unavailable_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceUnavailable):
            await openbd.book("9784864723428", client=client, cache=None)
    finally:
        await client.aclose()
