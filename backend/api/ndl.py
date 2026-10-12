"""`/api/ndl/*` routes (see specs/data-coverage/design.md, "Backend エンドポイント")."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from ..adapters import ndl
from ..dto import NdlSeriesDTO, SourceUnavailable
from ..errors import record_adapter_error

router = APIRouter(prefix="/api/ndl", tags=["ndl"])


@router.get("/series", response_model=NdlSeriesDTO)
async def get_series(
    title: str = Query(..., min_length=1),
    kind: Literal["novel", "manga"] = Query("novel"),
    line: Optional[str] = Query(None, description="NDL line key; '' = 本編"),
    refresh: bool = Query(False, alias="refresh"),
) -> NdlSeriesDTO:
    try:
        return await ndl.series(title, kind, line=line, refresh=refresh)
    except SourceUnavailable as exc:
        record_adapter_error(exc.source, exc.message)
        raise HTTPException(status_code=502, detail=exc.message) from exc
