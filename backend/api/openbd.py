"""`/api/openbd/*` routes (see specs/mvp-core/design.md, "API Design")."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..adapters import openbd
from ..adapters.openbd import OpenBdBook
from ..cache import TTLCache
from ..dto import SourceUnavailable
from ..errors import record_adapter_error

router = APIRouter(prefix="/api/openbd", tags=["openbd"])

_cache: Optional[TTLCache] = None


def get_cache() -> TTLCache:
    """The response cache, opened on first request (not at import)."""
    global _cache
    if _cache is None:
        _cache = TTLCache()
    return _cache


@router.get("/{isbn}", response_model=OpenBdBook)
async def get_book(
    isbn: str, refresh: bool = Query(False, alias="refresh")
) -> OpenBdBook:
    try:
        return await openbd.book(isbn, cache=get_cache(), refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        record_adapter_error(exc.source, exc.message)
        raise HTTPException(status_code=502, detail=exc.message) from exc
