"""`/api/search` — cross-source search (FR-006).

Runs the anilist and narou adapters' `search()` in parallel via
`asyncio.gather` and merges the results into a single list. A source that
raises `SourceUnavailable` (or any other error) is skipped rather than
failing the whole request — the endpoint always returns whatever partial
results are available, plus per-source error info (see
specs/mvp-core/design.md, "API Design (Collector)" / US-005).

Also exposes `/api/search/anilist-origins/{media_id}`, which surfaces the
AniList SOURCE/ADAPTATION relations (US-001 原作候補) for a registered anime
result. This lives here rather than on `backend/api/anilist.py` /
`MediaDetailDTO` (out of scope for this task) — it reuses
`AniListAdapter.source_candidates()` against the raw detail-query node
directly, since `AniListAdapter.media()` normalizes relations away.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..adapters import anilist as anilist_module
from ..adapters import narou as narou_adapter
from ..adapters.anilist import AniListAdapter
from ..cache import TTLCache
from ..dto import MediaType, SearchResult, SourceUnavailable

router = APIRouter(prefix="/api/search", tags=["search"])

_anilist_adapter: Optional[AniListAdapter] = None
_narou_cache = TTLCache()


def get_anilist_adapter() -> AniListAdapter:
    global _anilist_adapter
    if _anilist_adapter is None:
        _anilist_adapter = AniListAdapter()
    return _anilist_adapter


class SearchErrorInfo(BaseModel):
    """Per-source failure recorded when a source is skipped (see US-005)."""

    source: str
    message: str


class SearchResponse(BaseModel):
    results: list[SearchResult]
    errors: list[SearchErrorInfo] = Field(default_factory=list)


async def _search_anilist(q: str) -> list[SearchResult]:
    return await get_anilist_adapter().search(q)


async def _search_narou(q: str) -> list[SearchResult]:
    return await narou_adapter.search(q, cache=_narou_cache)


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    types: Optional[str] = Query(None, description="comma-separated mediaType filter"),
) -> SearchResponse:
    sources = {
        "anilist": _search_anilist(q),
        "narou": _search_narou(q),
    }
    outcomes = await asyncio.gather(*sources.values(), return_exceptions=True)

    results: list[SearchResult] = []
    errors: list[SearchErrorInfo] = []
    for source_name, outcome in zip(sources.keys(), outcomes):
        if isinstance(outcome, SourceUnavailable):
            errors.append(SearchErrorInfo(source=source_name, message=outcome.message))
        elif isinstance(outcome, BaseException):
            errors.append(SearchErrorInfo(source=source_name, message=str(outcome)))
        else:
            results.extend(outcome)

    if types:
        type_filter = {t.strip() for t in types.split(",") if t.strip()}
        if type_filter:
            results = [r for r in results if r.mediaType in type_filter]

    return SearchResponse(results=results, errors=errors)


class OriginCandidate(BaseModel):
    """A candidate original-work Edition (US-001), from AniList relations."""

    source: Literal["anilist"] = "anilist"
    externalId: str
    mediaType: MediaType
    title: str
    url: str


@router.get("/anilist-origins/{media_id}", response_model=list[OriginCandidate])
async def anilist_origins(media_id: str) -> list[OriginCandidate]:
    adapter = get_anilist_adapter()
    try:
        data = await adapter._graphql(anilist_module._DETAIL_QUERY, {"id": int(media_id)})
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    candidates = adapter.source_candidates(data["Media"])
    return [OriginCandidate.model_validate(c) for c in candidates]
