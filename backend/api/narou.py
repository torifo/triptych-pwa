"""`/api/narou/*` routes (see specs/mvp-core/design.md, "API Design").

Not registered on the app in this task (see tasks.md, Task 2.2) — the router
is wired into `backend/app.py` by a later task.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..adapters import narou
from ..adapters.narou import NarouNovelDetail
from ..cache import TTLCache
from ..dto import SourceUnavailable

router = APIRouter(prefix="/api/narou", tags=["narou"])

_cache = TTLCache()


@router.get("/novel/{ncode}", response_model=NarouNovelDetail)
async def get_novel(
    ncode: str, refresh: bool = Query(False, alias="refresh")
) -> NarouNovelDetail:
    try:
        return await narou.novel(ncode, cache=_cache, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
