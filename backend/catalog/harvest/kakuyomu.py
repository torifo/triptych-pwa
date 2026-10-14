"""カクヨム harvester for the catalog: popular works per genre.

カクヨム has no API. The popularity-ordered search page is allowed by robots.txt
(checked against the live file, 2026-10-14: only /shared_drafts/, the read pages,
login and a few forms are disallowed) and carries 20 works per page in the same
`__NEXT_DATA__` Apollo cache that backend/adapters/kakuyomu.py already parses.

Genre pages (verified live):

    https://kakuyomu.jp/search?genre_name=<name>&order=popular[&page=<n>]

`genre_name` is one of the twelve names in GENRES (the "ジャンルから探す" list on
the search page). The server applies it as `genres: ["FANTASY"]` and honors
`page` up to at least 50 (offset 980). A slice is `genre:<name>`, up to
MAX_PAGES pages (1000 works); its `cursor` is the next page number.

Politeness: robots.txt is fetched once per run and checked for the path and
query of every request; 1 req/s (Crawl-delay is honored when robots.txt sets
one for us); one request at a time.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable, Optional
from urllib.parse import urlencode
from urllib.robotparser import RobotFileParser

import httpx

from .. import db
from ..runtime import USER_AGENT, Clock, HarvestResult, Pacer, request_with_retry
from ...adapters.kakuyomu import (
    WORK_ID_RE,
    _extract_next_data,
    _page_state,
    _resolve_ref,
    _work_author,
)
from ...dto import SourceUnavailable
from ...timeutil import jst_date_from_iso

SOURCE = "kakuyomu"
BASE_URL = "https://kakuyomu.jp"
SEARCH_PATH = "/search"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
REQUEST_INTERVAL = 1.0  # seconds between requests (1 req/s)
MAX_PAGES = 50  # 20 works per page -> 1000 works per genre

# (genre_name in the URL, label shown on the site)
GENRES: tuple[tuple[str, str], ...] = (
    ("fantasy", "異世界ファンタジー"),
    ("action", "現代ファンタジー"),
    ("sf", "SF"),
    ("love_story", "恋愛"),
    ("romance", "ラブコメ"),
    ("drama", "現代ドラマ"),
    ("horror", "ホラー"),
    ("mystery", "ミステリー"),
    ("nonfiction", "エッセイ・ノンフィクション"),
    ("history", "歴史・時代・伝奇"),
    ("criticism", "創作論・評論"),
    ("others", "詩・童話・その他"),
)

_SERIAL_STATUS_COMPLETED = "COMPLETED"
_ISBN_PATH_RE = re.compile(r"/publication/entry/(97[89]\d{10})(?!\d)")


class KakuyomuError(Exception):
    """A page the harvester cannot use (robots, status, unexpected structure)."""


def slice_key(genre_name: str) -> str:
    return f"genre:{genre_name}"


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def publication_isbn(entry_path: Any) -> Optional[str]:
    """ISBN-13 from '/publication/entry/9784049521788' (older entries use a short id: None)."""
    match = _ISBN_PATH_RE.search(str(entry_path or ""))
    return match.group(1) if match else None


def to_entry(state: dict[str, Any], work: dict[str, Any], genre_label: str) -> Optional[db.Entry]:
    """One Work node of the search page -> a catalog entry (None when unusable)."""
    work_id = str(work.get("id") or "")
    title = str(work.get("title") or "").strip()
    if not WORK_ID_RE.match(work_id) or not title:
        return None
    author = _work_author(state, work)
    published = jst_date_from_iso(str(work["publishedAt"])) if work.get("publishedAt") else None
    last_episode = work.get("lastEpisodePublishedAt")
    tags = [tag for tag in (work.get("tagLabels") or []) if isinstance(tag, str) and tag]
    has_publication = bool(work.get("hasPublication"))
    return db.Entry(
        source=SOURCE,
        external_id=work_id,
        kind="web-novel",
        title=title,
        creators=[author] if author else [],
        genres=[genre_label],
        tags=tags,
        status="完結" if work.get("serialStatus") == _SERIAL_STATUS_COMPLETED else "連載中",
        episodes=_to_int(work.get("publicEpisodeCount")),
        popularity=_to_int(work.get("totalReviewPoint")),
        start_date=published,
        start_precision="day" if published else None,
        url=f"{BASE_URL}/works/{work_id}",
        extra={
            "lastUpdate": jst_date_from_iso(str(last_episode)) if last_episode else None,
            "hasPublication": has_publication,
            "publicationIsbn": publication_isbn(work.get("publicationEntryPath")),
            "totalCharacterCount": _to_int(work.get("totalCharacterCount")),
        },
    )


def parse_search_page(html: str) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """(apollo state, Work nodes, hasNextPage) of one search page; raises KakuyomuError."""
    data = _extract_next_data(html)
    if data is None:
        raise KakuyomuError("could not locate embedded search data")
    try:
        state = _page_state(data, "search")
    except SourceUnavailable as exc:
        raise KakuyomuError(exc.message) from exc
    root = state.get("ROOT_QUERY")
    connection: Optional[dict[str, Any]] = None
    if isinstance(root, dict):
        for key, value in root.items():
            if key.startswith("searchWorks(") and isinstance(value, dict):
                connection = value
                break
    if connection is None or not isinstance(connection.get("nodes"), list):
        raise KakuyomuError("unexpected search page structure")
    works = [w for w in (_resolve_ref(state, node) for node in connection["nodes"]) if w is not None]
    page_info = connection.get("pageInfo")
    has_next = isinstance(page_info, dict) and page_info.get("hasNextPage") is True
    return state, works, has_next


class RobotsGate:
    """robots.txt, fetched once (a failed fetch is retried on the next check)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        clock: Clock,
        pacer: Pacer,
        log: Callable[[str], None],
    ) -> None:
        self._client = client
        self._clock = clock
        self._pacer = pacer
        self._log = log
        self._parser: Optional[RobotFileParser] = None

    async def check(self, path_and_query: str) -> None:
        if self._parser is None:
            self._parser = await self._load()
        if not self._parser.can_fetch(USER_AGENT, path_and_query):
            raise KakuyomuError(f"robots.txt disallows fetching {path_and_query.split('?', 1)[0]}")

    async def _load(self) -> RobotFileParser:
        try:
            response = await request_with_retry(
                self._client, "GET", ROBOTS_URL, clock=self._clock, pacer=self._pacer, log=self._log
            )
        except httpx.HTTPError as exc:
            raise KakuyomuError(f"could not fetch robots.txt: {exc}") from exc
        if response.status_code != 200:
            raise KakuyomuError(f"could not fetch robots.txt: HTTP {response.status_code}")
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        delay = parser.crawl_delay(USER_AGENT)
        if delay:
            self._pacer.interval = max(self._pacer.interval, float(delay))
        return parser


def _resume_page(cursor: Optional[str]) -> int:
    value = _to_int(cursor)
    if value is None or not 1 <= value <= MAX_PAGES:
        return 1
    return value


async def _harvest_genre(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    genre_name: str,
    genre_label: str,
    *,
    clock: Clock,
    pacer: Pacer,
    robots: RobotsGate,
    limit: Optional[int],
    result: HarvestResult,
    log: Callable[[str], None],
) -> bool:
    """Harvests one genre slice. Returns False when the run's `limit` was reached."""
    key = slice_key(genre_name)
    state = db.slice_status(conn, SOURCE, key)
    page = _resume_page(state["cursor"]) if state is not None else 1
    total = state["fetched"] if state is not None and page > 1 else 0
    seen: set[str] = set()
    log(f"  {key} {genre_label} (from page {page})")

    while True:
        params = {"genre_name": genre_name, "order": "popular"}
        if page > 1:
            params["page"] = str(page)
        try:
            await robots.check(f"{SEARCH_PATH}?{urlencode(params)}")
            response = await request_with_retry(
                client,
                "GET",
                f"{BASE_URL}{SEARCH_PATH}",
                clock=clock,
                pacer=pacer,
                log=log,
                params=params,
            )
            if response.status_code != 200:
                raise KakuyomuError(f"HTTP {response.status_code}")
            apollo, works, has_next = parse_search_page(response.text)
        except (KakuyomuError, httpx.HTTPError) as exc:
            message = f"{key} page={page}: {type(exc).__name__}: {exc}"
            db.mark_slice(conn, SOURCE, key, "error", fetched=total, cursor=str(page), message=message)
            result.slices_error += 1
            result.errors.append(message)
            log(f"  ! {message}")
            return True

        entries: list[db.Entry] = []
        for work in works:
            entry = to_entry(apollo, work, genre_label)
            if entry is None or entry.external_id in seen:
                continue
            seen.add(entry.external_id)
            entries.append(entry)
        truncated = False
        if limit is not None and len(entries) > limit - result.fetched:
            entries = entries[: limit - result.fetched]
            truncated = True
        db.upsert_entries(conn, entries)
        result.fetched += len(entries)
        total += len(entries)
        log(f"    page={page}: {len(entries)} rows (slice total {total})")

        if truncated:
            # Part of this page was left out: fetch it again on the next run.
            db.mark_slice(conn, SOURCE, key, "pending", fetched=total, cursor=str(page))
            return False
        if not has_next or not works or page >= MAX_PAGES:
            db.mark_slice(conn, SOURCE, key, "done", fetched=total)
            result.slices_done += 1
            return True
        db.mark_slice(conn, SOURCE, key, "pending", fetched=total, cursor=str(page + 1))
        if limit is not None and result.fetched >= limit:
            return False
        page += 1


async def harvest(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    clock: Clock,
    limit: Optional[int] = None,
    log: Callable[[str], None] = print,
) -> HarvestResult:
    """Popular works of every genre into `entries`. `limit` caps the rows of this run."""
    result = HarvestResult(source=SOURCE)
    pacer = Pacer(REQUEST_INTERVAL, clock)
    robots = RobotsGate(client, clock, pacer, log)
    for genre_name, genre_label in GENRES:
        if limit is not None and result.fetched >= limit:
            break
        if db.is_slice_done(conn, SOURCE, slice_key(genre_name)):
            result.slices_skipped += 1
            continue
        keep_going = await _harvest_genre(
            conn,
            client,
            genre_name,
            genre_label,
            clock=clock,
            pacer=pacer,
            robots=robots,
            limit=limit,
            result=result,
            log=log,
        )
        if not keep_going:
            break
    return result
