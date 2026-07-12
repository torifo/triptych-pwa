"""`/api/openbd/*` routes (see specs/mvp-core/design.md, "API Design").

Not registered on the app in this task (see tasks.md, Task 2.2) — the router
is wired into `backend/app.py` by a later task.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..adapters import openbd
from ..adapters.openbd import OpenBdBook
from ..cache import TTLCache
from ..dto import SourceUnavailable

router = APIRouter(prefix="/api/openbd", tags=["openbd"])

_cache = TTLCache()


@router.get("/{isbn}", response_model=OpenBdBook)
async def get_book(
    isbn: str, refresh: bool = Query(False, alias="refresh")
) -> OpenBdBook:
    try:
        return await openbd.book(isbn, cache=_cache, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
