"""`/api/narou/*` routes (see specs/data-coverage/design.md, "Backend エンドポイント")."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..adapters import narou
from ..cache import TTLCache
from ..dto import SourceUnavailable, WebNovelDTO
from ..errors import record_adapter_error

router = APIRouter(prefix="/api/narou", tags=["narou"])

_cache = TTLCache()


@router.get("/novel/{ncode}", response_model=WebNovelDTO)
async def get_novel(
    ncode: str, refresh: bool = Query(False, alias="refresh")
) -> WebNovelDTO:
    try:
        return await narou.novel(ncode, cache=_cache, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        record_adapter_error("narou", exc.message)
        raise HTTPException(status_code=502, detail=exc.message) from exc
