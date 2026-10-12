"""AniList GraphQL adapter.

Talks to https://graphql.anilist.co, normalizes responses into the common
DTOs (`backend.dto`), and applies the source's own rate limit + TTL cache
(see specs/mvp-core/design.md, "API Design" / "レート制御・キャッシュ" and
specs/data-coverage/design.md).

AniList is used for:
  - cross-media search (anime / manga / light novel, via `format: NOVEL`)
  - anime release dates (season start + the next airing episode, in the
    Japanese broadcast-day convention: JST, day boundary at 05:00)
  - anime-adaptation / new-season detection through `relations`
  - resolving the original novel / manga entry of a tracked Work by title
  - title-based anime detection, which matches on `title.native` +
    `synonyms` rather than romaji/english to avoid cross-language false
    positives

Adult-only media (`isAdult`) is excluded from every search / detection /
relation result.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal, Optional

import httpx

from ..cache import TTLCache
from ..dto import (
    AiringStatus,
    DetectionCandidateDTO,
    MediaDetailDTO,
    MediaType,
    RelationEdge,
    RelationsDTO,
    ReleaseDTO,
    SearchResult,
    SourceUnavailable,
)
from ..ratelimit import TokenBucket
from ..timeutil import broadcast_day, partial_date

ANILIST_ENDPOINT = "https://graphql.anilist.co"
USER_AGENT = "triptych/0.1 (personal tracker)"

# AniList allows 30 req/min; burst + one minute of refill (4 + 25) stays under it.
RATE_LIMIT_PER_MINUTE = 25.0
RATE_LIMIT_BURST = 4.0

SEARCH_TTL_SECONDS = 60 * 60  # 1h
DETAIL_TTL_SECONDS = 6 * 60 * 60  # 6h

# Bump when the cached payload shape changes so stale entries are ignored.
_CACHE_VERSION = "v2"

# Relation types that indicate the related node is (or leads to) the
# original source material of the queried media (US-001 原作候補).
SOURCE_RELATION_TYPES = {"ADAPTATION", "SOURCE"}

_AIRING_STATUSES = {"NOT_YET_RELEASED", "RELEASING", "FINISHED", "CANCELLED", "HIATUS"}

SourceKind = Literal["novel", "manga"]
_SOURCE_FORMATS: dict[str, list[str]] = {
    "novel": ["NOVEL"],
    "manga": ["MANGA", "ONE_SHOT"],
}
_SOURCE_MEDIA_TYPE: dict[str, MediaType] = {
    "novel": "light-novel",
    "manga": "manga",
}

MAX_SOURCE_CANDIDATES = 5

_SEARCH_QUERY = """
query ($search: String, $type: MediaType) {
  Page(perPage: 10) {
    media(search: $search, type: $type, isAdult: false) {
      id
      type
      format
      isAdult
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
    isAdult
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
          status
          isAdult
          title { romaji english native }
          startDate { year month day }
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
    media(search: $search, type: ANIME, isAdult: false) {
      id
      format
      isAdult
      title { romaji english native }
      synonyms
      status
      startDate { year month day }
    }
  }
}
"""

_RESOLVE_SOURCE_QUERY = """
query ($search: String, $formats: [MediaFormat]) {
  Page(perPage: 10) {
    media(search: $search, type: MANGA, format_in: $formats, isAdult: false) {
      id
      type
      format
      isAdult
      popularity
      title { romaji english native }
      synonyms
      coverImage { large medium }
      siteUrl
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


def _source_match_score(query_normalized: str, candidate_titles: list[str]) -> float:
    """Tier score for resolving a tracked Work's title to an AniList source entry.

    Subtitled / prefixed titles are tolerated, because the same work is
    listed as "無職転生" in one place and "無職転生 ～異世界行ったら本気だす～"
    in another:

      exact                          -> 1.0
      one starts with the other      -> 0.8
      one merely contains the other  -> 0.5
      unrelated                      -> 0.0

    Length is deliberately not part of the score: a short spin-off suffix
    ("～蛇足編～") must not outrank the main series; ties are broken by
    popularity in `resolve_source`.
    """
    best = 0.0
    for raw in candidate_titles:
        if not raw:
            continue
        candidate_normalized = normalize_title(raw)
        if not candidate_normalized or not query_normalized:
            continue
        if candidate_normalized == query_normalized:
            return 1.0
        shorter, longer = sorted((query_normalized, candidate_normalized), key=len)
        if shorter not in longer:
            continue
        best = max(best, 0.8 if longer.startswith(shorter) else 0.5)
    return best


def _date_parts(parts: Optional[dict[str, Optional[int]]]):
    """(YYYY-MM-DD, precision) for an AniList FuzzyDate, or None if absent."""
    if not parts:
        return None
    return partial_date(parts.get("year"), parts.get("month"), parts.get("day"))


def _as_airing_status(status: Optional[str]) -> AiringStatus:
    if status in _AIRING_STATUSES:
        return status  # type: ignore[return-value]
    # Unknown / missing: nothing better to say than "no further episodes".
    return "FINISHED"


def _optional_airing_status(status: Optional[str]) -> Optional[AiringStatus]:
    if status in _AIRING_STATUSES:
        return status  # type: ignore[return-value]
    return None


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
            else TokenBucket(
                rate=RATE_LIMIT_PER_MINUTE / 60.0, capacity=RATE_LIMIT_BURST
            )
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

    def _cache_key(self, *parts: Any) -> str:
        return ":".join(["anilist", _CACHE_VERSION, *(str(p) for p in parts)])

    # -- search -----------------------------------------------------------

    async def search(self, q: str, refresh: bool = False) -> list[SearchResult]:
        """Cross-media search (anime + manga + light novel), adult media excluded.

        AniList's `media(search:, type:)` only accepts a single `MediaType`
        per query, so anime and manga (which also covers light novels via
        `format: NOVEL`) are fetched with two calls and merged.
        """
        cache_key = self._cache_key("search", q)
        if not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [SearchResult.model_validate(item) for item in cached]

        results: list[SearchResult] = []
        for media_type in ("ANIME", "MANGA"):
            data = await self._graphql(_SEARCH_QUERY, {"search": q, "type": media_type})
            for node in data["Page"]["media"]:
                if node.get("isAdult"):
                    continue
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

    async def _fetch_node(self, media_id: str | int, refresh: bool) -> dict[str, Any]:
        """Raw `Media` node (detail + relations). One cached request serves
        both `media()` and `relations()`."""
        cache_key = self._cache_key("node", int(media_id))
        if not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        data = await self._graphql(_DETAIL_QUERY, {"id": int(media_id)})
        node = data["Media"]
        self._cache.set(cache_key, node, DETAIL_TTL_SECONDS)
        return node

    async def media(self, media_id: str | int, refresh: bool = False) -> MediaDetailDTO:
        """Detail for a single AniList media, with the season start and the
        next airing episode normalized into `releases`.
        """
        return self._normalize_media(await self._fetch_node(media_id, refresh))

    def _normalize_media(self, node: dict[str, Any]) -> MediaDetailDTO:
        cover_image = node.get("coverImage") or {}
        releases: list[ReleaseDTO] = []

        # Novel / manga entries get their volumes from the paper-book sources.
        if node.get("type") == "ANIME":
            start = _date_parts(node.get("startDate"))
            if start:
                start_date, precision = start
                releases.append(
                    ReleaseDTO(
                        kind="season-start",
                        label=_title_text(node["title"]),
                        date=start_date,
                        precision=precision,
                        source="anilist",
                        externalId=str(node["id"]),
                    )
                )

            next_episode = node.get("nextAiringEpisode")
            if next_episode:
                airing_date, airing_time = broadcast_day(next_episode["airingAt"])
                releases.append(
                    ReleaseDTO(
                        kind="episode",
                        label=f"第{next_episode['episode']}話",
                        date=airing_date,
                        precision="day",
                        time=airing_time,
                        source="anilist",
                        externalId=f"{node['id']}:{next_episode['episode']}",
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

    # -- relations ----------------------------------------------------------

    async def relations(self, media_id: str | int, refresh: bool = False) -> RelationsDTO:
        """Relation edges of a media (ADAPTATION / SEQUEL / SOURCE / ...),
        with adult-only nodes dropped."""
        node = await self._fetch_node(media_id, refresh)
        return self._normalize_relations(node)

    def _normalize_relations(self, node: dict[str, Any]) -> RelationsDTO:
        edges: list[RelationEdge] = []
        for edge in (node.get("relations") or {}).get("edges") or []:
            related = edge.get("node")
            if not related or related.get("isAdult"):
                continue
            start = _date_parts(related.get("startDate"))
            edges.append(
                RelationEdge(
                    relationType=edge["relationType"],
                    externalId=str(related["id"]),
                    type=related["type"],
                    format=related.get("format"),
                    title=_title_text(related["title"]),
                    status=_optional_airing_status(related.get("status")),
                    startDate=start[0] if start else None,
                    startPrecision=start[1] if start else None,
                    isAdult=False,
                )
            )
        return RelationsDTO(
            externalId=str(node["id"]),
            type=node["type"],
            format=node.get("format"),
            title=_title_text(node["title"]),
            relations=edges,
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
            if related.get("isAdult"):
                continue
            candidates.append(
                {
                    "externalId": str(related["id"]),
                    "mediaType": _media_type_for(related["type"], related.get("format")),
                    "title": _title_text(related["title"]),
                    "url": related["siteUrl"],
                }
            )
        return candidates

    # -- resolve source -----------------------------------------------------

    async def resolve_source(
        self, title: str, kind: SourceKind, refresh: bool = False
    ) -> list[SearchResult]:
        """Find the AniList NOVEL (kind="novel") or MANGA/ONE_SHOT
        (kind="manga") entries for a tracked Work's title.

        Candidates are matched against the normalized native title and
        synonyms; a title that starts with, or contains, the other is
        accepted (subtitles / prefixes) but ranks below an exact match, and
        popularity orders entries within the same tier. At most 5 are
        returned, best first. Unrelated hits are dropped.
        """
        if kind not in _SOURCE_FORMATS:
            raise ValueError(f"kind must be 'novel' or 'manga', got {kind!r}")

        cache_key = self._cache_key("resolve-source", kind, title)
        if not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [SearchResult.model_validate(item) for item in cached]

        data = await self._graphql(
            _RESOLVE_SOURCE_QUERY,
            {"search": title, "formats": _SOURCE_FORMATS[kind]},
        )
        query_normalized = normalize_title(title)
        allowed_formats = set(_SOURCE_FORMATS[kind])

        scored: list[tuple[float, int, SearchResult]] = []
        seen: set[str] = set()
        for node in data["Page"]["media"]:
            if node.get("isAdult") or node.get("format") not in allowed_formats:
                continue
            external_id = str(node["id"])
            if external_id in seen:
                continue
            seen.add(external_id)
            score = _source_match_score(
                query_normalized,
                [(node["title"] or {}).get("native") or ""]
                + list(node.get("synonyms") or []),
            )
            if score <= 0.0:
                continue
            cover_image = node.get("coverImage") or {}
            scored.append(
                (
                    score,
                    node.get("popularity") or 0,
                    SearchResult(
                        source="anilist",
                        externalId=external_id,
                        mediaType=_SOURCE_MEDIA_TYPE[kind],
                        title=_title_text(node["title"]),
                        cover=cover_image.get("large") or cover_image.get("medium"),
                        url=node["siteUrl"],
                    ),
                )
            )

        # sort() is stable: full ties keep AniList's own relevance order.
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results = [item[2] for item in scored[:MAX_SOURCE_CANDIDATES]]

        self._cache.set(
            cache_key, [item.model_dump() for item in results], SEARCH_TTL_SECONDS
        )
        return results

    # -- detection ----------------------------------------------------------

    async def detect(
        self, title: str, refresh: bool = False
    ) -> list[DetectionCandidateDTO]:
        """Search AniList anime for candidates matching `title` (FR-005).

        Scoring is done against `title.native` and `synonyms` only (not
        romaji/english) to avoid cross-language false positives, per
        design.md. Adult media is excluded. Results are title-based
        (`via="title"`); relation-based detection goes through `relations()`.
        """
        cache_key = self._cache_key("detect", title)
        if not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [DetectionCandidateDTO.model_validate(item) for item in cached]

        data = await self._graphql(_DETECT_QUERY, {"search": title})
        query_normalized = normalize_title(title)

        candidates: list[DetectionCandidateDTO] = []
        for node in data["Page"]["media"]:
            if node.get("isAdult"):
                continue
            candidate_titles = [node["title"].get("native") or ""] + list(
                node.get("synonyms") or []
            )
            score = _match_score(query_normalized, candidate_titles)
            start = _date_parts(node.get("startDate"))
            candidates.append(
                DetectionCandidateDTO(
                    externalId=str(node["id"]),
                    title=_title_text(node["title"]),
                    airingStatus=_as_airing_status(node.get("status")),
                    format=node.get("format"),
                    startDate=start[0] if start else None,
                    startPrecision=start[1] if start else None,
                    matchScore=score,
                    via="title",
                )
            )

        candidates.sort(key=lambda c: c.matchScore, reverse=True)

        self._cache.set(
            cache_key, [item.model_dump() for item in candidates], SEARCH_TTL_SECONDS
        )
        return candidates
