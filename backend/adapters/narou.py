"""なろう小説API (https://api.syosetu.com/novelapi/api/) adapter.

Normalizes raw なろう小説API responses into local DTOs. Raw JSON never leaves
this module (see specs/mvp-core/design.md, "API Design").

- User-Agent: `triptych/0.1 (personal tracker)` (see design.md, "レート制御・キャッシュ")
- Rate limit: 1 req/s, shared across `novel()` and `search()` for this source.
- gzip: `Accept-Encoding: gzip` is sent explicitly; httpx decodes transparently.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from ..cache import TTLCache
from ..dto import SearchResult, SourceUnavailable
from ..ratelimit import TokenBucket

NAROU_API_URL = "https://api.syosetu.com/novelapi/api/"
USER_AGENT = "triptych/0.1 (personal tracker)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}

# ncode: 'n' + 4 digits + 1-2 lowercase letters (case-insensitive on input).
NCODE_PATTERN = re.compile(r"^n\d{4}[a-z]{1,2}$")

DETAIL_TTL_SECONDS = 6 * 60 * 60  # 詳細: 6h
SEARCH_TTL_SECONDS = 60 * 60  # 検索: 1h

# One bucket per adapter (module-level singleton), shared by novel()/search().
_rate_limiter = TokenBucket(rate=1.0, capacity=1.0)


class NarouNovelDetail(BaseModel):
    """Normalized detail for a single なろう小説 work."""

    source: str = "narou"
    ncode: str
    title: str
    author: str
    generalAllNo: int
    generalLastup: str  # YYYY-MM-DD
    completed: bool
    url: str


def validate_ncode(ncode: str) -> str:
    """Validates and normalizes an ncode. Raises ValueError if malformed."""
    candidate = ncode.strip().lower()
    if not NCODE_PATTERN.match(candidate):
        raise ValueError(f"invalid ncode: {ncode!r}")
    return candidate


def _novel_url(ncode: str) -> str:
    return f"https://ncode.syosetu.com/{ncode}/"


def _normalize_novel(raw: dict[str, Any]) -> NarouNovelDetail:
    """Normalizes a single record from the なろうAPI `novelapi` response."""
    ncode = str(raw["ncode"]).lower()
    lastup = str(raw["general_lastup"]).split(" ")[0]
    return NarouNovelDetail(
        ncode=ncode,
        title=raw["title"],
        author=raw["writer"],
        generalAllNo=int(raw["general_all_no"]),
        generalLastup=lastup,
        completed=int(raw.get("end", 0)) == 1,
        url=_novel_url(ncode),
    )


def _normalize_search(raw: list[dict[str, Any]]) -> list[SearchResult]:
    """Normalizes a なろうAPI search response (list, first element is a count)."""
    results: list[SearchResult] = []
    for record in raw[1:]:
        ncode = str(record["ncode"]).lower()
        results.append(
            SearchResult(
                source="narou",
                externalId=ncode,
                mediaType="web-novel",
                title=record["title"],
                cover=None,
                url=_novel_url(ncode),
            )
        )
    return results


async def _get_json(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> Any:
    try:
        response = await client.get(
            NAROU_API_URL, params=params, headers=REQUEST_HEADERS
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceUnavailable("narou", str(exc)) from exc
    try:
        return response.json()
    except ValueError as exc:
        raise SourceUnavailable("narou", f"invalid JSON response: {exc}") from exc


async def novel(
    ncode: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Optional[TTLCache] = None,
    refresh: bool = False,
) -> NarouNovelDetail:
    """Fetches and normalizes a single novel's metadata by ncode."""
    normalized_ncode = validate_ncode(ncode)
    cache_key = f"narou:novel:{normalized_ncode}"

    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return NarouNovelDetail.model_validate(cached)

    await _rate_limiter.acquire()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        raw = await _get_json(
            client, {"ncode": normalized_ncode, "out": "json"}
        )
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(raw, list) or len(raw) < 2:
        raise SourceUnavailable("narou", f"novel not found: {ncode}")

    detail = _normalize_novel(raw[1])
    if cache is not None:
        cache.set(cache_key, detail.model_dump(), DETAIL_TTL_SECONDS)
    return detail


async def search(
    q: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Optional[TTLCache] = None,
    refresh: bool = False,
) -> list[SearchResult]:
    """Searches なろう小説 by title, returning normalized SearchResult hits."""
    cache_key = f"narou:search:{q}"

    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return [SearchResult.model_validate(item) for item in cached]

    await _rate_limiter.acquire()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        raw = await _get_json(client, {"word": q, "out": "json"})
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(raw, list) or len(raw) < 1:
        raise SourceUnavailable("narou", f"search failed for: {q}")

    results = _normalize_search(raw)
    if cache is not None:
        cache.set(
            cache_key, [item.model_dump() for item in results], SEARCH_TTL_SECONDS
        )
    return results
