"""Tests for backend.adapters.openbd and the `/api/openbd` route.

All network access is stubbed via httpx.MockTransport, replayed from fixtures
recorded from the real openBD API (see fixtures/openbd_*.json). No network
access is required to run this file.

fixtures/openbd_batch_new_volumes.json is one real `/v1/get?isbn=a,b,...`
response (2026-10-13) for `BATCH_ISBNS`, in that order: most are null (not
in openBD) and 9784867949269 has an empty pubdate.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import errors
from backend.adapters import openbd
from backend.api import openbd as openbd_api
from backend.cache import TTLCache
from backend.dto import SourceUnavailable
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"

BATCH_ISBNS = [
    "9784867949443",
    "9784867949467",
    "9784868540700",
    "9784867949450",
    "9784867949269",
    "9784867949474",
    "9784868540366",
    "9784065444528",
    "9784065444443",
    "9784867169995",
    "9784825000360",
    "9784867169629",
    "9784065437544",
]


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


BATCH_BY_ISBN = dict(zip(BATCH_ISBNS, _load("openbd_batch_new_volumes.json")))


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


# --- pubdate precision -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("20261026", ("2026-10-26", "day")),
        ("202610", ("2026-10-01", "month")),
        ("2026", ("2026-01-01", "year")),
        ("2026-10", ("2026-10-01", "month")),
        ("20261000", ("2026-10-01", "month")),
        ("20261332", None),
        ("", None),
        (None, None),
        ("未定", None),
    ],
)
def test_parse_pubdate_keeps_precision(raw, expected):
    assert openbd.parse_pubdate(raw) == expected


# --- normalization -----------------------------------------------------------


def test_normalize_book_from_fixture_without_cover():
    raw = _load("openbd_9784864723428.json")
    detail = openbd._normalize_book(raw[0])

    assert detail.isbn == "9784864723428"
    assert "本好きの下剋上" in detail.title
    assert detail.publisher == "TOブックス"
    assert detail.pubdate == "2015-02-01"
    assert detail.pubdatePrecision == "month"
    assert detail.cover is None
    assert detail.url == "https://ndlsearch.ndl.go.jp/books/9784864723428"


def test_normalize_book_pubdate_full_date():
    raw = _load("openbd_9784864723428.json")
    record = json.loads(json.dumps(raw[0]))
    record["summary"]["pubdate"] = "20150210"
    detail = openbd._normalize_book(record)
    assert detail.pubdate == "2015-02-10"
    assert detail.pubdatePrecision == "day"


def test_normalize_book_pubdate_year_only():
    raw = _load("openbd_9784864723428.json")
    record = json.loads(json.dumps(raw[0]))
    record["summary"]["pubdate"] = "2015"
    detail = openbd._normalize_book(record)
    assert detail.pubdate == "2015-01-01"
    assert detail.pubdatePrecision == "year"


def test_normalize_book_empty_pubdate_has_no_date():
    detail = openbd._normalize_book(BATCH_BY_ISBN["9784867949269"])
    assert detail.isbn == "9784867949269"
    assert detail.pubdate is None
    assert detail.pubdatePrecision is None


def test_normalize_book_cover_present():
    raw = _load("openbd_9784864723428.json")
    record = json.loads(json.dumps(raw[0]))
    record["summary"]["cover"] = "https://cover.example/9784864723428.jpg"
    detail = openbd._normalize_book(record)
    assert detail.cover == "https://cover.example/9784864723428.jpg"


# --- book(): end-to-end via mocked HTTP transport ----------------------------


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
    assert detail.pubdatePrecision == "month"

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


# --- books(): one batched request -------------------------------------------


def _batch_client(requests: list[list[str]], status_code: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        isbns = request.url.params["isbn"].split(",")
        requests.append(isbns)
        assert request.headers["user-agent"] == openbd.USER_AGENT
        return httpx.Response(status_code, json=[BATCH_BY_ISBN.get(i) for i in isbns])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_books_fetches_all_isbns_in_one_request(tmp_path):
    requests: list[list[str]] = []
    wanted = ["9784867949474", "978-4-86854-036-6", "9784867949269", "9784065437544"]
    async with _batch_client(requests) as client:
        found = await openbd.books(wanted, client=client, cache=None)

    assert requests == [
        ["9784867949474", "9784868540366", "9784867949269", "9784065437544"]
    ]
    assert found["9784868540366"] is None  # not in openBD
    assert found["9784867949269"].pubdate is None  # empty pubdate
    assert found["9784867949474"].pubdate == "2026-04-01"
    assert found["9784867949474"].pubdatePrecision == "month"
    assert found["9784065437544"].title.startswith("転生したらスライムだった件")


@pytest.mark.asyncio
async def test_books_caches_hits_and_misses(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    requests: list[list[str]] = []
    wanted = ["9784867949474", "9784868540366"]
    async with _batch_client(requests) as client:
        first = await openbd.books(wanted, client=client, cache=cache)
        second = await openbd.books(wanted, client=client, cache=cache)
        await openbd.books(wanted, client=client, cache=cache, refresh=True)

    assert first == second
    assert second["9784868540366"] is None
    assert len(requests) == 2  # the cached call made no request; refresh did


@pytest.mark.asyncio
async def test_books_with_nothing_pending_makes_no_request():
    found = await openbd.books([], client=None, cache=None)
    assert found == {}


@pytest.mark.asyncio
async def test_books_rejects_invalid_isbn():
    with pytest.raises(ValueError):
        await openbd.books(["9784867949474", "123"], client=None, cache=None)


@pytest.mark.asyncio
async def test_books_raises_source_unavailable_on_http_error():
    requests: list[list[str]] = []
    async with _batch_client(requests, status_code=503) as client:
        with pytest.raises(SourceUnavailable):
            await openbd.books(["9784867949474"], client=client, cache=None)


# --- /api/openbd route ---------------------------------------------------------


def _route_client(monkeypatch, tmp_path, fake_book) -> TestClient:
    monkeypatch.setattr(openbd_api, "_cache", TTLCache(db_path=tmp_path / "cache.db"))
    monkeypatch.setattr(openbd_api.openbd, "book", fake_book)
    app = FastAPI()
    app.include_router(openbd_api.router)
    return TestClient(app)


def test_route_passes_refresh(monkeypatch, tmp_path):
    seen = {}

    async def fake_book(isbn, *, client=None, cache=None, refresh=False):
        seen.update(isbn=isbn, refresh=refresh)
        return openbd._normalize_book(_load("openbd_9784864723428.json")[0])

    client = _route_client(monkeypatch, tmp_path, fake_book)
    response = client.get("/api/openbd/9784864723428", params={"refresh": "1"})

    assert response.status_code == 200
    assert response.json()["pubdatePrecision"] == "month"
    assert seen == {"isbn": "9784864723428", "refresh": True}


def test_route_records_adapter_error_before_502(monkeypatch, tmp_path):
    async def fake_book(isbn, *, client=None, cache=None, refresh=False):
        raise SourceUnavailable("openbd", "boom")

    monkeypatch.setattr(errors, "last_adapter_errors", {})
    client = _route_client(monkeypatch, tmp_path, fake_book)
    response = client.get("/api/openbd/9784864723428")

    assert response.status_code == 502
    assert errors.last_adapter_errors["openbd"].message == "boom"
