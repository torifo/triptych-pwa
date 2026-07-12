"""AniList GraphQL adapter.

Talks to https://graphql.anilist.co, normalizes responses into the common
DTOs (`backend.dto`), and applies the source's own rate limit + TTL cache
(see specs/mvp-core/design.md, "API Design" / "レート制御・キャッシュ").

AniList is used for two things in triptych:
  - cross-media search (anime / manga / light novel, via `format: NOVEL`)
  - anime-adaptation detection for tracked novel/manga Works (FR-005), which
    matches on `title.native` + `synonyms` rather than romaji/english to
    avoid false positives across languages.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, Optional

import httpx

from ..cache import TTLCache
from ..dto import (
    AiringStatus,
    DetectionCandidateDTO,
    MediaDetailDTO,
    MediaType,
    ReleaseDTO,
    SearchResult,
    SourceUnavailable,
)
from ..ratelimit import TokenBucket

ANILIST_ENDPOINT = "https://graphql.anilist.co"
USER_AGENT = "triptych/0.1 (personal tracker)"

# AniList's own throttling is much looser than this; we self-limit to stay
# well clear of it (design.md: "anilist: 60/min に自主制限").
RATE_LIMIT_PER_MINUTE = 60.0

SEARCH_TTL_SECONDS = 60 * 60  # 1h
DETAIL_TTL_SECONDS = 6 * 60 * 60  # 6h

# Relation types that indicate the related node is (or leads to) the
# original source material of the queried media (US-001 原作候補).
SOURCE_RELATION_TYPES = {"ADAPTATION", "SOURCE"}

_SEARCH_QUERY = """
query ($search: String, $type: MediaType) {
  Page(perPage: 10) {
    media(search: $search, type: $type) {
      id
      type
      format
      title { romaji english native }
      coverImage { large medium }
      siteUrl
    }
  }
}
"""

_DETAIL_QUERY = """
query ($id: Int) {
  Media(id: $id) {
    id
    type
    format
    status
    title { romaji english native }
    synonyms
    coverImage { large medium }
    siteUrl
    startDate { year month day }
    nextAiringEpisode { episode airingAt timeUntilAiring }
    relations {
      edges {
        relationType
        node {
          id
          type
          format
          title { romaji english native }
          siteUrl
        }
      }
    }
  }
}
"""

_DETECT_QUERY = """
query ($search: String) {
  Page(perPage: 10) {
    media(search: $search, type: ANIME) {
      id
      title { romaji english native }
      synonyms
      status
      startDate { year month day }
    }
  }
}
"""


def normalize_title(title: str) -> str:
    """NFKC-normalize, lowercase, and strip whitespace/punctuation.

    Used both for detection matching (design.md `titleNormalized`) and for
    scoring AniList candidates against a tracked Work's title.
    """
    nfkc = unicodedata.normalize("NFKC", title)
    lowered = nfkc.lower()
    return re.sub(r"[\s\W_]+", "", lowered, flags=re.UNICODE)


def _media_type_for(media_type: str, media_format: Optional[str]) -> MediaType:
    if media_type == "ANIME":
        return "anime"
    if media_format == "NOVEL":
        return "light-novel"
    return "manga"


def _title_text(title: dict[str, Optional[str]]) -> str:
    return title.get("native") or title.get("romaji") or title.get("english") or ""


def _match_score(query_normalized: str, candidate_titles: list[str]) -> float:
    """Simple containment-based score against a set of candidate strings.

    1.0 for an exact match (after normalization), otherwise a score based on
    how much of the shorter string is covered by the longer one when one is
    a substring of the other; 0.0 if neither contains the other.
    """
    best = 0.0
    for raw in candidate_titles:
        if not raw:
            continue
        candidate_normalized = normalize_title(raw)
        if not candidate_normalized:
            continue
        if candidate_normalized == query_normalized:
            return 1.0
        if query_normalized in candidate_normalized:
            score = len(query_normalized) / len(candidate_normalized)
        elif candidate_normalized in query_normalized:
            score = len(candidate_normalized) / len(query_normalized)
        else:
            continue
        best = max(best, score)
    return best


class AniListAdapter:
    """Client for the AniList GraphQL API with caching + rate limiting."""

    def __init__(
        self,
        *,
        cache: Optional[TTLCache] = None,
        client: Optional[httpx.AsyncClient] = None,
        bucket: Optional[TokenBucket] = None,
    ) -> None:
        self._cache = cache if cache is not None else TTLCache()
        self._client = client
        self._owns_client = client is None
        self._bucket = (
            bucket
            if bucket is not None
            else TokenBucket(rate=RATE_LIMIT_PER_MINUTE / 60.0, capacity=RATE_LIMIT_PER_MINUTE)
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT}, timeout=10.0
            )
        return self._client

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        await self._bucket.acquire()
        client = await self._get_client()
        try:
            response = await client.post(
                ANILIST_ENDPOINT, json={"query": query, "variables": variables}
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable("anilist", str(exc)) from exc

        if response.status_code >= 400:
            raise SourceUnavailable(
                "anilist", f"HTTP {response.status_code}: {response.text[:200]}"
            )

        payload = response.json()
        if payload.get("errors"):
            raise SourceUnavailable("anilist", str(payload["errors"]))
        return payload["data"]

    # -- search -----------------------------------------------------------

    async def search(self, q: str) -> list[SearchResult]:
        """Cross-media search (anime + manga + light novel).

        AniList's `media(search:, type:)` only accepts a single `MediaType`
        per query, so anime and manga (which also covers light novels via
        `format: NOVEL`) are fetched with two calls and merged.
        """
        cache_key = f"anilist:search:{q}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [SearchResult.model_validate(item) for item in cached]

        results: list[SearchResult] = []
        for media_type in ("ANIME", "MANGA"):
            data = await self._graphql(_SEARCH_QUERY, {"search": q, "type": media_type})
            for node in data["Page"]["media"]:
                results.append(self._normalize_search_node(node))

        self._cache.set(
            cache_key, [item.model_dump() for item in results], SEARCH_TTL_SECONDS
        )
        return results

    def _normalize_search_node(self, node: dict[str, Any]) -> SearchResult:
        cover_image = node.get("coverImage") or {}
        return SearchResult(
            source="anilist",
            externalId=str(node["id"]),
            mediaType=_media_type_for(node["type"], node.get("format")),
            title=_title_text(node["title"]),
            cover=cover_image.get("large") or cover_image.get("medium"),
            url=node["siteUrl"],
        )

    # -- media detail -------------------------------------------------------

    async def media(self, media_id: str | int) -> MediaDetailDTO:
        """Detail for a single AniList media, with source-material relations
        and the next airing episode normalized into `releases`.
        """
        cache_key = f"anilist:media:{media_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return MediaDetailDTO.model_validate(cached)

        data = await self._graphql(_DETAIL_QUERY, {"id": int(media_id)})
        media_node = data["Media"]
        detail = self._normalize_media(media_node)

        self._cache.set(cache_key, detail.model_dump(), DETAIL_TTL_SECONDS)
        return detail

    def _normalize_media(self, node: dict[str, Any]) -> MediaDetailDTO:
        cover_image = node.get("coverImage") or {}
        releases: list[ReleaseDTO] = []

        start_date = node.get("startDate") or {}
        start_date_str = _format_date(start_date)
        if start_date_str:
            releases.append(
                ReleaseDTO(
                    kind="season-start",
                    label=_title_text(node["title"]),
                    date=start_date_str,
                    source="anilist",
                    externalId=str(node["id"]),
                )
            )

        next_episode = node.get("nextAiringEpisode")
        if next_episode:
            airing_date = time.strftime(
                "%Y-%m-%d", time.gmtime(next_episode["airingAt"])
            )
            releases.append(
                ReleaseDTO(
                    kind="episode",
                    label=f"第{next_episode['episode']}話",
                    date=airing_date,
                    source="anilist",
                    externalId=str(node["id"]),
                )
            )

        return MediaDetailDTO(
            source="anilist",
            externalId=str(node["id"]),
            mediaType=_media_type_for(node["type"], node.get("format")),
            title=_title_text(node["title"]),
            cover=cover_image.get("large") or cover_image.get("medium"),
            url=node["siteUrl"],
            releases=releases,
        )

    def source_candidates(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract original-work candidates (US-001) from a raw `Media` node's
        `relations` — edges whose `relationType` is SOURCE/ADAPTATION.
        """
        edges = (node.get("relations") or {}).get("edges") or []
        candidates = []
        for edge in edges:
            if edge.get("relationType") not in SOURCE_RELATION_TYPES:
                continue
            related = edge["node"]
            candidates.append(
                {
                    "externalId": str(related["id"]),
                    "mediaType": _media_type_for(related["type"], related.get("format")),
                    "title": _title_text(related["title"]),
                    "url": related["siteUrl"],
                }
            )
        return candidates

    # -- detection ----------------------------------------------------------

    async def detect(self, title: str) -> list[DetectionCandidateDTO]:
        """Search AniList anime for candidates matching `title` (FR-005).

        Scoring is done against `title.native` and `synonyms` only (not
        romaji/english) to avoid cross-language false positives, per
        design.md.
        """
        cache_key = f"anilist:detect:{title}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [DetectionCandidateDTO.model_validate(item) for item in cached]

        data = await self._graphql(_DETECT_QUERY, {"search": title})
        query_normalized = normalize_title(title)

        candidates: list[DetectionCandidateDTO] = []
        for node in data["Page"]["media"]:
            candidate_titles = [node["title"].get("native") or ""] + list(
                node.get("synonyms") or []
            )
            score = _match_score(query_normalized, candidate_titles)
            start_date_str = _format_date(node.get("startDate") or {})
            candidates.append(
                DetectionCandidateDTO(
                    externalId=str(node["id"]),
                    title=_title_text(node["title"]),
                    airingStatus=_as_airing_status(node["status"]),
                    startDate=start_date_str,
                    matchScore=score,
                )
            )

        candidates.sort(key=lambda c: c.matchScore, reverse=True)

        self._cache.set(
            cache_key, [item.model_dump() for item in candidates], SEARCH_TTL_SECONDS
        )
        return candidates


def _format_date(parts: dict[str, Optional[int]]) -> Optional[str]:
    year, month, day = parts.get("year"), parts.get("month"), parts.get("day")
    if not year or not month or not day:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _as_airing_status(status: str) -> AiringStatus:
    if status in ("NOT_YET_RELEASED", "RELEASING", "FINISHED"):
        return status  # type: ignore[return-value]
    # AniList also has CANCELLED / HIATUS; treat both as FINISHED-adjacent
    # for our purposes (no further episodes expected).
    return "FINISHED"
