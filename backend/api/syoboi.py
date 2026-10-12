"""Router for the syoboi calendar adapter (`/api/syoboi/...`).

`GET /api/syoboi/title?tid=` or `?title=` answers at once from the cache, or
with `status="queued"` while the 60 second crawl-delay queue fetches the data
(see `adapters/syoboi.py`). `?refresh=1` drops the cached result and queues it again.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..adapters.syoboi import SyoboiAdapter
from ..dto import SourceUnavailable, SyoboiTitleDTO
from ..errors import record_adapter_error

router = APIRouter(prefix="/api/syoboi", tags=["syoboi"])

_adapter: Optional[SyoboiAdapter] = None


def get_adapter() -> SyoboiAdapter:
    """Returns the process-wide SyoboiAdapter, creating it lazily.

    Lazy singleton so the shared TTL cache / request queue persist across
    requests within one collector process.
    """
    global _adapter
    if _adapter is None:
        _adapter = SyoboiAdapter()
    return _adapter


async def _title_info(
    adapter: SyoboiAdapter, tid: Optional[str], title: Optional[str], refresh: bool
) -> SyoboiTitleDTO:
    try:
        return await adapter.title_info(tid=tid or None, title=title, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        record_adapter_error("syoboi", exc.message)
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.get("/title", response_model=SyoboiTitleDTO)
async def get_title(
    tid: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    refresh: bool = Query(False),
    adapter: SyoboiAdapter = Depends(get_adapter),
) -> SyoboiTitleDTO:
    return await _title_info(adapter, tid, title, refresh)


@router.get("/title/{tid}", response_model=SyoboiTitleDTO)
async def get_title_by_tid(
    tid: str,
    refresh: bool = Query(False),
    adapter: SyoboiAdapter = Depends(get_adapter),
) -> SyoboiTitleDTO:
    return await _title_info(adapter, tid, None, refresh)
