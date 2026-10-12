"""なろう小説API (https://api.syosetu.com/novelapi/api/) adapter.

Normalizes raw なろう小説API responses into local DTOs. Raw JSON never leaves
this module (see specs/mvp-core/design.md, "API Design").

- User-Agent: `triptych/0.1 (personal tracker)` (see design.md, "レート制御・キャッシュ")
- Rate limit: 1 req/s, shared across `novel()` and `search()` for this source.
- gzip: `Accept-Encoding: gzip` is sent explicitly; httpx decodes transparently.

Facts verified against the live API (specs/data-coverage/design.md):
- `title=1` limits the keyword match to the title (otherwise the synopsis is
  searched too and the real work drops out of the first page).
- `type=re` keeps serialized works only (short stories are mostly reviews).
- `order=hyoka` sorts by rating, so the original work ranks first.
- `end`: 0 = completed serial or short story, 1 = still being serialized.
- `noveltype`: 1 = serial, 2 = short story.
- `of=` narrows the response to the listed fields. The plain-text names of
  the fields we ask for: t=title, n=ncode, w=writer, ga=general_all_no,
  gl=general_lastup (JST), e=end, nt=noveltype.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from pydantic import ValidationError

from ..cache import TTLCache
from ..dto import SearchResult, SourceUnavailable, WebNovelDTO
from ..ratelimit import TokenBucket

NAROU_API_URL = "https://api.syosetu.com/novelapi/api/"
USER_AGENT = "triptych/0.1 (personal tracker)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}

# ncode: 'n' + 4 digits + 1-2 lowercase letters (case-insensitive on input).
NCODE_PATTERN = re.compile(r"^n\d{4}[a-z]{1,2}$")

DETAIL_TTL_SECONDS = 6 * 60 * 60  # 詳細: 6h
SEARCH_TTL_SECONDS = 60 * 60  # 検索: 1h

SEARCH_LIMIT = 20
RESPONSE_FIELDS = "t-n-w-ga-gl-e-nt"

# Cache keys carry a schema version: older entries held a different shape
# (NarouNovelDetail / title-only SearchResult) and must not be read back.
_NOVEL_CACHE_PREFIX = "narou:novel:v2:"
_SEARCH_CACHE_PREFIX = "narou:search:v2:"

# One bucket per adapter (module-level singleton), shared by novel()/search().
_rate_limiter = TokenBucket(rate=1.0, capacity=1.0)


def validate_ncode(ncode: str) -> str:
    """Validates and normalizes an ncode. Raises ValueError if malformed."""
    candidate = ncode.strip().lower()
    if not NCODE_PATTERN.match(candidate):
        raise ValueError(f"invalid ncode: {ncode!r}")
    return candidate


def _novel_url(ncode: str) -> str:
    return f"https://ncode.syosetu.com/{ncode}/"


def _is_short_story(raw: dict[str, Any]) -> bool:
    # `of=nt` answers with `noveltype`; the full (no `of`) payload spells it
    # `novel_type`. Accept both.
    value = raw.get("noveltype", raw.get("novel_type"))
    return value is not None and int(value) == 2


def _is_completed(raw: dict[str, Any]) -> bool:
    # `end`: 0 = completed serial or short story, 1 = still serializing.
    # A missing value is "unknown", which must not be reported as completed.
    value = raw.get("end")
    return value is not None and int(value) == 0


def _normalize_novel(raw: dict[str, Any]) -> WebNovelDTO:
    """Normalizes a single record from the なろうAPI `novelapi` response."""
    ncode = str(raw["ncode"]).lower()
    # general_lastup is "YYYY-MM-DD HH:MM:SS" in JST: the date part is JST.
    lastup = str(raw.get("general_lastup") or "").split(" ")[0] or None
    return WebNovelDTO(
        source="narou",
        externalId=ncode,
        title=raw["title"],
        author=raw.get("writer"),
        url=_novel_url(ncode),
        episodeCount=int(raw.get("general_all_no") or 0),
        completed=_is_completed(raw),
        isShortStory=_is_short_story(raw),
        lastUpdate=lastup,
    )


def _normalize_search(raw: list[dict[str, Any]]) -> list[SearchResult]:
    """Normalizes a なろうAPI search response (list, first element is a count)."""
    results: list[SearchResult] = []
    for record in raw[1:]:
        ncode = str(record["ncode"]).lower()
        writer = (record.get("writer") or "").strip()
        results.append(
            SearchResult(
                source="narou",
                externalId=ncode,
                mediaType="web-novel",
                title=record["title"],
                cover=None,
                url=_novel_url(ncode),
                subtitle=writer or None,
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
) -> WebNovelDTO:
    """Fetches and normalizes a single novel's metadata by ncode."""
    normalized_ncode = validate_ncode(ncode)
    cache_key = f"{_NOVEL_CACHE_PREFIX}{normalized_ncode}"

    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            try:
                return WebNovelDTO.model_validate(cached)
            except ValidationError:
                pass  # unreadable entry: fall through and refetch

    await _rate_limiter.acquire()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        raw = await _get_json(
            client,
            {"ncode": normalized_ncode, "of": RESPONSE_FIELDS, "out": "json"},
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
    """Searches なろう小説 by title (serials only, best rated first)."""
    cache_key = f"{_SEARCH_CACHE_PREFIX}{q}"

    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            try:
                return [SearchResult.model_validate(item) for item in cached]
            except ValidationError:
                pass

    await _rate_limiter.acquire()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        raw = await _get_json(
            client,
            {
                "word": q,
                "title": 1,
                "type": "re",
                "order": "hyoka",
                "lim": SEARCH_LIMIT,
                "of": RESPONSE_FIELDS,
                "out": "json",
            },
        )
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
