"""カクヨム (kakuyomu) adapter.

Scrapes a work's page and normalizes it into a local DTO. No raw HTML is
ever handed back to callers (see specs/mvp-core/design.md, "Security
Considerations": スクレイピングHTMLは正規化DTOに変換してから返す).

Politeness (design.md 「レート制御・キャッシュ」 / 「収集マナー」):
- robots.txt is fetched once and honored before any /works/ request.
- 1 req/s token bucket + a global asyncio.Lock enforcing concurrency 1.
- Explicit User-Agent on every request.
- TTL cache (24h) to avoid re-scraping unchanged pages.

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
from typing import Any, Literal, Optional
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

from ..cache import TTLCache
from ..dto import ReleaseDTO, SourceUnavailable
from ..ratelimit import TokenBucket

BASE_URL = "https://kakuyomu.jp"
USER_AGENT = "triptych/0.1 (personal tracker)"

# カクヨムの作品IDは数字列。
WORK_ID_RE = re.compile(r"^[0-9]+$")

RATE_PER_SECOND = 1.0
CACHE_TTL_SECONDS = 24 * 60 * 60  # scrape: 24h (design.md 「レート制御・キャッシュ」)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

# Representative path used to evaluate robots.txt once at startup; work
# detail pages all live under /works/<id> and are checked as a group.
_ROBOTS_CHECK_PATH = "/works/0"


class KakuyomuWorkDTO(BaseModel):
    """Normalized kakuyomu work page.

    Not part of the shared collector DTOs in dto.py: kakuyomu-specific
    fields (author / episode count) don't fit MediaDetailDTO, so this
    extends the same shape locally instead of widening the shared schema.
    """

    source: Literal["kakuyomu"] = "kakuyomu"
    externalId: str
    mediaType: Literal["web-novel"] = "web-novel"
    title: str
    author: Optional[str] = None
    episodeCount: Optional[int] = None
    url: str
    releases: list[ReleaseDTO] = Field(default_factory=list)


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


def _parse_work_page(work_id: str, html: str) -> KakuyomuWorkDTO:
    data = _extract_next_data(html)
    if data is None:
        raise SourceUnavailable(
            "kakuyomu", f"could not locate embedded data for work {work_id}"
        )

    try:
        query_work_id = data["query"]["workId"]
        state = data["props"]["pageProps"]["__APOLLO_STATE__"]
        work = state[f"Work:{query_work_id}"]
    except (KeyError, TypeError) as exc:
        raise SourceUnavailable(
            "kakuyomu", f"unexpected work page structure for {work_id}"
        ) from exc

    title = work.get("title") or ""
    author_entry = _resolve_ref(state, work.get("author"))
    author = None
    if author_entry is not None:
        author = author_entry.get("activityName") or author_entry.get("name")

    episode_count = work.get("publicEpisodeCount")
    last_updated = work.get("lastEpisodePublishedAt")

    releases: list[ReleaseDTO] = []
    if last_updated:
        label = f"第{episode_count}話まで更新" if episode_count else "最終更新"
        releases.append(
            ReleaseDTO(
                kind="episode",
                label=label,
                date=str(last_updated)[:10],
                source="kakuyomu",
                externalId=work_id,
            )
        )

    return KakuyomuWorkDTO(
        externalId=work_id,
        title=title,
        author=author,
        episodeCount=episode_count,
        url=f"{BASE_URL}/works/{work_id}",
        releases=releases,
    )


class KakuyomuAdapter:
    """Adapter for scraping kakuyomu work pages (1 req/s, concurrency 1)."""

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
        self._robots_checked = False
        self._allowed = False

    async def _ensure_robots_allowed(self) -> None:
        if self._robots_checked:
            if not self._allowed:
                raise SourceUnavailable(
                    "kakuyomu", "robots.txt disallows fetching work pages"
                )
            return

        async with self._robots_lock:
            if self._robots_checked:
                if not self._allowed:
                    raise SourceUnavailable(
                        "kakuyomu", "robots.txt disallows fetching work pages"
                    )
                return

            try:
                response = await self._client.get(
                    f"{BASE_URL}/robots.txt",
                    headers={"User-Agent": self._user_agent},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                self._robots_checked = True
                self._allowed = False
                raise SourceUnavailable(
                    "kakuyomu", f"could not fetch robots.txt: {exc}"
                ) from exc

            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            self._allowed = parser.can_fetch(self._user_agent, _ROBOTS_CHECK_PATH)
            self._robots_checked = True

        if not self._allowed:
            raise SourceUnavailable(
                "kakuyomu", "robots.txt disallows fetching work pages"
            )

    async def work(self, work_id: str) -> KakuyomuWorkDTO:
        """Fetches and normalizes a single work page, using the TTL cache when possible."""
        validate_work_id(work_id)
        await self._ensure_robots_allowed()

        cache_key = f"kakuyomu:work:{work_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return KakuyomuWorkDTO.model_validate(cached)

        async with self._lock:
            await self._bucket.acquire(1)
            try:
                response = await self._client.get(
                    f"{BASE_URL}/works/{work_id}",
                    headers={"User-Agent": self._user_agent},
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable("kakuyomu", f"request failed: {exc}") from exc

        if response.status_code == 404:
            raise SourceUnavailable("kakuyomu", f"work {work_id} not found")
        if response.status_code >= 400:
            raise SourceUnavailable(
                "kakuyomu", f"unexpected status {response.status_code}"
            )

        dto = _parse_work_page(work_id, response.text)
        self._cache.set(cache_key, dto.model_dump(), ttl_seconds=CACHE_TTL_SECONDS)
        return dto
