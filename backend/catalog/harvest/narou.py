"""なろう小説 harvester for the catalog: top-rated serials per genre.

Facts verified against the live API (2026-10-14):

- The whole site has ~1.26M works, but one search condition can only reach the
  first ~2500 results: `st` is capped at 2000 and `lim` at 500. `st=2001` is
  answered with HTTP 404 and the plain-text body `ERROR:2000作品以上は検索できません。`.
  So the catalog slices by genre and takes the top of each: `type=re` (serials
  only, no short stories) ordered by `hyoka` (global points, best first).
- `gzip=5` returns a gzip *file* (`Content-Type: application/x-gzip`, no
  `Content-Encoding`), so httpx does not decode it; this module gunzips the body.
- A window past the end of the result set (`st` larger than `allcount`) answers
  `[{"allcount": N}]` with no records.
- `end`: 0 = completed (or a short story), 1 = still being serialized.
- `of=` narrows each record to the listed fields (t=title, n=ncode, w=writer,
  bg=biggenre, g=genre, k=keyword, gf=general_firstup, gl=general_lastup, e=end,
  ga=general_all_no, gp=global_point, f=fav_novel_cnt).
- Dates (`general_firstup` / `general_lastup`) are "YYYY-MM-DD HH:MM:SS" in JST,
  so the date part is already the JST calendar date.

Slices are `genre:<code>`. A slice's `cursor` is the next `st` to request, so an
interrupted run resumes inside a genre. 1 req/s.

`recent=True` (`build --recent`) re-asks only the top RECENT_PAGES windows of every
genre (the works readers look at, whose episode counts and status change), whether
or not the full slice is done. It keeps its own slice records, `recent:genre:<code>`,
and starts from `st=1` every time: the full slice's cursor and state are not touched.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import zlib
from datetime import date
from typing import Any, Callable, Optional

import httpx

from .. import db
from ..runtime import Clock, HarvestResult, Pacer, request_with_retry

SOURCE = "narou"
API_URL = "https://api.syosetu.com/novelapi/api/"
REQUEST_INTERVAL = 1.0  # seconds between requests (1 req/s)
PAGE_SIZE = 500  # API maximum for `lim`
MAX_START = 2000  # API maximum for `st`
GZIP_LEVEL = 5
RECENT_PAGES = 2  # `build --recent`: the top 2 x 500 of each genre
RECENT_PREFIX = "recent:"  # slice keys of a recent run: recent:genre:<code>
RESPONSE_FIELDS = "t-n-w-bg-g-k-gf-gl-e-ga-gp-f"

# Official genre codes (https://dev.syosetu.com/man/api/, "genre").
GENRES: dict[int, str] = {
    101: "異世界〔恋愛〕",
    102: "現実世界〔恋愛〕",
    201: "ハイファンタジー〔ファンタジー〕",
    202: "ローファンタジー〔ファンタジー〕",
    301: "純文学〔文芸〕",
    302: "ヒューマンドラマ〔文芸〕",
    303: "歴史〔文芸〕",
    304: "推理〔文芸〕",
    305: "ホラー〔文芸〕",
    306: "アクション〔文芸〕",
    307: "コメディー〔文芸〕",
    401: "VRゲーム〔SF〕",
    402: "宇宙〔SF〕",
    403: "空想科学〔SF〕",
    404: "パニック〔SF〕",
    9901: "童話〔その他〕",
    9902: "詩〔その他〕",
    9903: "エッセイ〔その他〕",
    9904: "リプレイ〔その他〕",
    9999: "その他〔その他〕",
    9801: "ノンジャンル〔ノンジャンル〕",
}

# Keywords that tell a work has been published / adapted.
PUBLICATION_FLAGS = ("書籍化", "コミカライズ", "アニメ化")


class NarouError(Exception):
    """A response the harvester cannot use (error body, bad JSON, bad shape)."""


def slice_key(code: int) -> str:
    return f"genre:{code}"


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_part(value: Any) -> Optional[str]:
    """'2016-01-05 01:24:12' (JST) -> '2016-01-05'; None when it is not a real date."""
    text = str(value or "").strip().split(" ")[0]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _status(end: Any) -> Optional[str]:
    value = _to_int(end)
    if value == 1:
        return "連載中"
    if value == 0:
        return "完結"
    return None


def to_entry(raw: dict[str, Any], default_genre: Optional[int] = None) -> Optional[db.Entry]:
    """One record of the novelapi response -> a catalog entry (None when unusable)."""
    ncode = str(raw.get("ncode") or "").strip().lower()
    title = str(raw.get("title") or "").strip()
    if not ncode or not title:
        return None
    genre_code = _to_int(raw.get("genre"))
    if genre_code not in GENRES:
        genre_code = default_genre
    writer = str(raw.get("writer") or "").strip()
    keyword = str(raw.get("keyword") or "")
    tags = list(dict.fromkeys(keyword.split()))
    first_up = _date_part(raw.get("general_firstup"))
    flags = [flag for flag in PUBLICATION_FLAGS if flag in keyword]
    return db.Entry(
        source=SOURCE,
        external_id=ncode,
        kind="web-novel",
        title=title,
        creators=[writer] if writer else [],
        genres=[GENRES[genre_code]] if genre_code in GENRES else [],
        tags=tags,
        status=_status(raw.get("end")),
        episodes=_to_int(raw.get("general_all_no")),
        popularity=_to_int(raw.get("global_point")),
        start_date=first_up,
        start_precision="day" if first_up else None,
        url=f"https://ncode.syosetu.com/{ncode}/",
        extra={
            "genre": genre_code,
            "bigGenre": _to_int(raw.get("biggenre")),
            "favCount": _to_int(raw.get("fav_novel_cnt")),
            "lastUpdate": _date_part(raw.get("general_lastup")),
            "flags": flags,
        },
    )


def _decode_body(response: httpx.Response) -> str:
    body = response.content
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as exc:
            raise NarouError(f"could not gunzip the response: {exc}") from exc
    return body.decode("utf-8", errors="replace")


def parse_page(response: httpx.Response) -> tuple[int, list[dict[str, Any]]]:
    """(allcount, records) of one novelapi response; raises NarouError on anything else."""
    text = _decode_body(response)
    if response.status_code != 200 or text.startswith("ERROR"):
        raise NarouError(f"HTTP {response.status_code}: {text.strip()[:120]}")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise NarouError(f"invalid JSON response: {exc}") from exc
    if (
        not isinstance(payload, list)
        or not payload
        or not isinstance(payload[0], dict)
        or _to_int(payload[0].get("allcount")) is None
    ):
        raise NarouError("unexpected response shape")
    records = [item for item in payload[1:] if isinstance(item, dict)]
    return int(payload[0]["allcount"]), records


def following_start(st: int, lim: int) -> Optional[int]:
    """Next `st` after a window of `lim` records starting at `st` (None = nothing reachable).

    The last window is pinned to MAX_START, so it overlaps the previous one by a
    row or two; the caller de-duplicates by ncode.
    """
    if st >= MAX_START:
        return None
    return min(st + lim, MAX_START)


def _resume_start(cursor: Optional[str]) -> int:
    value = _to_int(cursor)
    if value is None or not 1 <= value <= MAX_START:
        return 1
    return value


async def _harvest_genre(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    code: int,
    *,
    clock: Clock,
    pacer: Pacer,
    limit: Optional[int],
    result: HarvestResult,
    log: Callable[[str], None],
    recent: bool = False,
) -> bool:
    """Harvests one genre slice. Returns False when the run's `limit` was reached.

    A recent run never resumes (it is a few requests) and keeps its own slice record.
    """
    key = (RECENT_PREFIX if recent else "") + slice_key(code)
    state = None if recent else db.slice_status(conn, SOURCE, key)
    st = _resume_start(state["cursor"]) if state is not None else 1
    total = state["fetched"] if state is not None and st > 1 else 0
    seen: set[str] = set()
    pages = 0
    log(f"  {key} {GENRES[code]} (from st={st})")

    while True:
        lim = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - result.fetched)
        params = {
            "genre": code,
            "type": "re",
            "order": "hyoka",
            "lim": lim,
            "st": st,
            "out": "json",
            "gzip": GZIP_LEVEL,
            "of": RESPONSE_FIELDS,
        }
        try:
            response = await request_with_retry(
                client, "GET", API_URL, clock=clock, pacer=pacer, log=log, params=params
            )
            allcount, records = parse_page(response)
        except (NarouError, httpx.HTTPError) as exc:
            message = f"{key} st={st}: {type(exc).__name__}: {exc}"
            db.mark_slice(conn, SOURCE, key, "error", fetched=total, cursor=str(st), message=message)
            result.slices_error += 1
            result.errors.append(message)
            log(f"  ! {message}")
            return True

        entries: list[db.Entry] = []
        for record in records:
            entry = to_entry(record, default_genre=code)
            if entry is None or entry.external_id in seen:
                continue
            seen.add(entry.external_id)
            entries.append(entry)
        db.upsert_entries(conn, entries)
        result.fetched += len(entries)
        total += len(entries)
        log(f"    st={st} lim={lim}: {len(entries)} rows (allcount={allcount}, slice total {total})")

        pages += 1
        following = following_start(st, lim)
        # Done when the window reached the end of the result set or of what the API allows
        # (or, for a recent run, of the top pages it wants).
        if (
            len(records) < lim
            or following is None
            or st + lim > allcount
            or (recent and pages >= RECENT_PAGES)
        ):
            db.mark_slice(conn, SOURCE, key, "done", fetched=total)
            result.slices_done += 1
            return True
        db.mark_slice(conn, SOURCE, key, "pending", fetched=total, cursor=str(following))
        if limit is not None and result.fetched >= limit:
            return False
        st = following


async def harvest(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    clock: Clock,
    limit: Optional[int] = None,
    log: Callable[[str], None] = print,
    recent: bool = False,
) -> HarvestResult:
    """Top-rated serials of every genre into `entries`. `limit` caps the rows of this run.

    `recent`: only the top RECENT_PAGES windows of each genre, done slices or not.
    """
    result = HarvestResult(source=SOURCE)
    pacer = Pacer(REQUEST_INTERVAL, clock)
    for code in GENRES:
        if limit is not None and result.fetched >= limit:
            break
        if not recent and db.is_slice_done(conn, SOURCE, slice_key(code)):
            result.slices_skipped += 1
            continue
        keep_going = await _harvest_genre(
            conn, client, code, clock=clock, pacer=pacer, limit=limit, result=result, log=log, recent=recent
        )
        if not keep_going:
            break
    return result
