"""Router for the syoboi calendar adapter (`/api/syoboi/...`).

Not registered on the main app yet (see specs/mvp-core/tasks.md Task 2.3) —
app.py wiring happens in a later task.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from ..adapters.syoboi import SyoboiAdapter, SyoboiTitleDTO
from ..dto import SourceUnavailable

router = APIRouter(prefix="/api/syoboi", tags=["syoboi"])

_adapter: Optional[SyoboiAdapter] = None


def get_adapter() -> SyoboiAdapter:
    """Returns the process-wide SyoboiAdapter, creating it lazily.

    Lazy singleton so the shared TTL cache / token bucket persist across
    requests within one collector process.
    """
    global _adapter
    if _adapter is None:
        _adapter = SyoboiAdapter()
    return _adapter


@router.get("/title/{tid}", response_model=SyoboiTitleDTO)
async def get_title(tid: str) -> SyoboiTitleDTO:
    adapter = get_adapter()
    try:
        return await adapter.title(tid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
