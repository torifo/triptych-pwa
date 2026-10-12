"""AniList routes (all accept `?refresh=1` to bypass the response cache).

  GET /api/anilist/media/{id}
  GET /api/anilist/relations/{id}
  GET /api/anilist/resolve-source?title=&kind=novel|manga
  GET /api/anilist/detect?title=

A `SourceUnavailable` from the adapter is recorded for `/api/health` and
answered with 502.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..adapters.anilist import AniListAdapter
from ..dto import (
    DetectionCandidateDTO,
    MediaDetailDTO,
    RelationsDTO,
    SearchResult,
    SourceUnavailable,
)
from ..errors import record_adapter_error

router = APIRouter(prefix="/api/anilist", tags=["anilist"])

_adapter: AniListAdapter | None = None


def get_adapter() -> AniListAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AniListAdapter()
    return _adapter


def _unavailable(exc: SourceUnavailable) -> HTTPException:
    record_adapter_error("anilist", exc.message)
    return HTTPException(status_code=502, detail=exc.message)


@router.get("/media/{media_id}", response_model=MediaDetailDTO)
async def get_media(media_id: int, refresh: bool = Query(False)) -> MediaDetailDTO:
    try:
        return await get_adapter().media(media_id, refresh=refresh)
    except SourceUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/relations/{media_id}", response_model=RelationsDTO)
async def get_relations(media_id: int, refresh: bool = Query(False)) -> RelationsDTO:
    try:
        return await get_adapter().relations(media_id, refresh=refresh)
    except SourceUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/resolve-source", response_model=list[SearchResult])
async def resolve_source(
    title: str = Query(..., min_length=1),
    kind: Literal["novel", "manga"] = Query(...),
    refresh: bool = Query(False),
) -> list[SearchResult]:
    try:
        return await get_adapter().resolve_source(title, kind, refresh=refresh)
    except SourceUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/detect", response_model=list[DetectionCandidateDTO])
async def detect(
    title: str = Query(..., min_length=1), refresh: bool = Query(False)
) -> list[DetectionCandidateDTO]:
    try:
        return await get_adapter().detect(title, refresh=refresh)
    except SourceUnavailable as exc:
        raise _unavailable(exc) from exc
