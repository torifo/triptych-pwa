"""しょぼいカレンダー harvester for the catalog: every title in one request.

Facts verified against the live API (2026-10-14):

- `db.php?Command=TitleLookup&TID=*` answers all ~8100 titles in one XML
  response (~24 MB, gzip on the wire, ~3 s). `<Result><Code>200</Code>` comes first,
  then one `<TitleItem>` per title. Fields used: TID, Title, ShortTitle,
  TitleYomi, TitleEN, Cat, FirstYear / FirstMonth, FirstEndYear / FirstEndMonth,
  Keywords. Empty values are empty elements, never missing ones.
- `Cat` (the site's category; the numbers below are read off the data):

      1  アニメ                    144   current / regular anime        -> kept, format TV
      2  ラジオ                    339   radio shows                    -> dropped
      3  テレビ                    434   non-anime TV                   -> dropped
      4  特撮                      299   live-action hero shows         -> dropped
      5  アニメ関連                220   anime-related programmes       -> dropped
      6  メモ・アンテナ類           12   site notes (TID 21 "*アンテナ…*") -> dropped
      7  OVA                       434   OVA / web anime                -> kept, format OVA
      8  映画                      981   films (anime and live action)  -> kept, format 映画
      10 アニメ(終了/再放送)      5222   finished anime                 -> kept, format TV

  So 6781 of 8085 titles are anime-like, and 6760 are kept: placeholder rows
  whose title starts with `*` (TID 93 `*アニメ特番*`, 1 row) and live-action films
  (a 実写 in the title, e.g. `NANA2(実写版)`, 20 rows) are skipped.
- robots.txt says `Crawl-delay: 60`. The collector (`backend.adapters.syoboi`)
  keeps the time of its last request in the response cache
  (`syoboi:v2:last_request`, a UNIX time in seconds, written before and after
  every request). This harvester waits until 60 s after that time, writes it
  before sending and again when the request is over, so the collector and the
  harvester together stay at one request per 60 s. Retries obey the same wait.

One slice, `all`. The response is parsed as a stream so the 24 MB tree is never built.

`recent=True` (`build --recent`) asks only for the titles edited since the last run:
`TitleLookup&TID=*&LastUpdate=<YYYYMMDD_HHMMSS>-` answers just those (every title has a
`<LastUpdate>`; verified 2026-10-15: 119 titles for a week, mostly Cat 1 = on air, 0.5 MB,
0.2 s). Airing and upcoming shows are edited when their dates, episodes or category change,
so they are in it. When nothing matches the answer is `Code 404`, an empty `<TitleItems>`,
which a diff accepts. The window starts a day before the last finished run (RECENT_MARGIN;
`harvest_state.updated_at` of the `all` slice, JST), and a finished diff moves that time
forward while keeping the row count of the build. Without a finished `all` slice (never
built, a trial run, a failed run) a recent run falls back to the full request.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Optional
from xml.etree import ElementTree

import httpx

from ...adapters.syoboi import (
    CRAWL_DELAY_SECONDS,
    DB_URL,
    LAST_REQUEST_TTL_SECONDS,
    _LAST_REQUEST_KEY as LAST_REQUEST_KEY,
)
from ...cache import TTLCache
from ...timeutil import JST, partial_date
from .. import db
from ..runtime import Clock, HarvestResult, request_with_retry

SOURCE = "syoboi"
SLICE_KEY = "all"
TITLE_URL = "https://cal.syoboi.jp/tid/{tid}"
REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=10.0)  # the body is ~24 MB
ATTEMPTS = 3  # tries for the one request; each one waits out the crawl delay
RECENT_MARGIN = timedelta(days=1)  # a diff starts this long before the last run (clock and edit-time slack)

# Cat -> format. Only these categories are anime-like.
ANIME_CATS: dict[int, str] = {1: "TV", 7: "OVA", 8: "映画", 10: "TV"}

LIVE_ACTION_WORD = "実写"


class SyoboiError(Exception):
    """A response the harvester cannot use (HTTP status, result code, bad XML)."""


# -- parsing --------------------------------------------------------------------


def _text(item: ElementTree.Element, tag: str) -> str:
    child = item.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _number(value: str) -> Optional[int]:
    return int(value) if value.isdigit() else None


def _date(year: str, month: str) -> tuple[Optional[str], Optional[str]]:
    """('2026-07-01', 'month') from FirstYear / FirstMonth; (None, None) when unknown."""
    y, m = _number(year), _number(month)
    if m is not None and not 1 <= m <= 12:
        m = None  # a bogus month still leaves a usable year
    parsed = partial_date(y, m)
    return parsed if parsed else (None, None)


def to_entry(item: ElementTree.Element) -> Optional[db.Entry]:
    """One `<TitleItem>` -> a catalog entry; None for non-anime or placeholder rows."""
    tid = _text(item, "TID")
    title = _text(item, "Title")
    cat = _number(_text(item, "Cat"))
    if not tid.isdigit() or not title or cat not in ANIME_CATS:
        return None
    if title.startswith("*") or LIVE_ACTION_WORD in title:
        return None

    short = _text(item, "ShortTitle")
    start, start_precision = _date(_text(item, "FirstYear"), _text(item, "FirstMonth"))
    end, end_precision = _date(_text(item, "FirstEndYear"), _text(item, "FirstEndMonth"))
    keywords = [k.strip() for k in _text(item, "Keywords").split(",") if k.strip()]
    return db.Entry(
        source=SOURCE,
        external_id=tid,
        kind="anime",
        format=ANIME_CATS[cat],
        title=title,
        title_reading=_text(item, "TitleYomi") or None,
        title_english=_text(item, "TitleEN") or None,
        synonyms=[short] if short and short != title else [],
        start_date=start,
        start_precision=start_precision,
        end_date=end,
        end_precision=end_precision,
        url=TITLE_URL.format(tid=tid),
        extra={"tid": tid, "cat": cat, "keywords": keywords},
    )


def diff_start(last_run: str) -> str:
    """`LastUpdate` start ("20261013_182343") for a diff after a run at `last_run` (ISO 8601, UTC)."""
    when = datetime.fromisoformat(last_run)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when.astimezone(JST) - RECENT_MARGIN).strftime("%Y%m%d_%H%M%S")


def parse_titles(body: bytes, *, allow_empty: bool = False) -> Iterator[db.Entry]:
    """Streams the anime entries of a TitleLookup response (a generator).

    Raises SyoboiError when the result code is not 200 or the XML is broken.
    `allow_empty` also accepts `Code 404` (no title matches), the answer of a diff
    that has nothing new.
    """
    checked = False
    try:
        for event, element in ElementTree.iterparse(io.BytesIO(body), events=("end",)):
            if element.tag == "Result":
                code = _text(element, "Code")
                if code != "200" and not (allow_empty and code == "404"):
                    raise SyoboiError(f"result code {code or '?'}: {_text(element, 'Message')}")
                checked = True
            elif element.tag == "TitleItem":
                entry = to_entry(element)
                element.clear()  # keep memory flat on the 24 MB tree
                if entry is not None:
                    yield entry
    except ElementTree.ParseError as exc:
        raise SyoboiError(f"malformed XML response: {exc}") from exc
    if not checked:
        raise SyoboiError("no <Result> in the response")


# -- crawl delay shared with the collector ------------------------------------------


class CrawlDelay:
    """Waits out `Crawl-delay: 60` against the time the collector keeps in the cache."""

    def __init__(self, cache: TTLCache, clock: Clock, interval: float = CRAWL_DELAY_SECONDS) -> None:
        self.cache = cache
        self.clock = clock
        self.interval = interval

    def last(self) -> Optional[float]:
        stored = self.cache.get(LAST_REQUEST_KEY)
        return float(stored) if isinstance(stored, (int, float)) else None

    def stamp(self) -> None:
        self.cache.set(LAST_REQUEST_KEY, self.clock.now().timestamp(), LAST_REQUEST_TTL_SECONDS)

    async def wait_turn(self, log: Callable[[str], None] = print) -> None:
        """Sleeps until `interval` seconds have passed since the last request (by anyone)."""
        while True:
            last = self.last()
            if last is None:
                return
            # min(): a clock that jumped backwards must not stall us for longer than one interval.
            wait = min(last + self.interval - self.clock.now().timestamp(), self.interval)
            if wait <= 0:
                return
            log(f"  waiting {wait:.0f}s (Crawl-delay {self.interval:.0f}s)")
            await self.clock.sleep(wait)


async def _fetch(
    client: httpx.AsyncClient,
    delay: CrawlDelay,
    *,
    clock: Clock,
    log: Callable[[str], None],
    since: Optional[str] = None,
) -> bytes:
    """The TitleLookup body (only the titles updated since `since`, if given).

    Every attempt waits for its turn; raises SyoboiError at the end.
    """
    params = {"Command": "TitleLookup", "TID": "*"}
    if since is not None:
        params["LastUpdate"] = f"{since}-"
    last_error = "no attempt"
    for attempt in range(1, ATTEMPTS + 1):
        await delay.wait_turn(log)
        delay.stamp()
        try:
            # retries=0: a retry would come after seconds, not after the crawl delay.
            response = await request_with_retry(
                client,
                "GET",
                DB_URL,
                clock=clock,
                retries=0,
                log=log,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.HTTPStatusError as exc:  # 429 / 5xx after request_with_retry gave up
            last_error = f"HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 200:
                return response.content
            last_error = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                raise SyoboiError(last_error)  # not transient
        finally:
            delay.stamp()
        log(f"  attempt {attempt}/{ATTEMPTS} failed: {last_error}")
    raise SyoboiError(last_error)


# -- harvest ----------------------------------------------------------------------------


async def harvest(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    clock: Clock,
    limit: Optional[int] = None,
    log: Callable[[str], None] = print,
    cache: Optional[TTLCache] = None,
    recent: bool = False,
) -> HarvestResult:
    """All anime-like titles into `entries`. `limit` caps the rows of this run.

    `cache` is the collector's response cache (backend/cache.db by default); it
    holds the shared time of the last request. `recent`: only the titles edited
    since the last finished run (the whole list when there is none).
    """
    result = HarvestResult(source=SOURCE)
    state = db.slice_status(conn, SOURCE, SLICE_KEY)
    finished = state is not None and state["status"] == "done"
    if finished and not recent:
        result.slices_skipped = 1
        return result
    since = diff_start(state["updated_at"]) if finished and recent else None

    owns_cache = cache is None
    shared = cache if cache is not None else TTLCache()
    try:
        delay = CrawlDelay(shared, clock)
        try:
            body = await _fetch(client, delay, clock=clock, log=log, since=since)
            entries = []
            for entry in parse_titles(body, allow_empty=since is not None):
                entries.append(entry)
                if limit is not None and len(entries) >= limit:
                    break
        except SyoboiError as exc:
            message = f"{SLICE_KEY}: {exc}"
            db.mark_slice(conn, SOURCE, SLICE_KEY, "error", message=message)
            result.slices_error = 1
            result.errors.append(message)
            log(f"  ! {message}")
            return result
    finally:
        if owns_cache:
            shared.close()

    result.fetched = db.upsert_entries(conn, entries)
    cut = limit is not None and result.fetched >= limit
    if since is not None:
        # A diff keeps the build's row count. A trial run (cut) leaves the slice and its time as
        # they were, so the next diff still starts from the last complete run.
        if not cut:
            db.mark_slice(
                conn,
                SOURCE,
                SLICE_KEY,
                "done",
                fetched=state["fetched"],
                message=f"recent: {result.fetched} titles updated since {since}",
            )
            result.slices_done = 1
        log(f"  {SLICE_KEY}: {result.fetched} anime titles updated since {since}")
        return result
    if cut:
        # Trial run: the slice stays open so a full run fetches everything.
        db.mark_slice(conn, SOURCE, SLICE_KEY, "pending", fetched=result.fetched, message="limit")
    else:
        db.mark_slice(conn, SOURCE, SLICE_KEY, "done", fetched=result.fetched)
        result.slices_done = 1
    log(f"  {SLICE_KEY}: {result.fetched} anime titles")
    return result
