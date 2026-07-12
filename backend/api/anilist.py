"""AniList routes: `/api/anilist/media/{id}` and `/api/anilist/detect`.

Not registered on the app in this task; wiring into `backend/app.py` happens
when the search/detection flows are integrated (see specs/mvp-core/tasks.md,
Task 3.1 / 4.1).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..adapters.anilist import AniListAdapter
from ..dto import DetectionCandidateDTO, MediaDetailDTO, SourceUnavailable

router = APIRouter(prefix="/api/anilist", tags=["anilist"])

_adapter: AniListAdapter | None = None


def get_adapter() -> AniListAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AniListAdapter()
    return _adapter


@router.get("/media/{media_id}", response_model=MediaDetailDTO)
async def get_media(media_id: str) -> MediaDetailDTO:
    try:
        return await get_adapter().media(media_id)
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.get("/detect", response_model=list[DetectionCandidateDTO])
async def detect(title: str = Query(...)) -> list[DetectionCandidateDTO]:
    try:
        return await get_adapter().detect(title)
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
