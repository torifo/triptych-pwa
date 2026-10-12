"""カクヨム (kakuyomu) adapter.

Scrapes a work's page and the popularity-ordered search page, and normalizes
them into local DTOs. No raw HTML is ever handed back to callers (see
specs/mvp-core/design.md, "Security Considerations": スクレイピングHTMLは正規化DTOに
変換してから返す).

Politeness (design.md 「レート制御・キャッシュ」 / 「収集マナー」):
- robots.txt is fetched once and honored for every path before it is requested
  (`/works/<id>` and `/search`, which robots.txt allows).
- 1 req/s token bucket + a global asyncio.Lock enforcing concurrency 1, shared
  by `work()` and `search()`.
- Explicit User-Agent on every request.
- TTL cache (work 24h / search 1h) to avoid re-scraping unchanged pages.

Parsing strategy: prefer the embedded Next.js data blob
(`<script id="__NEXT_DATA__">`), which carries a normalized Apollo cache of
the page's GraphQL data. Falling back to ad-hoc regex/string scanning is
avoided for now since the target only ships this JSON island; if it ever
disappears, `_extract_next_data` returning None causes a SourceUnavailable
rather than a guess. No external HTML/parser library is added.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import ValidationError

from ..cache import TTLCache
from ..dto import SearchResult, SourceUnavailable, WebNovelDTO
from ..ratelimit import TokenBucket
from ..timeutil import jst_date_from_iso

BASE_URL = "https://kakuyomu.jp"
USER_AGENT = "triptych/0.1 (personal tracker)"

# カクヨムの作品IDは数字列。
WORK_ID_RE = re.compile(r"^[0-9]+$")

RATE_PER_SECOND = 1.0
CACHE_TTL_SECONDS = 24 * 60 * 60  # scrape: 24h (design.md 「レート制御・キャッシュ」)
SEARCH_TTL_SECONDS = 60 * 60  # 検索: 1h
SEARCH_LIMIT = 20  # the search page carries 20 works per page

# Cache keys carry a schema version: older work entries held KakuyomuWorkDTO
# (with releases) and must not be read back as WebNovelDTO.
_WORK_CACHE_PREFIX = "kakuyomu:work:v2:"
_SEARCH_CACHE_PREFIX = "kakuyomu:search:v1:"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

_SERIAL_STATUS_COMPLETED = "COMPLETED"


def validate_work_id(work_id: str) -> str:
    """Validates a kakuyomu work id (numeric string). Raises ValueError if malformed."""
    if not WORK_ID_RE.match(work_id):
        raise ValueError(f"invalid kakuyomu work id: {work_id!r}")
    return work_id


def _extract_next_data(html: str) -> Optional[dict[str, Any]]:
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _resolve_ref(state: dict[str, Any], ref: Any) -> Optional[dict[str, Any]]:
    if not isinstance(ref, dict):
        return None
    key = ref.get("__ref")
    if key is None:
        return None
    entry = state.get(key)
    return entry if isinstance(entry, dict) else None


def _work_author(state: dict[str, Any], work: dict[str, Any]) -> Optional[str]:
    """Author name as the site shows it.

    Works posted through a publisher's official account carry the real
    author's name in `alternateAuthorName`; prefer it over the account name.
    """
    alternate = work.get("alternateAuthorName")
    if isinstance(alternate, str) and alternate.strip():
        return alternate.strip()
    author_entry = _resolve_ref(state, work.get("author"))
    if author_entry is None:
        return None
    name = author_entry.get("activityName") or author_entry.get("name")
    if not isinstance(name, str):
        return None
    return name.strip() or None


def _work_url(work_id: str) -> str:
    return f"{BASE_URL}/works/{work_id}"


def _page_state(data: dict[str, Any], what: str) -> dict[str, Any]:
    try:
        state = data["props"]["pageProps"]["__APOLLO_STATE__"]
    except (KeyError, TypeError) as exc:
        raise SourceUnavailable("kakuyomu", f"unexpected {what} page structure") from exc
    if not isinstance(state, dict):
        raise SourceUnavailable("kakuyomu", f"unexpected {what} page structure")
    return state


def _parse_work_page(work_id: str, html: str) -> WebNovelDTO:
    data = _extract_next_data(html)
    if data is None:
        raise SourceUnavailable(
            "kakuyomu", f"could not locate embedded data for work {work_id}"
        )

    try:
        query_work_id = data["query"]["workId"]
    except (KeyError, TypeError) as exc:
        raise SourceUnavailable(
            "kakuyomu", f"unexpected work page structure for {work_id}"
        ) from exc
    state = _page_state(data, f"work {work_id}")
    work = state.get(f"Work:{query_work_id}")
    if not isinstance(work, dict):
        raise SourceUnavailable(
            "kakuyomu", f"unexpected work page structure for {work_id}"
        )

    last_published = work.get("lastEpisodePublishedAt")
    return WebNovelDTO(
        source="kakuyomu",
        externalId=work_id,
        title=work.get("title") or "",
        author=_work_author(state, work),
        url=_work_url(work_id),
        episodeCount=int(work.get("publicEpisodeCount") or 0),
        completed=work.get("serialStatus") == _SERIAL_STATUS_COMPLETED,
        isShortStory=False,
        # UTC timestamp -> JST calendar date (UTC 16:30 is already "tomorrow").
        lastUpdate=jst_date_from_iso(str(last_published)) if last_published else None,
    )


def _search_nodes(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The Work nodes of the `searchWorks(...)` result on the search page."""
    root = state.get("ROOT_QUERY")
    if not isinstance(root, dict):
        raise SourceUnavailable("kakuyomu", "unexpected search page structure")
    for key, value in root.items():
        if not key.startswith("searchWorks(") or not isinstance(value, dict):
            continue
        nodes = value.get("nodes")
        if not isinstance(nodes, list):
            break
        works = (_resolve_ref(state, node) for node in nodes)
        return [work for work in works if work is not None]
    raise SourceUnavailable("kakuyomu", "unexpected search page structure")


def _parse_search_page(html: str) -> list[SearchResult]:
    data = _extract_next_data(html)
    if data is None:
        raise SourceUnavailable("kakuyomu", "could not locate embedded search data")
    state = _page_state(data, "search")

    results: list[SearchResult] = []
    for work in _search_nodes(state)[:SEARCH_LIMIT]:
        work_id = str(work.get("id") or "")
        title = work.get("title")
        if not WORK_ID_RE.match(work_id) or not title:
            continue
        parts = []
        author = _work_author(state, work)
        if author:
            parts.append(author)
        if work.get("hasPublication"):
            parts.append("書籍化")
        results.append(
            SearchResult(
                source="kakuyomu",
                externalId=work_id,
                mediaType="web-novel",
                title=title,
                cover=None,
                url=_work_url(work_id),
                subtitle="・".join(parts) or None,
            )
        )
    return results


class KakuyomuAdapter:
    """Adapter for scraping kakuyomu pages (1 req/s, concurrency 1)."""

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        cache: Optional[TTLCache] = None,
        bucket: Optional[TokenBucket] = None,
        lock: Optional[asyncio.Lock] = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._client = client or httpx.AsyncClient()
        self._cache = cache if cache is not None else TTLCache()
        self._bucket = bucket or TokenBucket(rate=RATE_PER_SECOND, capacity=1)
        self._lock = lock or asyncio.Lock()
        self._user_agent = user_agent

        self._robots_lock = asyncio.Lock()
        self._robots: Optional[RobotFileParser] = None

    async def _load_robots(self) -> RobotFileParser:
        """Fetches and parses robots.txt once; a failed fetch is retried later."""
        if self._robots is not None:
            return self._robots
        async with self._robots_lock:
            if self._robots is not None:
                return self._robots
            try:
                response = await self._client.get(
                    f"{BASE_URL}/robots.txt",
                    headers={"User-Agent": self._user_agent},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceUnavailable(
                    "kakuyomu", f"could not fetch robots.txt: {exc}"
                ) from exc
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            self._robots = parser
            return parser

    async def _ensure_robots_allowed(self, path_and_query: str) -> None:
        parser = await self._load_robots()
        if not parser.can_fetch(self._user_agent, path_and_query):
            path = path_and_query.split("?", 1)[0]
            raise SourceUnavailable(
                "kakuyomu", f"robots.txt disallows fetching {path}"
            )

    async def _get_html(
        self,
        path: str,
        params: Optional[dict[str, str]] = None,
        *,
        not_found_message: Optional[str] = None,
    ) -> str:
        """GET a page under the shared robots / rate-limit / concurrency rules."""
        path_and_query = f"{path}?{urlencode(params)}" if params else path
        await self._ensure_robots_allowed(path_and_query)

        async with self._lock:
            await self._bucket.acquire(1)
            try:
                response = await self._client.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    headers={"User-Agent": self._user_agent},
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable("kakuyomu", f"request failed: {exc}") from exc

        if response.status_code == 404 and not_found_message:
            raise SourceUnavailable("kakuyomu", not_found_message)
        if response.status_code >= 400:
            raise SourceUnavailable(
                "kakuyomu", f"unexpected status {response.status_code}"
            )
        return response.text

    async def work(self, work_id: str, refresh: bool = False) -> WebNovelDTO:
        """Fetches and normalizes a single work page, using the TTL cache when possible."""
        validate_work_id(work_id)

        cache_key = f"{_WORK_CACHE_PREFIX}{work_id}"
        if not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                try:
                    return WebNovelDTO.model_validate(cached)
                except ValidationError:
                    pass  # unreadable entry: fall through and refetch

        html = await self._get_html(
            f"/works/{work_id}", not_found_message=f"work {work_id} not found"
        )
        dto = _parse_work_page(work_id, html)
        self._cache.set(cache_key, dto.model_dump(), ttl_seconds=CACHE_TTL_SECONDS)
        return dto

    async def search(self, q: str, refresh: bool = False) -> list[SearchResult]:
        """First page (20 works) of the popularity-ordered search results."""
        query = q.strip()
        if not query:
            return []

        cache_key = f"{_SEARCH_CACHE_PREFIX}{query}"
        if not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                try:
                    return [SearchResult.model_validate(item) for item in cached]
                except ValidationError:
                    pass

        html = await self._get_html("/search", {"q": query, "order": "popular"})
        results = _parse_search_page(html)
        self._cache.set(
            cache_key,
            [item.model_dump() for item in results],
            ttl_seconds=SEARCH_TTL_SECONDS,
        )
        return results
