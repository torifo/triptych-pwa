"""Tests for the syoboi calendar adapter.

Network access is fully mocked via httpx.MockTransport against recorded
fixtures (see backend/tests/fixtures/syoboi_title_*.xml); no live requests
are made.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from backend.adapters.syoboi import SyoboiAdapter, validate_tid
from backend.cache import TTLCache
from backend.dto import SourceUnavailable
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _client_for(xml_text: str, status_code: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/db.php"
        assert request.url.params["Command"] == "TitleLookup"
        return httpx.Response(status_code, text=xml_text)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _make_adapter(xml_text: str, tmp_path: Path, **kwargs) -> SyoboiAdapter:
    return SyoboiAdapter(
        client=_client_for(xml_text),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        **kwargs,
    )


# --- TID validation -----------------------------------------------------


def test_validate_tid_accepts_numeric_string():
    assert validate_tid("6199") == "6199"


@pytest.mark.parametrize("bad_tid", ["", "abc", "12a", "-1", "6199 ", "TID6199"])
def test_validate_tid_rejects_non_numeric(bad_tid):
    with pytest.raises(ValueError):
        validate_tid(bad_tid)


# --- Normalization -------------------------------------------------------


@pytest.mark.asyncio
async def test_title_normalizes_found_response(tmp_path):
    xml_text = _read_fixture("syoboi_title_found.xml")
    adapter = _make_adapter(xml_text, tmp_path)

    dto = await adapter.title("6199")

    assert dto.source == "syoboi"
    assert dto.externalId == "6199"
    assert dto.mediaType == "anime"
    assert dto.title == "からかい上手の高木さん3"
    assert dto.titleYomi == "からかいじょうずのたかぎさん"
    assert dto.channel == "MBS・TBS"
    assert dto.url == "https://cal.syoboi.jp/tid/6199"

    assert len(dto.releases) == 1
    release = dto.releases[0]
    assert release.kind == "season-start"
    assert release.date == "2022-01-01"
    assert release.source == "syoboi"
    assert "MBS・TBS" in release.label


@pytest.mark.asyncio
async def test_title_not_found_raises_source_unavailable(tmp_path):
    xml_text = _read_fixture("syoboi_title_not_found.xml")
    adapter = _make_adapter(xml_text, tmp_path)

    with pytest.raises(SourceUnavailable) as exc_info:
        await adapter.title("999999999")

    assert exc_info.value.source == "syoboi"


@pytest.mark.asyncio
async def test_malformed_xml_raises_source_unavailable(tmp_path):
    adapter = _make_adapter("<not><valid", tmp_path)

    with pytest.raises(SourceUnavailable):
        await adapter.title("6199")


@pytest.mark.asyncio
async def test_http_error_raises_source_unavailable(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    adapter = SyoboiAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    with pytest.raises(SourceUnavailable):
        await adapter.title("6199")


# --- Caching ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_call_is_served_from_cache(tmp_path):
    xml_text = _read_fixture("syoboi_title_found.xml")
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, text=xml_text)

    adapter = SyoboiAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
    )

    await adapter.title("6199")
    await adapter.title("6199")

    assert call_count == 1


# --- Rate limiting -----------------------------------------------------


def test_default_rate_is_one_request_per_second():
    adapter = SyoboiAdapter()
    assert adapter._bucket.rate == pytest.approx(1.0)
    assert adapter._bucket.capacity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_rate_limit_is_enforced_between_requests(tmp_path):
    xml_text = _read_fixture("syoboi_title_found.xml")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml_text)

    # Fast-but-nonzero bucket so the test doesn't need a full second, while
    # still exercising the same acquire() gate used at rate=1/s in prod.
    bucket = TokenBucket(rate=5, capacity=1)
    adapter = SyoboiAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        bucket=bucket,
    )

    start = time.monotonic()
    await adapter.title("6199")  # different TIDs so neither hits the cache
    await adapter.title("1234")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15  # ~1/5s minimum gap, generous margin
