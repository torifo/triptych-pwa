"""Router for the kakuyomu scraping adapter (`/api/kakuyomu/...`)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..adapters.kakuyomu import KakuyomuAdapter
from ..dto import SourceUnavailable, WebNovelDTO
from ..errors import record_adapter_error

router = APIRouter(prefix="/api/kakuyomu", tags=["kakuyomu"])

_adapter: Optional[KakuyomuAdapter] = None


def get_adapter() -> KakuyomuAdapter:
    """Returns the process-wide KakuyomuAdapter, creating it lazily.

    Lazy singleton so the shared TTL cache / token bucket / concurrency
    lock / robots.txt decision persist across requests within one
    collector process. `/api/search` reuses it so work and search requests
    share one 1 req/s budget.
    """
    global _adapter
    if _adapter is None:
        _adapter = KakuyomuAdapter()
    return _adapter


@router.get("/work/{id}", response_model=WebNovelDTO)
async def get_work(
    id: str, refresh: bool = Query(False, alias="refresh")
) -> WebNovelDTO:
    adapter = get_adapter()
    try:
        return await adapter.work(id, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        record_adapter_error("kakuyomu", exc.message)
        raise HTTPException(status_code=502, detail=exc.message) from exc
