"""Router for the kakuyomu scraping adapter (`/api/kakuyomu/...`).

Not registered on the main app yet (see specs/mvp-core/tasks.md Task 2.3) —
app.py wiring happens in a later task.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from ..adapters.kakuyomu import KakuyomuAdapter, KakuyomuWorkDTO
from ..dto import SourceUnavailable

router = APIRouter(prefix="/api/kakuyomu", tags=["kakuyomu"])

_adapter: Optional[KakuyomuAdapter] = None


def get_adapter() -> KakuyomuAdapter:
    """Returns the process-wide KakuyomuAdapter, creating it lazily.

    Lazy singleton so the shared TTL cache / token bucket / concurrency
    lock / robots.txt decision persist across requests within one
    collector process.
    """
    global _adapter
    if _adapter is None:
        _adapter = KakuyomuAdapter()
    return _adapter


@router.get("/work/{id}", response_model=KakuyomuWorkDTO)
async def get_work(id: str) -> KakuyomuWorkDTO:
    adapter = get_adapter()
    try:
        return await adapter.work(id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
