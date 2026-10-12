"""しょぼいカレンダー (syoboi) adapter: broadcast / streaming schedule by title.

cal.syoboi.jp declares `Crawl-delay: 60` in robots.txt, so every request to the
host goes through one queue (`HostQueue`) that lets a single worker send at most
one request per 60 seconds. Callers are never made to wait for it:

- `title_info()` answers from the cache (`status="ok"` / `"not_found"`).
- On a cache miss it starts a background job and answers `status="queued"`
  right away; the job's requests (title search -> channel table -> programs)
  are spread over the queue and the next call finds the result in the cache.

The worker is started lazily on the first enqueue, so no app-level startup hook
is needed. The time of the last request is also kept in the cache, so the delay
is honoured across restarts of the collector.

Requests used (all with `User-Agent: triptych/0.1 (personal tracker)`):

- `json.php?Req=TitleSearch&Search=<title>`        title -> TID
- `db.php?Command=ProgLookup&TID=<tid>&StTime=...` programs from today on
- `db.php?Command=ChLookup&ChID=*`                 channel table (7 day cache)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable, Optional, TypeVar
from xml.etree import ElementTree

import httpx

from ..cache import TTLCache
from ..dto import BroadcastDTO, ReleaseDTO, SourceUnavailable, SyoboiTitleDTO
from ..timeutil import JST, broadcast_day

JSON_URL = "https://cal.syoboi.jp/json.php"
DB_URL = "https://cal.syoboi.jp/db.php"
USER_AGENT = "triptych/0.1 (personal tracker)"

# robots.txt: `Crawl-delay: 60` — one request to the host per 60 seconds.
CRAWL_DELAY_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 30.0

TITLE_TTL_SECONDS = 24 * 60 * 60  # title / program result: 24h
NOT_FOUND_TTL_SECONDS = 6 * 60 * 60  # "no such title" answer: 6h
TID_TITLE_TTL_SECONDS = 30 * 24 * 60 * 60  # TID -> title name
CHANNELS_TTL_SECONDS = 7 * 24 * 60 * 60  # channel table: 7 days
LAST_REQUEST_TTL_SECONDS = 60 * 60

# 失敗したジョブと同じ照会は、この間エラーを返す（再キューしない）。
FAILURE_COOLDOWN_SECONDS = 5 * 60
CHANNELS_RETRY_SECONDS = 10 * 60

MAX_PROGRAMS = 50

# しょぼいカレンダーの TID は正の整数のみ。
TID_RE = re.compile(r"^[0-9]+$")

_CACHE_PREFIX = "syoboi:v2"
_CHANNELS_KEY = f"{_CACHE_PREFIX}:channels"
_LAST_REQUEST_KEY = f"{_CACHE_PREFIX}:last_request"

# ChLookup ChGID: 7 = ネット配信、23 = ABEMA の各チャンネル（1サービスにまとめる）。
STREAMING_GROUP_ID = "7"
ABEMA_GROUP_ID = "23"
ABEMA_SERVICE = "ABEMA"

# ChLookup が未取得・失敗の間の判定表。
DEFAULT_STREAMING_CHANNELS: dict[str, str] = {
    "107": "バンダイチャンネル",
    "132": "ニコニコアニメチャンネル",
    "136": "ニコニコ生放送",
    "165": "ニコニコチャンネル",
    "178": "YouTube",
    "210": "DMM.com",
    "234": "dアニメストア",
    "248": "Amazon Prime Video",
    "256": "Netflix",
    "257": "Hulu",
    "268": "FOD",
    "269": "TVer",
    "274": "Disney+",
}
DEFAULT_ABEMA_CHANNEL_IDS = frozenset(
    str(chid)
    for chid in (
        225, 226, 227, 228, 229, 238, 239, 240, 241, 244, 247,
        249, 250, 252, 270, 276, 277, 278, 279, 280, 281, 282,
    )
)

# chid -> (channel name, ChGID)
ChannelTable = dict[str, tuple[str, str]]

T = TypeVar("T")


def validate_tid(tid: str) -> str:
    """Validates a syoboi TID (numeric string). Raises ValueError if malformed."""
    if not TID_RE.match(tid):
        raise ValueError(f"invalid syoboi TID: {tid!r}")
    return tid


# --- Clock and host queue -------------------------------------------------


class Clock:
    """Wall clock + sleep. Replaced by a fake in tests so nothing really waits."""

    def now(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class HostQueue:
    """FIFO of requests to one host, served by a single worker.

    The worker starts a request only when at least `interval` seconds have
    passed since the previous request *finished* (failed requests count too).
    The time of the last request is mirrored into `cache` so a restarted
    process keeps waiting. The worker task is created on the first `run()`.
    """

    def __init__(
        self,
        interval: float = CRAWL_DELAY_SECONDS,
        clock: Optional[Clock] = None,
        cache: Optional[TTLCache] = None,
    ) -> None:
        self.interval = interval
        self._clock = clock or Clock()
        self._cache = cache
        self._last_request_at: Optional[float] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]]]] = None
        self._worker: Optional[asyncio.Task[None]] = None

    @property
    def worker_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def run(self, factory: Callable[[], Awaitable[T]]) -> T:
        """Queues `factory` (an async callable that sends one request) and awaits it."""
        loop = asyncio.get_running_loop()
        if not self.worker_running or self._loop is not loop:
            self._loop = loop
            self._queue = asyncio.Queue()
            self._worker = loop.create_task(self._serve(self._queue))
        assert self._queue is not None
        future: asyncio.Future[T] = loop.create_future()
        self._queue.put_nowait((factory, future))
        return await future

    async def aclose(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _serve(
        self,
        queue: asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]]],
    ) -> None:
        while True:
            factory, future = await queue.get()
            if future.cancelled():
                continue
            await self._wait_turn()
            self._stamp()
            try:
                result = await factory()
            except asyncio.CancelledError:
                future.cancel()
                raise
            except Exception as exc:  # delivered to the caller, never kills the worker
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)
            finally:
                self._stamp()

    def _stamp(self) -> None:
        now = self._clock.now()
        self._last_request_at = now
        if self._cache is not None:
            self._cache.set(_LAST_REQUEST_KEY, now, LAST_REQUEST_TTL_SECONDS)

    def _last(self) -> Optional[float]:
        last = self._last_request_at
        if self._cache is not None:
            stored = self._cache.get(_LAST_REQUEST_KEY)
            if isinstance(stored, (int, float)):
                last = stored if last is None else max(last, stored)
        return last

    async def _wait_turn(self) -> None:
        while True:
            last = self._last()
            if last is None:
                return
            # min(): a clock that jumped backwards must not stall the worker.
            wait = min(last + self.interval - self._clock.now(), self.interval)
            if wait <= 0:
                return
            await self._clock.sleep(wait)


# --- Parsing ----------------------------------------------------------------


@dataclass(frozen=True)
class TitleCandidate:
    tid: str
    title: str
    short_title: str = ""
    has_programs: bool = False


@dataclass(frozen=True)
class Program:
    chid: str
    start: int  # UNIX seconds
    count: Optional[int] = None


def _xml_text(item: ElementTree.Element, tag: str) -> Optional[str]:
    child = item.find(tag)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _parse_xml(xml_text: str, what: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SourceUnavailable("syoboi", f"{what}: malformed XML response: {exc}") from exc


def _check_result(root: ElementTree.Element, what: str, ok_codes: tuple[str, ...] = ("200",)) -> None:
    result = root.find("Result")
    code = _xml_text(result, "Code") if result is not None else None
    if code not in ok_codes:
        message = _xml_text(result, "Message") if result is not None else None
        raise SourceUnavailable("syoboi", message or f"{what} failed (code={code})")


def parse_title_search(text: str) -> list[TitleCandidate]:
    """Parses `json.php?Req=TitleSearch`. No hit is `{"Titles": null}`."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SourceUnavailable("syoboi", f"TitleSearch: malformed JSON response: {exc}") from exc
    titles = data.get("Titles") if isinstance(data, dict) else None
    if not titles:
        return []
    rows = titles.values() if isinstance(titles, dict) else titles
    candidates: list[TitleCandidate] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("TID") or not row.get("Title"):
            continue
        tid = str(row["TID"])
        if not TID_RE.match(tid):
            continue
        candidates.append(
            TitleCandidate(
                tid=tid,
                title=str(row["Title"]),
                short_title=str(row.get("ShortTitle") or ""),
                has_programs=bool(row.get("Programs")),
            )
        )
    return candidates


def parse_channels(xml_text: str) -> ChannelTable:
    """Parses `db.php?Command=ChLookup` into {chid: (name, ChGID)}."""
    root = _parse_xml(xml_text, "ChLookup")
    _check_result(root, "ChLookup")
    table: ChannelTable = {}
    for item in root.iterfind("./ChItems/ChItem"):
        chid = _xml_text(item, "ChID")
        if chid is None:
            continue
        table[chid] = (_xml_text(item, "ChName") or chid, _xml_text(item, "ChGID") or "")
    if not table:
        raise SourceUnavailable("syoboi", "ChLookup returned no channels")
    return table


def parse_programs(xml_text: str) -> list[Program]:
    """Parses `db.php?Command=ProgLookup`. StTime is JST wall time ('2026-10-16 23:00:00')."""
    root = _parse_xml(xml_text, "ProgLookup")
    _check_result(root, "ProgLookup", ok_codes=("200", "404"))  # 404 = no programs
    programs: list[Program] = []
    for item in root.iterfind("./ProgItems/ProgItem"):
        if _xml_text(item, "Deleted") not in (None, "0"):
            continue
        chid = _xml_text(item, "ChID")
        start_text = _xml_text(item, "StTime")
        if chid is None or start_text is None:
            continue
        try:
            start = int(datetime.strptime(start_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST).timestamp())
        except ValueError:
            continue
        count_text = _xml_text(item, "Count")
        count = int(count_text) if count_text and count_text.isdigit() and int(count_text) > 0 else None
        programs.append(Program(chid=chid, start=start, count=count))
    return programs


# --- Title matching -----------------------------------------------------------

_STRIP_CHARS = re.compile(
    r"[\s()\[\]{}<>「」『』〈〉《》【】（）・:;,.!?~〜～\-–—―‐/\\'\"“”‘’☆★♪♡♥]+"
)
_SEASON_PATTERNS = (
    re.compile(r"第\s*(\d+)\s*(?:期|クール|シーズン|部|章)"),
    re.compile(r"(\d+)\s*(?:st|nd|rd|th)\s*season", re.IGNORECASE),
    re.compile(r"season\s*(\d+)", re.IGNORECASE),
)
_TRAILING_SEASON = re.compile(
    r"[\s(（]*(?:第\s*\d+\s*(?:期|クール|シーズン|部|章)"
    r"|\d+\s*(?:st|nd|rd|th)\s*season|season\s*\d+|final\s*season)[\s)）]*$",
    re.IGNORECASE,
)
MIN_MATCH_SCORE = 0.5


def normalize_title(title: str) -> str:
    """NFKC + case-fold + drop spaces / brackets / punctuation, for comparison."""
    return _STRIP_CHARS.sub("", unicodedata.normalize("NFKC", title)).casefold()


def season_of(title: str) -> int:
    """Season number written in the title ('第3期', '2nd Season'); 1 when absent."""
    text = unicodedata.normalize("NFKC", title)
    for pattern in _SEASON_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return 1


def search_term(title: str) -> str:
    """Title with a trailing season marker removed ('X 第3期' -> 'X').

    The server's search matches the base title and returns every season, which
    is more reliable than guessing how a season is spelled on their side.
    """
    text = unicodedata.normalize("NFKC", title).strip()
    stripped = _TRAILING_SEASON.sub("", text).strip()
    return stripped if len(stripped) >= 2 else text


def _score(query_norm: str, candidate_norm: str) -> float:
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 1.0
    if query_norm in candidate_norm or candidate_norm in query_norm:
        return 0.9 * min(len(query_norm), len(candidate_norm)) / max(len(query_norm), len(candidate_norm))
    return 0.9 * SequenceMatcher(None, query_norm, candidate_norm).ratio()


def pick_title(candidates: list[TitleCandidate], query: str) -> Optional[TitleCandidate]:
    """Best candidate for `query`, or None. A different season number never matches."""
    query_norm = normalize_title(query)
    query_season = season_of(query)
    best: Optional[tuple[tuple[float, bool, int], TitleCandidate]] = None
    for candidate in candidates:
        if season_of(candidate.title) != query_season:
            continue
        score = max(
            _score(query_norm, normalize_title(name))
            for name in (candidate.title, candidate.short_title)
            if name
        )
        if score < MIN_MATCH_SCORE:
            continue
        rank = (score, candidate.has_programs, int(candidate.tid))
        if best is None or rank > best[0]:
            best = (rank, candidate)
    return best[1] if best else None


# --- Channels and the DTO -------------------------------------------------------


@dataclass(frozen=True)
class ChannelInfo:
    name: str
    is_streaming: bool
    service: str  # name shown in the "<service> 配信" label


def classify_channel(chid: str, channels: Optional[ChannelTable]) -> ChannelInfo:
    """Decides whether a channel is a streaming service.

    Uses the ChLookup table (ChGID 7 / 23) when it is known and
    falls back to the built-in ID table otherwise.
    """
    if channels is not None and chid in channels:
        name, gid = channels[chid]
        if gid == ABEMA_GROUP_ID:
            return ChannelInfo(name, True, ABEMA_SERVICE)
        return ChannelInfo(name, gid == STREAMING_GROUP_ID, name)
    if chid in DEFAULT_ABEMA_CHANNEL_IDS:
        return ChannelInfo(ABEMA_SERVICE, True, ABEMA_SERVICE)
    if chid in DEFAULT_STREAMING_CHANNELS:
        name = DEFAULT_STREAMING_CHANNELS[chid]
        return ChannelInfo(name, True, name)
    return ChannelInfo(f"ChID {chid}", False, f"ChID {chid}")


def build_title_dto(
    tid: str,
    title: Optional[str],
    programs: list[Program],
    channels: Optional[ChannelTable],
    today: str,
) -> SyoboiTitleDTO:
    """Programs from `today` (broadcast day, YYYY-MM-DD) on, plus the first slot per streaming service."""
    rows: list[tuple[Program, str, str, ChannelInfo]] = []
    for program in programs:
        day, clock_time = broadcast_day(program.start)
        if day < today:
            continue
        rows.append((program, day, clock_time, classify_channel(program.chid, channels)))
    rows.sort(key=lambda row: (row[0].start, row[0].chid))

    broadcasts = [
        BroadcastDTO(
            channel=info.name,
            isStreaming=info.is_streaming,
            date=day,
            time=clock_time,
            episodeCount=program.count,
        )
        for program, day, clock_time, info in rows[:MAX_PROGRAMS]
    ]

    stream_starts: list[ReleaseDTO] = []
    seen_services: set[str] = set()
    for program, day, clock_time, info in rows:
        if not info.is_streaming or info.service in seen_services:
            continue
        seen_services.add(info.service)
        stream_starts.append(
            ReleaseDTO(
                kind="stream-start",
                label=f"{info.service} 配信",
                date=day,
                precision="day",
                time=clock_time,
                source="syoboi",
                externalId=f"{tid}:{program.chid}",
            )
        )

    return SyoboiTitleDTO(
        status="ok",
        tid=tid,
        title=title,
        programs=broadcasts,
        streamStarts=stream_starts,
    )


# --- Adapter --------------------------------------------------------------------


class SyoboiAdapter:
    """Title -> schedule lookups that never block on the 60 second crawl delay."""

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        cache: Optional[TTLCache] = None,
        clock: Optional[Clock] = None,
        interval: float = CRAWL_DELAY_SECONDS,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._client = client or httpx.AsyncClient()
        self._cache = cache if cache is not None else TTLCache()
        self._clock = clock or Clock()
        self._user_agent = user_agent
        self._host_queue = HostQueue(interval=interval, clock=self._clock, cache=self._cache)
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._failures: dict[str, tuple[str, float]] = {}
        self._channels_lock = asyncio.Lock()
        self._channels_failed_at: Optional[float] = None

    # -- public API --

    async def title_info(
        self,
        tid: Optional[str] = None,
        title: Optional[str] = None,
        refresh: bool = False,
    ) -> SyoboiTitleDTO:
        """Schedule for a TID or a title.

        Returns at once: `ok` / `not_found` from the cache, else `queued` while a
        background job fetches it through the 60 second queue. With `refresh` the
        cached result is dropped first (the TID found for a title is kept), and
        the lookup is queued again. Raises ValueError for a bad TID / no input and
        SourceUnavailable while a recent job for this lookup has failed.
        """
        title = title.strip() if title else None
        if tid is not None:
            validate_tid(tid)
        elif not title or not normalize_title(title):
            raise ValueError("tid or title is required")

        key = f"tid:{tid}" if tid is not None else f"title:{normalize_title(title or '')}"
        if refresh:
            self._invalidate(tid, title)
            self._failures.pop(key, None)
        else:
            cached = self._cached(tid, title)
            if cached is not None:
                return cached
            failure = self._failures.get(key)
            if failure is not None:
                message, failed_at = failure
                if self._clock.now() - failed_at < FAILURE_COOLDOWN_SECONDS:
                    raise SourceUnavailable("syoboi", message)
                del self._failures[key]

        if key not in self._jobs:
            job = self._job_for_tid(tid, title) if tid is not None else self._job_for_title(title or "")
            self._jobs[key] = asyncio.get_running_loop().create_task(self._run_job(key, job))
        return SyoboiTitleDTO(status="queued", tid=tid)

    async def wait_idle(self) -> None:
        """Waits until no background job is running (shutdown / tests)."""
        while self._jobs:
            await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)

    async def aclose(self) -> None:
        """Stops the jobs and the queue worker. The HTTP client stays open."""
        jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await self._host_queue.aclose()

    # -- cache --

    def _tid_key(self, tid: str) -> str:
        return f"{_CACHE_PREFIX}:tid:{tid}"

    def _title_key(self, title: str) -> str:
        return f"{_CACHE_PREFIX}:title:{normalize_title(title)}"

    def _tid_title_key(self, tid: str) -> str:
        return f"{_CACHE_PREFIX}:tidtitle:{tid}"

    def _cached(self, tid: Optional[str], title: Optional[str]) -> Optional[SyoboiTitleDTO]:
        if tid is None:
            mapping = self._cache.get(self._title_key(title or ""))
            if not isinstance(mapping, dict):
                return None
            if mapping.get("notFound"):
                return SyoboiTitleDTO(status="not_found")
            tid = mapping.get("tid")
            if not isinstance(tid, str):
                return None
        data = self._cache.get(self._tid_key(tid))
        if data is None:
            return None
        dto = SyoboiTitleDTO.model_validate(data)
        if dto.title is None:  # looked up by TID before the title was known
            known = self._cache.get(self._tid_title_key(tid))
            dto.title = known if isinstance(known, str) else None
        return dto

    def _invalidate(self, tid: Optional[str], title: Optional[str]) -> None:
        if tid is None:
            mapping = self._cache.get(self._title_key(title or ""))
            if isinstance(mapping, dict) and isinstance(mapping.get("tid"), str):
                tid = mapping["tid"]  # keep the title -> TID mapping
            else:
                self._cache.delete(self._title_key(title or ""))  # includes "not found"
        if tid is not None:
            self._cache.delete(self._tid_key(tid))

    # -- background jobs --

    async def _run_job(self, key: str, job: Awaitable[None]) -> None:
        try:
            await job
            self._failures.pop(key, None)
        except SourceUnavailable as exc:
            self._failures[key] = (exc.message, self._clock.now())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a bug must not vanish with the task
            self._failures[key] = (f"unexpected error: {exc!r}", self._clock.now())
        finally:
            self._jobs.pop(key, None)

    async def _job_for_title(self, title: str) -> None:
        mapping = self._cache.get(self._title_key(title))
        if isinstance(mapping, dict) and isinstance(mapping.get("tid"), str):
            tid, found_title = mapping["tid"], mapping.get("title")
        else:
            text = await self._request(JSON_URL, {"Req": "TitleSearch", "Search": search_term(title)})
            picked = pick_title(parse_title_search(text), title)
            if picked is None:
                self._cache.set(self._title_key(title), {"notFound": True}, NOT_FOUND_TTL_SECONDS)
                return
            tid, found_title = picked.tid, picked.title
            self._cache.set(self._title_key(title), {"tid": tid, "title": found_title}, TITLE_TTL_SECONDS)
        await self._fetch_programs(tid, found_title if isinstance(found_title, str) else None)

    async def _job_for_tid(self, tid: str, title_hint: Optional[str]) -> None:
        known = self._cache.get(self._tid_title_key(tid))
        await self._fetch_programs(tid, title_hint or (known if isinstance(known, str) else None))

    async def _fetch_programs(self, tid: str, title: Optional[str]) -> None:
        if title:
            self._cache.set(self._tid_title_key(tid), title, TID_TITLE_TTL_SECONDS)
        if self._cache.get(self._tid_key(tid)) is not None:
            return  # another lookup has just stored it
        channels = await self._channels()
        today = broadcast_day(int(self._clock.now()))[0]
        text = await self._request(
            DB_URL,
            {"Command": "ProgLookup", "TID": tid, "StTime": f"{today.replace('-', '')}_000000-"},
        )
        dto = build_title_dto(tid, title, parse_programs(text), channels, today)
        self._cache.set(self._tid_key(tid), dto.model_dump(), TITLE_TTL_SECONDS)

    async def _channels(self) -> Optional[ChannelTable]:
        """Channel table from the 7 day cache, else via the queue. None -> use the built-in ID table."""
        async with self._channels_lock:
            cached = self._cache.get(_CHANNELS_KEY)
            if isinstance(cached, dict):
                return {chid: (str(v[0]), str(v[1])) for chid, v in cached.items()}
            now = self._clock.now()
            if self._channels_failed_at is not None and now - self._channels_failed_at < CHANNELS_RETRY_SECONDS:
                return None
            try:
                table = parse_channels(await self._request(DB_URL, {"Command": "ChLookup", "ChID": "*"}))
            except SourceUnavailable:
                self._channels_failed_at = self._clock.now()
                return None
            self._cache.set(_CHANNELS_KEY, {chid: list(v) for chid, v in table.items()}, CHANNELS_TTL_SECONDS)
            return table

    # -- HTTP --

    async def _request(self, url: str, params: dict[str, str]) -> str:
        """One GET to cal.syoboi.jp, through the 60 second queue."""
        return await self._host_queue.run(lambda: self._get(url, params))

    async def _get(self, url: str, params: dict[str, str]) -> str:
        try:
            response = await self._client.get(
                url,
                params=params,
                headers={"User-Agent": self._user_agent},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailable("syoboi", f"request failed: {exc}") from exc
        return response.text
