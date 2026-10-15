"""国会図書館サーチ (NDL Search) harvester for the catalog: new and upcoming paper books.

Two classes of book are collected, each with its NDC (a prefix match on the
server): `913.6` Japanese novels and `726.1` manga. Every request is
`mediatype=books&from=<YYYY-MM>[&until=<YYYY-MM>]&cnt=500&idx=1[&isbn=<prefix>]`.

Facts verified against the live API (2026-10-14):

- **idx cap.** Only the first 500 records of a query are reachable: the window
  `idx .. idx+cnt-1` must end at 500 or the answer is empty (`idx=501&cnt=1`,
  `idx=1001`, `idx=250&cnt=500` -> records 250..500 only, `idx=500&cnt=500` -> one
  record). `totalResults` still reports the real count (10013 novels and 16037
  manga from 2025-10 on). So `cnt=500&idx=1` is the whole reachable window and
  paging with idx cannot get further; a query whose `totalResults` is above 500
  has to be cut into smaller queries.
- **from / until.** `YYYY-MM` is the finest unit that splits anything: NDL dates
  are almost all month precision (`2026.8`), so `until=2025-10-15` answers
  exactly what `until=2025-10` does. One month holds 700-1100 novels and
  1250-1550 manga, which is still above 500, so a month is cut further.
- **isbn prefix.** `isbn=9784334` (hyphens allowed: `978-4-334`) matches the ISBNs
  starting with it. A prefix of 5 digits or more answers in a second or two;
  `isbn=9784` or `isbn=978` never answers (timeout), and `*` wildcards time out as
  well. So a slice that is over 500 is cut into `97840 .. 97849` and a prefix
  that is still over 500 into its ten children, recursively. Only 978-4 (Japan)
  books are wanted, so the other ISBN groups are never asked for.
- Records come back ordered by the title reading, so the first 500 of a query
  are the あ-行 ones: an over-limit query cannot be sampled, only cut. The cut was
  checked on 913.6:2026-09: `totalResults` 662 = the ten `97840 .. 97849` answers
  (148+44+51+63+58+52+2+37+180+8 = 643) + 19 records without a 978-4 ISBN.
- `ndc=` only returns catalogued records. 近刊 records of NDL's pre-publication
  feed (provider R100000137: ISBN, dated to the day, volume in the title, no NDC)
  are not found by an `ndc` query at all; they are only reachable by title (the
  real-time adapter does that per series). The catalogued records of the months
  ahead mostly have no ISBN yet (2026-11: 70 of 78) and are dropped.
- A query NDL has not answered before can take more than 30 s (ReadTimeout); the
  retry a few seconds later is answered from its cache, so the timeouts are only
  a delay (about 4 in 10 requests on 2026-10-14).
- Under load the API answers HTTP 429 with an XML `<error>` body and no
  Retry-After ("同時アクセス数の上限" - a concurrency limit, not a quota; it
  cleared within seconds when probed). `runtime.request_with_retry` would wait
  60 s each time, so a 429 without Retry-After is given a 20 s one here.

Slices (resume units) are `<ndc>:<YYYY-MM>`, one per month from one year ago to
six months ahead, newest first (an interrupted or `--limit` run keeps the books
that matter most), plus `<ndc>:<YYYY-MM>..` for everything after that. A slice is
cut into ISBN prefixes inside; an interrupted slice is simply fetched again.

`recent=True` (`build --recent`) asks only the slices from last month on (RECENT_MONTHS_BACK:
the books NDL is cataloguing now), the months ahead and the tail, whether or not they are
done. They are the full build's own slices, so a finished one is simply marked done again;
older months keep their record.

Filtering reuses the real-time adapter (`backend.adapters.ndl`): ISBNs other
than 978-4, records without an ISBN, e-books, date ranges and excluded words
(アンソロジー・ファンブック・特装版 ...) are dropped, and the same ISBN listed
twice becomes one row with the finer date. 1 req/s.
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable, Optional

import httpx

from ...adapters.ndl import (
    _NS,
    _PRECISION_RANK,
    NDL_API_URL,
    NdlRecord,
    _key,
    _match_prefix,
    _merge,
    _parse_item,
    _series_name,
    _text,
    _title_label,
    exclusion_reason,
)
from ...timeutil import JST
from .. import db
from ..runtime import Clock, HarvestResult, Pacer, request_with_retry

SOURCE = "ndl"
REQUEST_INTERVAL = 1.0  # seconds between requests (1 req/s)
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
PAGE_SIZE = 500  # NDL's maximum `cnt`
REACH = 500  # records reachable per query: idx + cnt - 1 must not pass this
MONTHS_BACK = 12
MONTHS_AHEAD = 6
RECENT_MONTHS_BACK = 1  # `build --recent`: last month (still being catalogued), this month and the months ahead
ISBN_ROOT = "9784"  # Japanese ISBNs; `isbn=9784` itself never answers, its 5-digit children do
ISBN_LENGTH = 13
MAX_REQUESTS_PER_SLICE = 600
RATE_LIMIT_WAIT = 20.0  # seconds to wait after a 429 that names no Retry-After
RETRIES = 6  # NDL's 429 comes in bursts of a minute or so (the design's 3 retries run out inside one)


@dataclass(frozen=True)
class BookClass:
    ndc: str
    format: str  # Entry.format
    hint: str  # extra.kindHint


CLASSES = (
    BookClass("913.6", "小説", "novel"),
    BookClass("726.1", "漫画", "manga"),
)


@dataclass(frozen=True)
class Slice:
    cls: BookClass
    start: str  # YYYY-MM
    end: Optional[str]  # YYYY-MM, None = everything from `start` on

    @property
    def key(self) -> str:
        return f"{self.cls.ndc}:{self.start}" + ("" if self.end else "..")

    def params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "ndc": self.cls.ndc,
            "mediatype": "books",
            "from": self.start,
            "cnt": PAGE_SIZE,
            "idx": 1,
        }
        if self.end:
            params["until"] = self.end
        return params


def _add_months(first: date, months: int) -> date:
    index = first.year * 12 + (first.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def build_slices(today: date, *, recent: bool = False) -> list[Slice]:
    """Every slice of a run, newest first: the open-ended tail, then month by month back.

    `recent` keeps the tails and the months from RECENT_MONTHS_BACK before this month on.
    """
    this_month = today.replace(day=1)
    last = _add_months(this_month, MONTHS_AHEAD)
    months = [_add_months(last, -n) for n in range(MONTHS_BACK + MONTHS_AHEAD + 1)]
    slices = [Slice(cls, _add_months(last, 1).strftime("%Y-%m"), None) for cls in CLASSES]
    for month in months:
        ym = month.strftime("%Y-%m")
        slices.extend(Slice(cls, ym, ym) for cls in CLASSES)
    if recent:
        first = _add_months(this_month, -RECENT_MONTHS_BACK).strftime("%Y-%m")
        slices = [spec for spec in slices if spec.start >= first]  # YYYY-MM sorts as text
    return slices


# -- volume / series ------------------------------------------------------------------------

_SPACE = r"[\s　]"
_NUM = r"[0-9一二三四五六七八九十百]+"
_TRAILING_SEPARATORS = re.compile(r"[\s　~〜～:：\-‐―・,、/]+$")
# 部 marker of a 近刊 title: "<series> 第五部 女神の化身12" (published records keep it in dcndl:volume).
_PART = re.compile(rf"[\s　~〜～:：\-]*第\s*{_NUM}\s*部.*$")
# Volume written at the end of a title, tried repeatedly ("... 物語17 (上)" -> "17" then nothing).
_VOLUME_TAILS = (
    re.compile(rf"{_SPACE}*[(（]\s*[上中下前後]\s*[)）]$"),
    re.compile(rf"{_SPACE}*[上中下前後]巻$"),
    re.compile(rf"{_SPACE}*[(（]\s*(?:第\s*)?\d+\s*(?:巻|話|集)?\s*[)）]$"),
    re.compile(rf"{_SPACE}*[「『][^「『」』]*?\d+\s*[」』]$"),
    re.compile(rf"{_SPACE}*(?:第\s*)?{_NUM}\s*[巻冊集話]$"),
    re.compile(rf"{_SPACE}*(?:vol|volume|ep|episode)\.?\s*\d+$", re.IGNORECASE),
    re.compile(rf"{_SPACE}*#\s*\d+$"),
    re.compile(rf"{_SPACE}*(?:第\s*)?(?<![0-9])\d+$"),
)
_MIN_SERIES_LENGTH = 2


def _strip_tails(text: str) -> str:
    """`text` without the volume markers at its end (it is returned as is when nothing is left)."""
    current = text
    while True:
        for pattern in _VOLUME_TAILS:
            stripped = _TRAILING_SEPARATORS.sub("", pattern.sub("", current, count=1))
            if stripped != current.rstrip() and len(stripped) >= _MIN_SERIES_LENGTH:
                current = stripped
                break
        else:
            return current


def series_name(title: str, volume: Optional[str], *, in_title: bool = False) -> str:
    """The series a volume belongs to: its title without the volume.

    A catalogued record carries `dcndl:volume` and keeps its title (only a copy of the
    volume at the end is dropped). A 近刊 record has no NDC (`in_title`) or no volume
    field and writes the volume in the title, so the 部 marker, "第N巻", "(上)", "17" ...
    are cut from the end.
    """
    text = title.strip()
    if volume and text.endswith(volume) and len(text) - len(volume) >= _MIN_SERIES_LENGTH:
        text = _TRAILING_SEPARATORS.sub("", text[: -len(volume)])  # the volume copied into the title
    if volume and not in_title:
        return text  # the volume is its own field
    part = _PART.search(text)
    if part and part.start() >= _MIN_SERIES_LENGTH:
        text = text[: part.start()]
    return _strip_tails(text)


def display_title(title: str, volume: Optional[str]) -> str:
    """Title with its volume ("悪役令嬢はしゃべりません 5"), unless the title already ends with it."""
    if not volume:
        return title
    numbers = re.findall(r"\d+", volume)
    core = title.rstrip(" 」』)]】>〉》\"'")
    if title.endswith(volume) or (numbers and re.search(rf"(?<![0-9]){numbers[-1]}$", core)):
        return title
    return f"{title} {volume}"


def line_of(record: NdlRecord, series: str) -> str:
    """叢書 (系統): the series name of the label ("角川ビーンズ文庫"), else a label written in the title."""
    stated = _series_name(record.series_title) or _title_label(record.title) or ""
    if stated and (
        _match_prefix(stated, _key(series)) is not None or _match_prefix(series, _key(stated)) is not None
    ):
        return ""  # the "series" is the work itself: 本編
    return stated


_ROLES = (
    "著・文・その他|イラスト原案|キャラクター原案|キャラクターデザイン|原作|原著|原案|作画|漫画|"
    "イラスト|編著|監修|編|著|作|文|絵|画|脚本|訳|翻訳"
)
# "<name> <role>" or "<name> [<role>]": the space matters, "…美文" is a name, not a 文.
_ROLE = re.compile(rf"^(?P<name>.+?)(?:\s+|\s*[\[［])(?:{_ROLES})\s*[\]］]?$")


def creators_of(record: NdlRecord) -> list[str]:
    """Author names without roles ("香月美夜 原作" -> "香月美夜").

    `<author>` is "姓, 名, 生年,<名前> <役割>,..." so only the pieces ending in a role are
    names; records with none fall back to `dc:creator`.
    """
    names: list[str] = []
    for piece in re.split(r"[,;]", record.author or ""):
        match = _ROLE.match(piece.strip())
        if match:
            name = match.group("name").strip()
            if name and name not in names:
                names.append(name)
    if names:
        return names
    return list(dict.fromkeys(record.creators))


# -- parsing --------------------------------------------------------------------------------


class NdlError(Exception):
    """A response the harvester cannot use (error body, bad XML)."""


@dataclass(frozen=True)
class NdlItem:
    record: NdlRecord
    link: Optional[str] = None
    reading: Optional[str] = None


def parse_page(body: bytes) -> tuple[int, list[NdlItem]]:
    """(totalResults, items) of one OpenSearch page; raises NdlError on anything else."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NdlError(f"invalid XML response: {exc}") from exc
    if root.tag == "error":
        raise NdlError(f"{root.findtext('code') or '?'} {(root.findtext('message') or '').strip()[:80]}")
    channel = root.find("channel")
    if channel is None:
        raise NdlError("no <channel> in the response")
    total_text = (channel.findtext("openSearch:totalResults", namespaces=_NS) or "").strip()
    if not total_text.isdigit():
        raise NdlError("no totalResults in the response")
    items: list[NdlItem] = []
    for element in channel.findall("item"):
        record = _parse_item(element)
        if record is None:
            continue
        link = (element.findtext("link") or "").strip() or None
        items.append(NdlItem(record=record, link=link, reading=_text(element, "dcndl:titleTranscription")))
    return int(total_text), items


def to_entry(item: NdlItem, cls: BookClass) -> Optional[db.Entry]:
    """One NDL record -> a catalog entry (None when the record is not a Japanese paper volume)."""
    record = item.record
    if exclusion_reason(record, _key(record.title)) is not None:
        return None
    assert record.isbn and record.date and record.precision
    series = series_name(record.title, record.volume, in_title=record.ndc is None)
    volume = record.volume or record.title[len(series) :].strip(" 　~〜～:：-") or None
    return db.Entry(
        source=SOURCE,
        external_id=record.isbn,
        kind="book",
        format=cls.format,
        title=display_title(record.title, record.volume),
        title_reading=item.reading,
        start_date=record.date,
        start_precision=record.precision,
        creators=creators_of(record),
        url=item.link,
        extra={
            "isbn": record.isbn,
            "volume": volume,
            "series": series,
            "line": line_of(record, series),
            "publisher": record.publisher,
            "ndc": record.ndc or cls.ndc,
            "kindHint": cls.hint,
        },
    )


def _merge_items(a: NdlItem, b: NdlItem) -> NdlItem:
    return NdlItem(
        record=_merge(a.record, b.record),
        link=a.link or b.link,
        reading=a.reading or b.reading,
    )


def entries_of(items: Iterable[NdlItem], cls: BookClass) -> list[db.Entry]:
    """Entries of a page: filtered, and one per ISBN (finer date, catalogued record preferred)."""
    by_isbn: dict[str, NdlItem] = {}
    for item in items:
        isbn = item.record.isbn
        if not isbn:
            continue
        by_isbn[isbn] = _merge_items(by_isbn[isbn], item) if isbn in by_isbn else item
    entries = (to_entry(item, cls) for item in by_isbn.values())
    return [entry for entry in entries if entry is not None]


def _keep_finer_dates(conn: sqlite3.Connection, entries: list[db.Entry]) -> None:
    """A row stored earlier keeps its date when it is more precise than the new one."""
    for offset in range(0, len(entries), 500):
        chunk = entries[offset : offset + 500]
        marks = ", ".join("?" for _ in chunk)
        stored = {
            row["id"]: row
            for row in conn.execute(
                f"SELECT id, start_date, start_precision FROM entries WHERE id IN ({marks})",
                [entry.id for entry in chunk],
            )
        }
        for entry in chunk:
            row = stored.get(entry.id)
            if row is not None and _PRECISION_RANK[row["start_precision"]] > _PRECISION_RANK[entry.start_precision]:
                entry.start_date, entry.start_precision = row["start_date"], row["start_precision"]


def children(prefix: str) -> list[str]:
    """The ISBN prefixes a query over `prefix` ("" = the whole slice) is cut into."""
    if not prefix:
        return [f"{ISBN_ROOT}{digit}" for digit in "0123456789"]
    if len(prefix) >= ISBN_LENGTH:
        return []
    return [f"{prefix}{digit}" for digit in "0123456789"]


# -- fetching ---------------------------------------------------------------------------------


class _PatientClient:
    """Gives a 429 that names no Retry-After a short one (see the module docstring)."""

    def __init__(self, client: httpx.AsyncClient, wait: float) -> None:
        self._client = client
        self._wait = wait

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(method, url, **kwargs)
        if response.status_code == 429 and "retry-after" not in response.headers:
            response.headers["Retry-After"] = f"{self._wait:g}"
        return response


async def _fetch(
    client: Any, params: dict[str, Any], *, clock: Clock, pacer: Pacer, log: Callable[[str], None]
) -> tuple[int, list[NdlItem]]:
    response = await request_with_retry(
        client,
        "GET",
        NDL_API_URL,
        clock=clock,
        pacer=pacer,
        retries=RETRIES,
        log=log,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise NdlError(f"HTTP {response.status_code}")
    return parse_page(response.content)


async def _harvest_slice(
    conn: sqlite3.Connection,
    client: Any,
    spec: Slice,
    *,
    clock: Clock,
    pacer: Pacer,
    limit: Optional[int],
    result: HarvestResult,
    log: Callable[[str], None],
) -> bool:
    """Harvests one slice. Returns False when the run's `limit` was reached."""
    key = spec.key
    seen: set[str] = set()
    failures: list[str] = []
    pending = [""]  # ISBN prefixes still to ask ("" = the whole slice)
    requests = 0
    stopped = False

    while pending:
        prefix = pending.pop()
        if requests >= MAX_REQUESTS_PER_SLICE:
            failures.append(f"{key}: more than {MAX_REQUESTS_PER_SLICE} requests, giving up")
            break
        params = spec.params()
        if prefix:
            params["isbn"] = prefix
        requests += 1
        try:
            total, items = await _fetch(client, params, clock=clock, pacer=pacer, log=log)
        except (NdlError, httpx.HTTPError) as exc:
            failures.append(f"{key} isbn={prefix or '*'}: {type(exc).__name__}: {exc}")
            continue

        entries = [e for e in entries_of(items, spec.cls) if e.external_id not in seen]
        if limit is not None:
            entries = entries[: max(limit - result.fetched, 0)]
        _keep_finer_dates(conn, entries)
        db.upsert_entries(conn, entries)
        seen.update(entry.external_id for entry in entries)
        result.fetched += len(entries)
        log(f"    {key} isbn={prefix or '*'}: {len(items)} records, {len(entries)} new rows (of {total})")

        if limit is not None and result.fetched >= limit:
            stopped = True
            break
        if total > REACH:
            cut = children(prefix)
            if not cut:
                log(f"    ! {key} isbn={prefix}: {total} records for one ISBN, only {len(items)} reachable")
            pending.extend(reversed(cut))  # ascending order

    if stopped:
        db.mark_slice(conn, SOURCE, key, "pending", fetched=len(seen), message="limit")
        return False
    if failures:
        message = failures[0] if len(failures) == 1 else f"{failures[0]} (+{len(failures) - 1} more)"
        db.mark_slice(conn, SOURCE, key, "error", fetched=len(seen), message=message)
        result.slices_error += 1
        result.errors.extend(failures)
        log(f"  ! {key}: {message}")
        return True
    db.mark_slice(conn, SOURCE, key, "done", fetched=len(seen))
    result.slices_done += 1
    log(f"  {key}: {len(seen)} books in {requests} requests")
    return True


async def harvest(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    clock: Clock,
    limit: Optional[int] = None,
    log: Callable[[str], None] = print,
    recent: bool = False,
) -> HarvestResult:
    """New and upcoming novels / manga into `entries`. `limit` caps the rows of this run.

    `recent`: only the slices from last month on, done or not.
    """
    result = HarvestResult(source=SOURCE)
    pacer = Pacer(REQUEST_INTERVAL, clock)
    patient = _PatientClient(client, RATE_LIMIT_WAIT)
    today = clock.now().astimezone(JST).date()
    for spec in build_slices(today, recent=recent):
        if limit is not None and result.fetched >= limit:
            break
        if not recent and db.is_slice_done(conn, SOURCE, spec.key):
            result.slices_skipped += 1
            continue
        keep_going = await _harvest_slice(
            conn, patient, spec, clock=clock, pacer=pacer, limit=limit, result=result, log=log
        )
        if not keep_going:
            break
    return result
