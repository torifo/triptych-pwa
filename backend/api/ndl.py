"""`/api/ndl/*` routes (see specs/mvp-core/design.md, "API Design").

Not registered on the app in this task (see tasks.md, Task 2.2) — the router
is wired into `backend/app.py` by a later task.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..adapters import ndl
from ..cache import TTLCache
from ..dto import ReleaseDTO, SourceUnavailable

router = APIRouter(prefix="/api/ndl", tags=["ndl"])

_cache = TTLCache()


@router.get("/series", response_model=list[ReleaseDTO])
async def get_series(
    title: str = Query(...), refresh: bool = Query(False, alias="refresh")
) -> list[ReleaseDTO]:
    try:
        return await ndl.series(title, cache=_cache, refresh=refresh)
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
