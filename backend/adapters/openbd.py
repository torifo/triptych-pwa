"""openBD API (https://api.openbd.jp/v1/get) adapter.

ISBN-13-only lookup adapter — openBD has no title search. It exposes:

- `book(isbn)`: one book; raises `SourceUnavailable` when openBD has no record.
- `books(isbns)`: several books in one `/v1/get?isbn=a,b,c` request; books
  openBD does not know map to `None` (used by the NDL adapter to sharpen
  month/year-only release dates without one request per volume).

`pubdate` is `YYYYMMDD` / `YYYYMM` / `YYYY` (sometimes empty). It is returned
as a sortable YYYY-MM-DD together with its real precision
(`pubdatePrecision`), so a month-only date is never shown as the 1st.

- User-Agent: `triptych/0.1 (personal tracker)`.
- Rate limit: 2 req/s (openBD has no published limit; kept conservative and
  courteous since it is a free, donation-funded public service).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

import httpx
from pydantic import BaseModel

from ..cache import TTLCache
from ..dto import SourceUnavailable
from ..ratelimit import TokenBucket
from ..timeutil import DatePrecision, partial_date

OPENBD_API_URL = "https://api.openbd.jp/v1/get"
USER_AGENT = "triptych/0.1 (personal tracker)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}

DETAIL_TTL_SECONDS = 6 * 60 * 60  # 詳細: 6h（未収録の記録も同じ期間だけ覚える）
MAX_BATCH = 100  # ISBNs per `/v1/get` request (keeps the URL short)

_rate_limiter = TokenBucket(rate=2.0, capacity=2.0)

_PUBDATE_PATTERN = re.compile(r"^(\d{4})(?:[-/.]?(\d{2}))?(?:[-/.]?(\d{2}))?")


class OpenBdBook(BaseModel):
    """Normalized detail for a single book, keyed by ISBN-13."""

    source: str = "openbd"
    isbn: str
    title: str
    publisher: Optional[str] = None
    pubdate: Optional[str] = None  # YYYY-MM-DD; month/year precision fills 01
    pubdatePrecision: Optional[DatePrecision] = None
    cover: Optional[str] = None
    url: str


def validate_isbn13(isbn: str) -> str:
    """Validates ISBN-13 format + checksum. Raises ValueError if invalid.

    Returns the normalized (hyphen-stripped) 13-digit ISBN.
    """
    digits = re.sub(r"[-\s]", "", isbn)
    if len(digits) != 13 or not digits.isdigit():
        raise ValueError(f"invalid ISBN-13: {isbn!r}")

    total = 0
    for i, ch in enumerate(digits):
        weight = 1 if i % 2 == 0 else 3
        total += int(ch) * weight
    if total % 10 != 0:
        raise ValueError(f"invalid ISBN-13 checksum: {isbn!r}")

    return digits


def parse_pubdate(raw: Optional[str]) -> Optional[tuple[str, DatePrecision]]:
    """Parses openBD's pubdate into (YYYY-MM-DD, precision).

    '20261026' -> day, '202610' -> month, '2026' -> year. A zero month/day
    ('20261000') lowers the precision instead of inventing a date.
    """
    if not raw:
        return None
    match = _PUBDATE_PATTERN.match(raw.strip())
    if not match:
        return None
    year, month, day = (int(part) if part else None for part in match.groups())
    if not month:
        return partial_date(year)
    if not day:
        return partial_date(year, month)
    return partial_date(year, month, day)


def _normalize_pubdate(raw: Optional[str]) -> Optional[str]:
    """Sortable YYYY-MM-DD only (kept for callers that ignore precision)."""
    parsed = parse_pubdate(raw)
    return parsed[0] if parsed else None


def _book_url(isbn: str) -> str:
    return f"https://ndlsearch.ndl.go.jp/books/{isbn}"


def _book_cache_key(isbn: str) -> str:
    return f"openbd:book:v2:{isbn}"


def _missing_cache_key(isbn: str) -> str:
    return f"openbd:missing:v2:{isbn}"


def _normalize_book(raw: dict[str, Any], fallback_isbn: Optional[str] = None) -> OpenBdBook:
    """Normalizes a single non-null element of the openBD `get` response."""
    summary = raw.get("summary") or {}
    isbn = str(summary.get("isbn") or fallback_isbn or "")
    cover = summary.get("cover") or None
    parsed = parse_pubdate(summary.get("pubdate"))
    return OpenBdBook(
        isbn=isbn,
        title=summary.get("title", ""),
        publisher=summary.get("publisher") or None,
        pubdate=parsed[0] if parsed else None,
        pubdatePrecision=parsed[1] if parsed else None,
        cover=cover,
        url=_book_url(isbn),
    )


async def _get(client: httpx.AsyncClient, isbn_param: str) -> list[Any]:
    """One `/v1/get` call. Returns the raw JSON list (elements may be null)."""
    await _rate_limiter.acquire()
    try:
        response = await client.get(
            OPENBD_API_URL,
            params={"isbn": isbn_param},
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceUnavailable("openbd", str(exc)) from exc
    try:
        raw = response.json()
    except ValueError as exc:
        raise SourceUnavailable("openbd", f"invalid JSON response: {exc}") from exc
    if not isinstance(raw, list):
        raise SourceUnavailable("openbd", "unexpected response shape")
    return raw


async def book(
    isbn: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Optional[TTLCache] = None,
    refresh: bool = False,
) -> OpenBdBook:
    """Fetches and normalizes a single book's metadata by ISBN-13."""
    normalized_isbn = validate_isbn13(isbn)
    cache_key = _book_cache_key(normalized_isbn)

    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return OpenBdBook.model_validate(cached)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        raw = await _get(client, normalized_isbn)
    finally:
        if owns_client:
            await client.aclose()

    if not raw or not raw[0]:
        raise SourceUnavailable("openbd", f"isbn not found: {normalized_isbn}")

    detail = _normalize_book(raw[0], normalized_isbn)
    if cache is not None:
        cache.set(cache_key, detail.model_dump(), DETAIL_TTL_SECONDS)
    return detail


async def books(
    isbns: Iterable[str],
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Optional[TTLCache] = None,
    refresh: bool = False,
) -> dict[str, Optional[OpenBdBook]]:
    """Looks up several ISBN-13s with one `/v1/get?isbn=a,b,c` request.

    Returns {isbn: book or None}; None means openBD has no record. Cached
    hits (including "not in openBD") skip the request; `refresh` ignores the
    cache. Raises ValueError for a malformed ISBN and `SourceUnavailable`
    when openBD cannot be reached.
    """
    wanted: list[str] = []
    for isbn in isbns:
        normalized = validate_isbn13(isbn)
        if normalized not in wanted:
            wanted.append(normalized)

    result: dict[str, Optional[OpenBdBook]] = {}
    pending: list[str] = []
    for isbn in wanted:
        if cache is not None and not refresh:
            cached = cache.get(_book_cache_key(isbn))
            if cached is not None:
                result[isbn] = OpenBdBook.model_validate(cached)
                continue
            if cache.get(_missing_cache_key(isbn)) is not None:
                result[isbn] = None
                continue
        pending.append(isbn)

    if not pending:
        return result

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        for start in range(0, len(pending), MAX_BATCH):
            chunk = pending[start : start + MAX_BATCH]
            raw = await _get(client, ",".join(chunk))
            if len(raw) != len(chunk):
                raise SourceUnavailable(
                    "openbd", f"expected {len(chunk)} records, got {len(raw)}"
                )
            for isbn, item in zip(chunk, raw):
                if not item or not isinstance(item, dict) or not item.get("summary"):
                    result[isbn] = None
                    if cache is not None:
                        cache.set(_missing_cache_key(isbn), True, DETAIL_TTL_SECONDS)
                    continue
                detail = _normalize_book(item, isbn)
                result[isbn] = detail
                if cache is not None:
                    cache.set(_book_cache_key(isbn), detail.model_dump(), DETAIL_TTL_SECONDS)
    finally:
        if owns_client:
            await client.aclose()

    return result
