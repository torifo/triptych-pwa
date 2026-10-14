"""AniList harvester: every Japanese anime / manga / light novel into the catalog.

AniList cannot page deeper than 5000 results per search (page 100 x perPage
50) and has no `id_greater`, so the catalog is cut into slices that each stay
below that wall:

  per type (ANIME, MANGA; MANGA covers NOVEL / MANGA / ONE_SHOT):
    <1960          startDate_greater: 10000, startDate_lesser: 19600000
    1960 .. now+3  startDate_like: "YYYY%"            (one slice per year)
                   -> a year that still has a full page 100 is split into
                      startDate_like: "YYYYMM%" (one slice per month)
    >now+3         startDate_greater: <now+3>1231     (announced far ahead)
    nodate         startDate_lesser: 10000            (no start date at all)
  then, per type, the 2000 most popular titles (`sort: POPULARITY_DESC`), which
  is a safety net on top of the dated slices.

`startDate_lesser: 19600000` alone also matches works without any start date
(AniList treats a missing date as 0), so dated and dateless works are fetched
as separate slices; otherwise MANGA could run past the 5000 wall.

Slice keys: `ANIME:<1960`, `ANIME:2024`, `ANIME:202404`, `ANIME:>2029`,
`MANGA:nodate`, `MANGA:pop`.

Every page is committed on its own, and the slice's progress is kept in
`harvest_state`, so an interrupted or failed slice resumes from its `cursor`
(the page to fetch next). Finished slices are skipped on the next run.

Pacing: one request per 3 s (20 req/min, under AniList's 30) and, whenever the
response says 3 or fewer requests remain, a wait until `X-RateLimit-Reset`.

`extra` keys written: `idMal`, `streaming` (STREAMING external link site
names) and `nextAiringEpisode` (`{"date", "time", "episode"}` in broadcast-day
notation, see `backend.timeutil.broadcast_day`).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from .. import db
from ..runtime import Clock, HarvestResult, Pacer, request_with_retry
from ...timeutil import broadcast_day, partial_date

SOURCE = "anilist"
ENDPOINT = "https://graphql.anilist.co"

PER_PAGE = 50
MAX_PAGES = 100  # AniList refuses anything deeper than 100 pages x 50
MAX_ITEMS = MAX_PAGES * PER_PAGE
POP_PAGES = 40  # popularity top 2000
FIRST_YEAR = 1960
NO_DATE = 10000  # FuzzyDateInt 0001-00-00: below it only works without a start date remain
YEARS_AHEAD = 3

PACE_SECONDS = 3.0  # 20 req/min
LOW_REMAINING = 3
DEFAULT_RESET_WAIT = 60.0
MAX_RESET_WAIT = 120.0
RESET_MARGIN = 1.0

TYPES = ("ANIME", "MANGA")
SPLIT_CURSOR = "split"  # year slice that moved on to its twelve month slices

MAX_TAGS = 8
MIN_TAG_RANK = 60
MAX_RECORDED_ERRORS = 50

_MEDIA_FIELDS = """
  id idMal type format
  status(version: 2)
  source(version: 3)
  season seasonYear episodes chapters volumes popularity averageScore genres
  tags { name rank isAdult }
  title { native romaji english }
  synonyms
  startDate { year month day }
  endDate { year month day }
  studios(isMain: true) { nodes { name } }
  staff(perPage: 3, sort: RELEVANCE) { edges { role node { name { native full } } } }
  externalLinks { site type }
  coverImage { medium }
  siteUrl
  nextAiringEpisode { airingAt episode }
  relations { edges { relationType(version: 2) node { id type } } }
"""

def build_query(
    *, start_like: bool = False, start_greater: bool = False, start_lesser: bool = False
) -> str:
    """The page query; only the filters a slice uses are declared (AniList rejects null filters)."""
    declarations = ["$page: Int", "$type: MediaType", "$sort: [MediaSort]"]
    filters = ['countryOfOrigin: "JP"', "isAdult: false", "sort: $sort"]
    if start_like:
        declarations.append("$startLike: String")
        filters.append("startDate_like: $startLike")
    if start_greater:
        declarations.append("$startGreater: FuzzyDateInt")
        filters.append("startDate_greater: $startGreater")
    if start_lesser:
        declarations.append("$startLesser: FuzzyDateInt")
        filters.append("startDate_lesser: $startLesser")
    return (
        f"query ({', '.join(declarations)}) {{\n"
        f"  Page(page: $page, perPage: {PER_PAGE}) {{\n"
        "    pageInfo { total currentPage lastPage hasNextPage }\n"
        f"    media(type: $type, {', '.join(filters)}) {{{_MEDIA_FIELDS}}}\n"
        "  }\n"
        "}\n"
    )


class AniListError(Exception):
    """A response AniList sent that cannot be used (HTTP 4xx, GraphQL errors)."""


# --------------------------------------------------------------------------- slices


@dataclass(frozen=True)
class Slice:
    key: str
    type: str
    start_like: Optional[str] = None
    start_greater: Optional[int] = None
    start_lesser: Optional[int] = None
    sort: str = "ID"
    max_pages: int = MAX_PAGES
    year: Optional[int] = None  # set on a year slice: it may split into months

    def variables(self, page: int) -> dict[str, Any]:
        variables: dict[str, Any] = {"page": page, "type": self.type, "sort": [self.sort]}
        if self.start_like is not None:
            variables["startLike"] = self.start_like
        if self.start_greater is not None:
            variables["startGreater"] = self.start_greater
        if self.start_lesser is not None:
            variables["startLesser"] = self.start_lesser
        return variables

    @property
    def query(self) -> str:
        return build_query(
            start_like=self.start_like is not None,
            start_greater=self.start_greater is not None,
            start_lesser=self.start_lesser is not None,
        )

    def month(self, month: int) -> "Slice":
        assert self.year is not None
        stamp = f"{self.year}{month:02d}"
        return Slice(key=f"{self.type}:{stamp}", type=self.type, start_like=f"{stamp}%")


def plan_slices(this_year: int) -> list[Slice]:
    """Dated slices of both types first, then the popularity top of each type."""
    last_year = this_year + YEARS_AHEAD
    slices: list[Slice] = []
    for media_type in TYPES:
        slices.append(
            Slice(
                key=f"{media_type}:<{FIRST_YEAR}",
                type=media_type,
                start_greater=NO_DATE,
                start_lesser=FIRST_YEAR * 10000,
            )
        )
        for year in range(FIRST_YEAR, last_year + 1):
            slices.append(
                Slice(key=f"{media_type}:{year}", type=media_type, start_like=f"{year}%", year=year)
            )
        slices.append(
            Slice(key=f"{media_type}:>{last_year}", type=media_type, start_greater=last_year * 10000 + 1231)
        )
        slices.append(Slice(key=f"{media_type}:nodate", type=media_type, start_lesser=NO_DATE))
    for media_type in TYPES:
        slices.append(
            Slice(
                key=f"{media_type}:pop",
                type=media_type,
                sort="POPULARITY_DESC",
                max_pages=POP_PAGES,
            )
        )
    return slices


# --------------------------------------------------------------------------- conversion

_CREATOR_ROLE = re.compile(r"\b(?:story|art|artist|original|illustration|author)\b", re.IGNORECASE)


def _kind(media: dict[str, Any]) -> str:
    if media.get("type") == "ANIME":
        return "anime"
    return "novel" if media.get("format") == "NOVEL" else "manga"


def _date(parts: Optional[dict[str, Any]]) -> Optional[tuple[str, str]]:
    if not parts:
        return None
    return partial_date(parts.get("year"), parts.get("month"), parts.get("day"))


def _tags(media: dict[str, Any]) -> list[str]:
    ranked = [
        tag
        for tag in media.get("tags") or []
        if tag.get("name") and (tag.get("rank") or 0) >= MIN_TAG_RANK and not tag.get("isAdult")
    ]
    ranked.sort(key=lambda tag: tag["rank"], reverse=True)  # stable: ties keep AniList order
    return [tag["name"] for tag in ranked[:MAX_TAGS]]


def _studios(media: dict[str, Any]) -> list[str]:
    nodes = ((media.get("studios") or {}).get("nodes")) or []
    return [node["name"] for node in nodes if node and node.get("name")]


def _creators(media: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for edge in ((media.get("staff") or {}).get("edges")) or []:
        if not _CREATOR_ROLE.search(edge.get("role") or ""):
            continue
        name = ((edge.get("node") or {}).get("name")) or {}
        value = name.get("native") or name.get("full")
        if value and value not in names:
            names.append(value)
    return names


def _extra(media: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if media.get("idMal") is not None:
        extra["idMal"] = media["idMal"]
    streaming: list[str] = []
    for link in media.get("externalLinks") or []:
        site = link.get("site")
        if link.get("type") == "STREAMING" and site and site not in streaming:
            streaming.append(site)
    if streaming:
        extra["streaming"] = streaming
    upcoming = media.get("nextAiringEpisode")
    if upcoming and upcoming.get("airingAt") is not None:
        day, clock_time = broadcast_day(int(upcoming["airingAt"]))
        extra["nextAiringEpisode"] = {
            "date": day,
            "time": clock_time,
            "episode": upcoming.get("episode"),
        }
    return extra


def to_entry(media: dict[str, Any]) -> Optional[db.Entry]:
    """One AniList `Media` -> catalog entry (None when it has no usable title)."""
    title = media.get("title") or {}
    display = title.get("native") or title.get("romaji") or title.get("english")
    if not display:
        return None
    kind = _kind(media)
    start = _date(media.get("startDate"))
    end = _date(media.get("endDate"))
    return db.Entry(
        source=SOURCE,
        external_id=str(media["id"]),
        kind=kind,
        format=media.get("format"),
        title=display,
        title_native=title.get("native"),
        title_romaji=title.get("romaji"),
        title_english=title.get("english"),
        synonyms=[s for s in media.get("synonyms") or [] if s],
        status=media.get("status"),
        start_date=start[0] if start else None,
        start_precision=start[1] if start else None,
        end_date=end[0] if end else None,
        end_precision=end[1] if end else None,
        season=media.get("season"),
        season_year=media.get("seasonYear"),
        episodes=media.get("episodes"),
        chapters=media.get("chapters"),
        volumes=media.get("volumes"),
        genres=[g for g in media.get("genres") or [] if g],
        tags=_tags(media),
        studios=_studios(media) if kind == "anime" else [],
        creators=_creators(media) if kind != "anime" else [],
        popularity=media.get("popularity"),
        score=media.get("averageScore"),
        source_material=media.get("source"),
        url=media.get("siteUrl"),
        cover=(media.get("coverImage") or {}).get("medium"),
        extra=_extra(media),
    )


def relation_edges(media: dict[str, Any]) -> list[tuple[str, str]]:
    """(to_id, relation_type) pairs for `db.replace_relations`."""
    edges: list[tuple[str, str]] = []
    for edge in ((media.get("relations") or {}).get("edges")) or []:
        node = edge.get("node") or {}
        relation = edge.get("relationType")
        if node.get("id") is not None and relation:
            edges.append((db.entry_id(SOURCE, str(node["id"])), relation))
    return edges


# --------------------------------------------------------------------------- HTTP


class _Api:
    """Paced GraphQL calls that also honour AniList's remaining-request header."""

    def __init__(self, client: httpx.AsyncClient, clock: Clock, log: Callable[[str], None]) -> None:
        self.client = client
        self.clock = clock
        self.log = log
        self.pacer = Pacer(PACE_SECONDS, clock)
        self._owed_wait = 0.0

    async def page(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if self._owed_wait > 0:
            wait, self._owed_wait = self._owed_wait, 0.0
            self.log(f"  rate limit nearly used up, waiting {wait:.0f}s")
            await self.clock.sleep(wait)
        response = await request_with_retry(
            self.client,
            "POST",
            ENDPOINT,
            clock=self.clock,
            pacer=self.pacer,
            log=self.log,
            json={"query": query, "variables": variables},
            headers={"Accept": "application/json"},
        )
        self._note_rate_limit(response)
        return _unwrap(response)

    def _note_rate_limit(self, response: httpx.Response) -> None:
        try:
            remaining = int(response.headers["x-ratelimit-remaining"])
        except (KeyError, ValueError):
            return
        if remaining > LOW_REMAINING:
            return
        wait = DEFAULT_RESET_WAIT
        try:
            reset = float(response.headers["x-ratelimit-reset"])
            wait = min(max(reset - self.clock.now().timestamp(), 0.0) + RESET_MARGIN, MAX_RESET_WAIT)
        except (KeyError, ValueError):
            pass
        self._owed_wait = max(self._owed_wait, wait)


def _cursor_page(cursor: str) -> int:
    try:
        return max(int(cursor), 1)
    except ValueError:
        return 1


def _unwrap(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AniListError(f"HTTP {response.status_code}: not JSON ({exc})") from exc
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if response.status_code >= 400:
        message = "; ".join(str(e.get("message")) for e in errors or [] if isinstance(e, dict))
        raise AniListError(f"HTTP {response.status_code}: {message or response.text[:200]}")
    page = ((payload.get("data") or {}).get("Page")) if isinstance(payload, dict) else None
    if page is None:
        raise AniListError(f"no Page in response: {str(errors)[:200]}")
    return page


# --------------------------------------------------------------------------- harvest


@dataclass
class _Run:
    conn: sqlite3.Connection
    api: _Api
    result: HarvestResult
    limit: Optional[int]
    log: Callable[[str], None]

    @property
    def limit_reached(self) -> bool:
        return self.limit is not None and self.result.fetched >= self.limit

    def _record_error(self, message: str) -> None:
        if len(self.result.errors) < MAX_RECORDED_ERRORS:
            self.result.errors.append(message)

    def _store(self, media_list: list[dict[str, Any]]) -> int:
        """Upserts a page (entries + relations) in one commit; returns the row count."""
        entries: list[db.Entry] = []
        for media in media_list:
            try:
                entry = to_entry(media)
                if entry is None:
                    continue
                db.replace_relations(self.conn, entry.id, relation_edges(media))
                entries.append(entry)
            except Exception as exc:  # one odd record must not sink a whole slice
                self._record_error(f"convert anilist:{media.get('id')}: {type(exc).__name__}: {exc}")
        return db.upsert_entries(self.conn, entries)

    async def run_slice(self, sl: Slice) -> bool:
        """Runs one slice; True when it finished (done), False when it failed or stopped early."""
        state = db.slice_status(self.conn, SOURCE, sl.key)
        if state is not None and state["status"] == "done":
            self.result.slices_skipped += 1
            return True

        page, fetched = 1, 0
        if state is not None and state["cursor"]:
            if state["cursor"] == SPLIT_CURSOR:
                return await self._run_months(sl, state["fetched"])
            page = _cursor_page(state["cursor"])
            fetched = state["fetched"]
            self.log(f"{sl.key}: resuming from page {page}")
        else:
            self.log(f"{sl.key}: start")

        while True:
            try:
                data = await self.api.page(sl.query, sl.variables(page))
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                db.mark_slice(
                    self.conn, SOURCE, sl.key, "error", fetched=fetched, cursor=str(page), message=message
                )
                self.result.slices_error += 1
                self._record_error(f"{sl.key} page {page}: {message}")
                self.log(f"{sl.key}: error on page {page}: {message}")
                return False

            media_list = [m for m in data.get("media") or [] if m]
            info = data.get("pageInfo") or {}
            if self.limit is not None:
                media_list = media_list[: max(self.limit - self.result.fetched, 0)]
            full_page = len(data.get("media") or []) >= PER_PAGE
            stored = self._store(media_list)
            fetched += stored
            self.result.fetched += stored
            if page % 10 == 0:
                self.log(f"{sl.key}: page {page}, {fetched} rows")

            if self.limit_reached:
                # Not done: resume by fetching this page again (it may have been cut short).
                db.mark_slice(self.conn, SOURCE, sl.key, "pending", fetched=fetched, cursor=str(page))
                self.log(f"{sl.key}: limit {self.limit} reached")
                return False

            # `pageInfo.total` is a bogus 5000 for any big result set, and `hasNextPage` follows it,
            # so only a short page (or an explicit False) is a trustworthy end of the slice.
            if page >= sl.max_pages:
                if sl.sort != "ID" or not full_page:
                    return self._finish(sl, fetched)
                if sl.year is not None:
                    return await self._run_months(sl, fetched)
                note = f"truncated at {MAX_ITEMS} rows"
                self.log(f"{sl.key}: {note}")
                self._record_error(f"{sl.key}: {note}")
                return self._finish(sl, fetched, note)
            if not info.get("hasNextPage") or not full_page:
                return self._finish(sl, fetched)

            page += 1
            # Progress checkpoint, so a killed process resumes here instead of at page 1.
            db.mark_slice(self.conn, SOURCE, sl.key, "pending", fetched=fetched, cursor=str(page))

    def _finish(self, sl: Slice, fetched: int, message: Optional[str] = None) -> bool:
        db.mark_slice(self.conn, SOURCE, sl.key, "done", fetched=fetched, message=message)
        self.result.slices_done += 1
        self.log(f"{sl.key}: done, {fetched} rows")
        return True

    async def _run_months(self, sl: Slice, fetched: int) -> bool:
        self.log(f"{sl.key}: more than {MAX_ITEMS} rows, splitting by month")
        db.mark_slice(
            self.conn, SOURCE, sl.key, "pending", fetched=fetched, cursor=SPLIT_CURSOR, message="split by month"
        )
        all_done = True
        for month in range(1, 13):
            if self.limit_reached:
                return False
            if not await self.run_slice(sl.month(month)):
                all_done = False
        if not all_done:
            return False
        return self._finish(sl, fetched, "split by month")


async def harvest(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    clock: Clock,
    limit: Optional[int] = None,
    log: Callable[[str], None] = print,
) -> HarvestResult:
    result = HarvestResult(source=SOURCE)
    run = _Run(conn=conn, api=_Api(client, clock, log), result=result, limit=limit, log=log)
    for sl in plan_slices(clock.now().year):
        if run.limit_reached:
            break
        await run.run_slice(sl)
    return result
