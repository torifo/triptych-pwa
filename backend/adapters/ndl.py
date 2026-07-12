"""国会図書館サーチ (NDL Search) OpenSearch API adapter.

Used exclusively to discover new-volume candidates for a series by title;
NDL Search results are then detailed via `openbd.book()` for each ISBN found
(the "2段構え" described in specs/mvp-core/design.md, "Source Adapters").

- User-Agent: `triptych/0.1 (personal tracker)`.
- Rate limit: 1 req/s for the NDL search request itself. Follow-up openBD
  detail calls go through `openbd.book()`, which applies its own rate limit.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from ..cache import TTLCache
from ..dto import ReleaseDTO, SourceUnavailable
from ..ratelimit import TokenBucket
from . import openbd

NDL_API_URL = "https://ndlsearch.ndl.go.jp/api/opensearch"
USER_AGENT = "triptych/0.1 (personal tracker)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}

SEARCH_TTL_SECONDS = 60 * 60  # 検索: 1h
MAX_CANDIDATES = 20  # cap follow-up openBD calls per series() request

_rate_limiter = TokenBucket(rate=1.0, capacity=1.0)

_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "openSearch": "http://a9.com/-/spec/opensearchrss/1.0/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
    "dcterms": "http://purl.org/dc/terms/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
_XSI_TYPE = f"{{{_NS['xsi']}}}type"
_ISBN_TYPES = {"dcndl:ISBN", "dcndl:ISBN13"}

_ISSUED_PATTERN = re.compile(r"^(\d{4})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?")


class NdlCandidate(BaseModel):
    """A single series-volume candidate extracted from NDL Search, pre-openBD."""

    title: str
    volume: Optional[str] = None
    isbn: Optional[str] = None
    issued: Optional[str] = None  # normalized YYYY-MM-DD, best-effort


def _normalize_issued(raw: Optional[str]) -> Optional[str]:
    """Normalizes dcterms:issued (e.g. '2018.11', '2018.1-2020.1') to YYYY-MM-DD."""
    if not raw:
        return None
    match = _ISSUED_PATTERN.match(raw.strip())
    if not match:
        return None
    year, month, day = match.groups()
    if not year:
        return None
    return f"{year}-{(month or '1').zfill(2)}-{(day or '1').zfill(2)}"


def _find_isbn(item: ET.Element) -> Optional[str]:
    for identifier in item.findall("dc:identifier", _NS):
        if identifier.get(_XSI_TYPE) in _ISBN_TYPES and identifier.text:
            return re.sub(r"[-\s]", "", identifier.text.strip())
    return None


def parse_candidates(xml_bytes: bytes) -> list[NdlCandidate]:
    """Parses an NDL Search OpenSearch RSS payload into series-volume candidates.

    Pure/offline: no network access. Items without a title are skipped;
    ISBN/volume/issued are best-effort (a missing ISBN just means the
    candidate can't be detailed via openBD later).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SourceUnavailable("ndl", f"invalid XML response: {exc}") from exc

    channel = root.find("channel")
    if channel is None:
        return []

    candidates: list[NdlCandidate] = []
    for item in channel.findall("item"):
        title_el = item.find("dc:title", _NS)
        title = title_el.text if title_el is not None else None
        if not title:
            continue

        volume_el = item.find("dcndl:volume", _NS)
        volume = volume_el.text if volume_el is not None else None

        issued_el = item.find("dcterms:issued", _NS)
        if issued_el is None:
            issued_el = item.find("dc:date", _NS)
        issued = _normalize_issued(issued_el.text if issued_el is not None else None)

        candidates.append(
            NdlCandidate(
                title=title,
                volume=volume,
                isbn=_find_isbn(item),
                issued=issued,
            )
        )
    return candidates


async def series(
    title: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Optional[TTLCache] = None,
    refresh: bool = False,
) -> list[ReleaseDTO]:
    """Finds series-volume candidates by title, detailed via openBD.

    NDL Search is used purely as a title -> ISBN discovery step; the returned
    ReleaseDTOs carry openBD-detailed titles/dates so the result reflects
    actual book metadata rather than raw NDL Search fields.
    """
    cache_key = f"ndl:search:{title}"

    xml_bytes: Optional[bytes] = None
    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            xml_bytes = cached.encode("utf-8")

    # The client is kept open for both the NDL fetch and the follow-up
    # openBD detail calls below, so it must not be closed until the very end.
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        if xml_bytes is None:
            await _rate_limiter.acquire()
            try:
                response = await client.get(
                    NDL_API_URL, params={"title": title}, headers=REQUEST_HEADERS
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceUnavailable("ndl", str(exc)) from exc

            xml_bytes = response.content
            if cache is not None:
                cache.set(cache_key, xml_bytes.decode("utf-8"), SEARCH_TTL_SECONDS)

        candidates = parse_candidates(xml_bytes)

        releases: list[ReleaseDTO] = []
        seen_isbns: set[str] = set()
        for candidate in candidates:
            if not candidate.isbn or candidate.isbn in seen_isbns:
                continue
            if len(seen_isbns) >= MAX_CANDIDATES:
                break
            seen_isbns.add(candidate.isbn)

            try:
                detail = await openbd.book(
                    candidate.isbn, client=client, cache=cache, refresh=refresh
                )
            except (ValueError, SourceUnavailable):
                # Skip candidates openBD can't detail (bad ISBN, not indexed, etc.)
                continue

            releases.append(
                ReleaseDTO(
                    kind="volume",
                    label=candidate.volume or detail.title,
                    date=detail.pubdate or candidate.issued or "",
                    source="ndl",
                    externalId=candidate.isbn,
                )
            )

        return releases
    finally:
        if owns_client:
            await client.aclose()
