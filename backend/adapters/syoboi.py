"""しょぼいカレンダー (syoboi) adapter.

Fetches title metadata from the syoboi calendar TitleLookup API and
normalizes it into a local DTO (see specs/mvp-core/design.md, "API Design"
and "レート制御・キャッシュ"). No raw XML is ever handed back to callers.
"""

from __future__ import annotations

import re
from typing import Literal, Optional
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, Field

from ..cache import TTLCache
from ..dto import ReleaseDTO, SourceUnavailable
from ..ratelimit import TokenBucket

BASE_URL = "https://cal.syoboi.jp/db.php"
USER_AGENT = "triptych/0.1 (personal tracker)"

# しょぼいカレンダーの TID は正の整数のみ。
TID_RE = re.compile(r"^[0-9]+$")

RATE_PER_SECOND = 1.0
CACHE_TTL_SECONDS = 6 * 60 * 60  # 詳細情報: 6h (design.md 「レート制御・キャッシュ」)


class SyoboiTitleDTO(BaseModel):
    """Normalized syoboi title lookup result.

    Not part of the shared collector DTOs in dto.py: syoboi-specific fields
    (yomi / channel) don't fit MediaDetailDTO, so this extends the same
    shape locally instead of widening the shared schema.
    """

    source: Literal["syoboi"] = "syoboi"
    externalId: str
    mediaType: Literal["anime"] = "anime"
    title: str
    titleYomi: Optional[str] = None
    channel: Optional[str] = None
    url: str
    releases: list[ReleaseDTO] = Field(default_factory=list)


def validate_tid(tid: str) -> str:
    """Validates a syoboi TID (numeric string). Raises ValueError if malformed."""
    if not TID_RE.match(tid):
        raise ValueError(f"invalid syoboi TID: {tid!r}")
    return tid


def _text(item: ElementTree.Element, tag: str) -> Optional[str]:
    child = item.find(tag)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _parse_title_response(tid: str, xml_text: str) -> SyoboiTitleDTO:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SourceUnavailable("syoboi", f"malformed XML response: {exc}") from exc

    result = root.find("Result")
    code = _text(result, "Code") if result is not None else None
    if code != "200":
        message = _text(result, "Message") if result is not None else None
        raise SourceUnavailable(
            "syoboi", message or f"TitleLookup failed for TID {tid} (code={code})"
        )

    item = root.find("./TitleItems/TitleItem")
    if item is None:
        raise SourceUnavailable("syoboi", f"no TitleItem for TID {tid}")

    title = _text(item, "Title") or ""
    title_yomi = _text(item, "TitleYomi")
    channel = _text(item, "FirstCh")
    first_year = _text(item, "FirstYear")
    first_month = _text(item, "FirstMonth")

    releases: list[ReleaseDTO] = []
    if first_year and first_month:
        label = f"放送開始（{channel}）" if channel else "放送開始"
        releases.append(
            ReleaseDTO(
                kind="season-start",
                label=label,
                date=f"{int(first_year):04d}-{int(first_month):02d}-01",
                source="syoboi",
                externalId=tid,
            )
        )

    return SyoboiTitleDTO(
        externalId=tid,
        title=title,
        titleYomi=title_yomi,
        channel=channel,
        url=f"https://cal.syoboi.jp/tid/{tid}",
        releases=releases,
    )


class SyoboiAdapter:
    """Adapter for the syoboi calendar TitleLookup API (1 req/s)."""

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        cache: Optional[TTLCache] = None,
        bucket: Optional[TokenBucket] = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._client = client or httpx.AsyncClient()
        self._cache = cache if cache is not None else TTLCache()
        self._bucket = bucket or TokenBucket(rate=RATE_PER_SECOND, capacity=1)
        self._user_agent = user_agent

    async def title(self, tid: str) -> SyoboiTitleDTO:
        """Looks up a single title by TID, using the TTL cache when possible."""
        validate_tid(tid)

        cache_key = f"syoboi:title:{tid}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return SyoboiTitleDTO.model_validate(cached)

        await self._bucket.acquire(1)
        try:
            response = await self._client.get(
                BASE_URL,
                params={"Command": "TitleLookup", "TID": tid},
                headers={"User-Agent": self._user_agent},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable("syoboi", f"request failed: {exc}") from exc

        dto = _parse_title_response(tid, response.text)
        self._cache.set(cache_key, dto.model_dump(), ttl_seconds=CACHE_TTL_SECONDS)
        return dto
