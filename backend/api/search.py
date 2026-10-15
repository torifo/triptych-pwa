"""`/api/search` — cross-source search (FR-006, US-104).

Runs the AniList, なろう, カクヨム and NDL (books) searches in parallel via
`asyncio.gather` and merges the results into a single list. A source that
raises `SourceUnavailable` (or any other error) is skipped rather than
failing the whole request — the endpoint always returns whatever partial
results are available, plus per-source error info (see
specs/mvp-core/design.md, "API Design (Collector)" / US-005). Every
`SourceUnavailable` is also recorded for `/api/health`.

When `q` is itself a pointer to one work — a なろう URL or ncode, a カクヨム URL,
an AniList URL, or an ISBN — nothing is searched: the work is resolved
directly and returned as the single `direct=True` result.

Also exposes `/api/search/anilist-origins/{media_id}`, which surfaces the
AniList SOURCE/ADAPTATION relations (US-001 原作候補) for a registered anime
result. This lives here rather than on `backend/api/anilist.py` /
`MediaDetailDTO` (out of scope for this task) — it reuses
`AniListAdapter.source_candidates()` against the raw detail-query node
directly, since `AniListAdapter.media()` normalizes relations away.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any, Awaitable, Callable, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..adapters import anilist as anilist_module
from ..adapters import narou as narou_adapter
from ..adapters import ndl as ndl_adapter
from ..adapters import openbd as openbd_adapter
from ..adapters.anilist import AniListAdapter
from ..cache import TTLCache
from ..dto import MediaType, SearchResult, SourceUnavailable
from ..errors import record_adapter_error
from .kakuyomu import get_adapter as get_kakuyomu_adapter

router = APIRouter(prefix="/api/search", tags=["search"])

_anilist_adapter: Optional[AniListAdapter] = None
_cache = TTLCache()


def get_anilist_adapter() -> AniListAdapter:
    global _anilist_adapter
    if _anilist_adapter is None:
        _anilist_adapter = AniListAdapter()
    return _anilist_adapter


class SearchErrorInfo(BaseModel):
    """Per-source failure recorded when a source is skipped (see US-005)."""

    source: str
    message: str


class SearchResponse(BaseModel):
    results: list[SearchResult]
    errors: list[SearchErrorInfo] = Field(default_factory=list)


# -- direct resolution (URL / ncode / ISBN) ---------------------------------

DirectKind = Literal["narou", "kakuyomu", "anilist", "isbn"]

_NAROU_URL_RE = re.compile(
    r"^(?:https?://)?ncode\.syosetu\.com/(n\d{4}[a-z]{1,2})(?:[/?#]|$)", re.IGNORECASE
)
_NCODE_RE = re.compile(r"^n\d{4}[a-z]{1,2}$", re.IGNORECASE)
_KAKUYOMU_URL_RE = re.compile(
    r"^(?:https?://)?kakuyomu\.jp/works/(\d+)(?:[/?#]|$)", re.IGNORECASE
)
_ANILIST_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?anilist\.co/(?:anime|manga)/(\d+)(?:[/?#]|$)",
    re.IGNORECASE,
)
_ISBN_PREFIX_RE = re.compile(r"^ISBN(?:-?1[03])?\s*[:：]?\s*", re.IGNORECASE)
_ISBN_CHARS_RE = re.compile(r"^[0-9Xx\- ]+$")


def _isbn13_check_digit(first12: str) -> int:
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(first12))
    return (10 - total % 10) % 10


def _normalize_isbn(text: str) -> Optional[str]:
    """Hyphen-stripped, checksum-valid ISBN-13 for an ISBN-10/13 input, else None."""
    candidate = _ISBN_PREFIX_RE.sub("", text.strip())
    if not candidate or not _ISBN_CHARS_RE.match(candidate):
        return None
    compact = re.sub(r"[-\s]", "", candidate).upper()

    if len(compact) == 13 and compact.isdigit():
        if _isbn13_check_digit(compact[:12]) != int(compact[12]):
            return None
        return compact

    if len(compact) == 10 and compact[:9].isdigit():
        last = 10 if compact[9] == "X" else (int(compact[9]) if compact[9].isdigit() else -1)
        if last < 0:
            return None
        total = sum(int(ch) * (10 - i) for i, ch in enumerate(compact[:9])) + last
        if total % 11 != 0:
            return None
        first12 = "978" + compact[:9]
        return first12 + str(_isbn13_check_digit(first12))

    return None


def parse_direct_query(q: str) -> Optional[tuple[DirectKind, str]]:
    """Recognizes a query that points at exactly one work.

    Returns (kind, identifier): the ncode (lower-case), the カクヨム work id,
    the AniList media id, or the ISBN-13. None means "run a normal search".
    """
    text = q.strip()
    if not text:
        return None
    if match := _NAROU_URL_RE.match(text):
        return "narou", match.group(1).lower()
    if _NCODE_RE.match(text):
        return "narou", text.lower()
    if match := _KAKUYOMU_URL_RE.match(text):
        return "kakuyomu", match.group(1)
    if match := _ANILIST_URL_RE.match(text):
        return "anilist", match.group(1)
    if isbn := _normalize_isbn(text):
        return "isbn", isbn
    return None


async def _resolve_direct(
    kind: DirectKind, identifier: str, refresh: bool
) -> SearchResult:
    """Resolves one work into a `direct=True` SearchResult."""
    if kind == "narou":
        novel = await narou_adapter.novel(identifier, cache=_cache, refresh=refresh)
        return SearchResult(
            source="narou",
            externalId=novel.externalId,
            mediaType="web-novel",
            title=novel.title,
            url=novel.url,
            subtitle=novel.author,
            direct=True,
        )
    if kind == "kakuyomu":
        work = await get_kakuyomu_adapter().work(identifier, refresh=refresh)
        return SearchResult(
            source="kakuyomu",
            externalId=work.externalId,
            mediaType="web-novel",
            title=work.title,
            url=work.url,
            subtitle=work.author,
            direct=True,
        )
    if kind == "anilist":
        media = await _call_with_refresh(get_anilist_adapter().media, identifier, refresh)
        return SearchResult(
            source="anilist",
            externalId=media.externalId,
            mediaType=media.mediaType,
            title=media.title,
            cover=media.cover,
            url=media.url,
            direct=True,
        )
    book = await openbd_adapter.book(identifier, cache=_cache, refresh=refresh)
    return SearchResult(
        source="openbd",
        externalId=identifier,
        mediaType="light-novel",
        title=book.title,
        cover=book.cover,
        url=book.url,
        subtitle=book.publisher,
        direct=True,
    )


# -- fan-out search -----------------------------------------------------------


async def _call_with_refresh(fn: Callable[..., Awaitable[Any]], arg: Any, refresh: bool) -> Any:
    """Calls `fn(arg)`, adding `refresh=True` only if `fn` accepts it.

    The AniList and NDL adapters belong to other tasks; this keeps the router
    working whether or not their methods take a `refresh` argument.
    """
    if refresh and "refresh" in inspect.signature(fn).parameters:
        return await fn(arg, refresh=True)
    return await fn(arg)


async def _call_search(
    fn: Callable[..., Awaitable[Any]], q: str, refresh: bool
) -> list[SearchResult]:
    items = await _call_with_refresh(fn, q, refresh)
    return [
        item if isinstance(item, SearchResult) else SearchResult.model_validate(item)
        for item in items
    ]


async def _search_anilist(q: str, refresh: bool) -> list[SearchResult]:
    return await _call_search(get_anilist_adapter().search, q, refresh)


async def _search_narou(q: str, refresh: bool) -> list[SearchResult]:
    return await narou_adapter.search(q, cache=_cache, refresh=refresh)


async def _search_kakuyomu(q: str, refresh: bool) -> list[SearchResult]:
    return await get_kakuyomu_adapter().search(q, refresh=refresh)


async def _search_ndl(q: str, refresh: bool) -> list[SearchResult]:
    return await _call_search(ndl_adapter.search_books, q, refresh)


def _error_message(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


# 1つのソースを待つ上限（秒）。NDL は混んでいると数十秒かかり、全体の結果まで止めてしまうので打ち切る。
SEARCH_SOURCE_TIMEOUT_SECONDS = 12.0


def _discard_result(task: "asyncio.Task[Any]") -> None:
    """Retrieve a background search's outcome so an error is not reported as never retrieved."""
    if not task.cancelled():
        task.exception()


async def _within(source: str, search: Awaitable[list[SearchResult]]) -> list[SearchResult]:
    """Wait for one source up to SEARCH_SOURCE_TIMEOUT_SECONDS.

    On timeout the search keeps running in the background (shielded) so its
    adapter can still fill the cache; the next identical query is then fast.
    """
    task = asyncio.ensure_future(search)
    try:
        return await asyncio.wait_for(asyncio.shield(task), SEARCH_SOURCE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        task.add_done_callback(_discard_result)
        raise SourceUnavailable(
            source, f"{SEARCH_SOURCE_TIMEOUT_SECONDS:g}秒以内に応答がありませんでした（裏で取得を続けます）"
        ) from exc


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    types: Optional[str] = Query(None, description="comma-separated mediaType filter"),
    refresh: bool = Query(False, alias="refresh"),
) -> SearchResponse:
    direct = parse_direct_query(q)
    if direct is not None:
        # A pointer to one work: resolve it and skip the keyword search (and
        # the `types` filter, which would otherwise hide what was pasted).
        kind, identifier = direct
        try:
            hit = await _resolve_direct(kind, identifier, refresh)
        except SourceUnavailable as exc:
            record_adapter_error(exc.source, exc.message)
            return SearchResponse(
                results=[],
                errors=[SearchErrorInfo(source=exc.source, message=exc.message)],
            )
        except ValueError as exc:
            return SearchResponse(
                results=[],
                errors=[SearchErrorInfo(source=kind, message=str(exc))],
            )
        return SearchResponse(results=[hit], errors=[])

    sources = {
        "anilist": _within("anilist", _search_anilist(q, refresh)),
        "narou": _within("narou", _search_narou(q, refresh)),
        "kakuyomu": _within("kakuyomu", _search_kakuyomu(q, refresh)),
        "ndl": _within("ndl", _search_ndl(q, refresh)),
    }
    outcomes = await asyncio.gather(*sources.values(), return_exceptions=True)

    results: list[SearchResult] = []
    errors: list[SearchErrorInfo] = []
    for source_name, outcome in zip(sources.keys(), outcomes):
        if isinstance(outcome, SourceUnavailable):
            record_adapter_error(source_name, outcome.message)  # type: ignore[arg-type]
            errors.append(SearchErrorInfo(source=source_name, message=outcome.message))
        elif isinstance(outcome, BaseException):
            errors.append(
                SearchErrorInfo(source=source_name, message=_error_message(outcome))
            )
        else:
            results.extend(outcome)

    if types:
        type_filter = {t.strip() for t in types.split(",") if t.strip()}
        if type_filter:
            results = [r for r in results if r.mediaType in type_filter]

    return SearchResponse(results=results, errors=errors)


class OriginCandidate(BaseModel):
    """A candidate original-work Edition (US-001), from AniList relations."""

    source: Literal["anilist"] = "anilist"
    externalId: str
    mediaType: MediaType
    title: str
    url: str


@router.get("/anilist-origins/{media_id}", response_model=list[OriginCandidate])
async def anilist_origins(media_id: str) -> list[OriginCandidate]:
    adapter = get_anilist_adapter()
    try:
        data = await adapter._graphql(anilist_module._DETAIL_QUERY, {"id": int(media_id)})
    except SourceUnavailable as exc:
        record_adapter_error("anilist", exc.message)
        raise HTTPException(status_code=502, detail=exc.message) from exc
    candidates = adapter.source_candidates(data["Media"])
    return [OriginCandidate.model_validate(c) for c in candidates]
