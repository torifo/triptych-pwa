"""openBD API (https://api.openbd.jp/v1/get) adapter.

ISBN-13-only lookup adapter — openBD has no title search, so this module only
exposes `book(isbn)` (see specs/mvp-core/design.md, "Source Adapters").

- User-Agent: `triptych/0.1 (personal tracker)`.
- Rate limit: 2 req/s (openBD has no published limit; kept conservative and
  courteous since it is a free, donation-funded public service).
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from ..cache import TTLCache
from ..dto import SourceUnavailable
from ..ratelimit import TokenBucket

OPENBD_API_URL = "https://api.openbd.jp/v1/get"
USER_AGENT = "triptych/0.1 (personal tracker)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}

DETAIL_TTL_SECONDS = 6 * 60 * 60  # 詳細: 6h

_rate_limiter = TokenBucket(rate=2.0, capacity=2.0)

_DATE_PATTERN = re.compile(r"^(\d{4})(\d{2})?(\d{2})?")


class OpenBdBook(BaseModel):
    """Normalized detail for a single book, keyed by ISBN-13."""

    source: str = "openbd"
    isbn: str
    title: str
    publisher: Optional[str] = None
    pubdate: Optional[str] = None  # YYYY-MM-DD (best-effort; day/month may default to 01)
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


def _normalize_pubdate(raw: Optional[str]) -> Optional[str]:
    """Normalizes openBD's pubdate (YYYY / YYYYMM / YYYYMMDD) to YYYY-MM-DD."""
    if not raw:
        return None
    match = _DATE_PATTERN.match(raw.strip())
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{month or '01'}-{day or '01'}"


def _book_url(isbn: str) -> str:
    return f"https://ndlsearch.ndl.go.jp/books/{isbn}"


def _normalize_book(raw: dict[str, Any]) -> OpenBdBook:
    """Normalizes a single non-null element of the openBD `get` response."""
    summary = raw.get("summary") or {}
    isbn = str(summary.get("isbn"))
    cover = summary.get("cover") or None
    return OpenBdBook(
        isbn=isbn,
        title=summary.get("title", ""),
        publisher=summary.get("publisher") or None,
        pubdate=_normalize_pubdate(summary.get("pubdate")),
        cover=cover,
        url=_book_url(isbn),
    )


async def book(
    isbn: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Optional[TTLCache] = None,
    refresh: bool = False,
) -> OpenBdBook:
    """Fetches and normalizes a single book's metadata by ISBN-13."""
    normalized_isbn = validate_isbn13(isbn)
    cache_key = f"openbd:book:{normalized_isbn}"

    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return OpenBdBook.model_validate(cached)

    await _rate_limiter.acquire()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        try:
            response = await client.get(
                OPENBD_API_URL,
                params={"isbn": normalized_isbn},
                headers=REQUEST_HEADERS,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable("openbd", str(exc)) from exc
        try:
            raw = response.json()
        except ValueError as exc:
            raise SourceUnavailable(
                "openbd", f"invalid JSON response: {exc}"
            ) from exc
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(raw, list) or not raw or raw[0] is None:
        raise SourceUnavailable("openbd", f"isbn not found: {normalized_isbn}")

    detail = _normalize_book(raw[0])
    if cache is not None:
        cache.set(cache_key, detail.model_dump(), DETAIL_TTL_SECONDS)
    return detail
