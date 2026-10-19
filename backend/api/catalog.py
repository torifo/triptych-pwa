"""`/api/catalog/*` — browse and export the catalog (backend/catalog.db).

Read-only: every request opens the catalog DB on its own with `mode=ro`, so
the batch commands (`python -m backend.catalog build / link`) can keep writing
to it. When the file does not exist yet, `/stats` answers `ready: false` and
the other endpoints answer 404 `{"detail": "catalog not built"}`.
See specs/catalog/design.md ("API").
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import unicodedata
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Iterable, Iterator, Optional, Sequence
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import timeutil
from ..catalog import db, export
from ..catalog.dto import (
    CatalogEntry,
    CatalogEvent,
    CatalogEventPage,
    CatalogSort,
    CatalogSourceCount,
    CatalogStats,
    CatalogWorkDetail,
    CatalogWorkPage,
    CatalogWorkSummary,
)
from ..catalog.text import fold_for_search

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

NOT_BUILT = "catalog not built"
WORK_NOT_FOUND = "work not found"  # the front end tells these two 404s apart by their detail
DEFAULT_PER = 50
MAX_PER = 100
MAX_QUERY_LENGTH = 200
FTS_MIN_CHARS = 3  # the trigram index needs 3+ characters; shorter queries scan the search text
LINK_THRESHOLD = 0.8  # links below this are only "candidates" (see link.py)
MAX_CANDIDATES = 100

SOURCE_ORDER = ("anilist", "syoboi", "narou", "kakuyomu", "ndl", "jpro")  # jpro: harvester of the 近刊 ndl rows
_KIND_COLUMN = {
    "anime": "has_anime",
    "manga": "has_manga",
    "novel": "has_novel",
    "web-novel": "has_web_novel",
    "book": "has_book",
}
_SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")
# Order of a work's faces in the detail view (the original comes first).
_KIND_RANK = {"novel": 0, "manga": 1, "anime": 2, "web-novel": 3, "book": 4}


# -- database -----------------------------------------------------------------


def _connect(*, check_same_thread: bool = True) -> sqlite3.Connection:
    """Opens the catalog read-only. Raises HTTP 404 if it has not been built."""
    path = db.db_path()
    if not path.is_file():
        raise HTTPException(status_code=404, detail=NOT_BUILT)
    # URI paths are percent-encoded so '?', '#' and '%' in a path stay literal.
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=check_same_thread)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=404, detail=NOT_BUILT) from exc
    conn.row_factory = sqlite3.Row
    return conn


def _schema_ready(conn: sqlite3.Connection) -> bool:
    try:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('entries', 'cluster_summary', 'clusters')"
            )
        }
    except sqlite3.DatabaseError:
        return False
    return names == {"entries", "cluster_summary", "clusters"}


@contextmanager
def _open(*, check_same_thread: bool = True) -> Iterator[sqlite3.Connection]:
    """A read-only connection, closed afterwards. 404 unless the catalog has been built."""
    conn = _connect(check_same_thread=check_same_thread)
    try:
        if not _schema_ready(conn):
            raise HTTPException(status_code=404, detail=NOT_BUILT)
        yield conn
    finally:
        conn.close()


# -- /stats -------------------------------------------------------------------


def _source_rank(source: str) -> tuple[int, str]:
    return (SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER), source)


@router.get("/stats", response_model=CatalogStats)
def get_stats() -> CatalogStats:
    try:
        conn = _connect()
    except HTTPException:
        return CatalogStats(ready=False)
    try:
        if not _schema_ready(conn):
            return CatalogStats(ready=False)
        entry_counts = {
            row["source"]: row["n"]
            for row in conn.execute("SELECT source, COUNT(*) AS n FROM entries GROUP BY source")
        }
        total_entries = sum(entry_counts.values())
        # JPRO の近刊は ndl の行として持つので、ndl の内数として数える（全体の件数には二重に足さない）
        jpro_rows = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE source = 'ndl' AND json_extract(extra, '$.via') = 'jpro'"
        ).fetchone()[0]
        if jpro_rows:
            entry_counts["jpro"] = jpro_rows
        error_counts = {
            row["source"]: row["n"]
            for row in conn.execute(
                "SELECT source, COUNT(*) AS n FROM harvest_state WHERE status = 'error' GROUP BY source"
            )
        }
        harvested = {
            row["key"].split(":")[1]: db.get_meta(conn, row["key"])
            for row in conn.execute("SELECT key FROM meta WHERE key LIKE 'harvest:%:at'")
        }
        sources = sorted(set(entry_counts) | set(error_counts) | set(harvested), key=_source_rank)

        face_sums = conn.execute(
            "SELECT COUNT(*) AS works, "
            + ", ".join(f"COALESCE(SUM({column}), 0) AS {column}" for column in _KIND_COLUMN.values())
            + " FROM cluster_summary"
        ).fetchone()
        return CatalogStats(
            ready=True,
            works=face_sums["works"],
            entries=total_entries,
            links=conn.execute("SELECT COUNT(*) FROM links").fetchone()[0],
            sources=[
                CatalogSourceCount(
                    source=source,
                    entries=entry_counts.get(source, 0),
                    lastHarvestAt=harvested.get(source),
                    errorSlices=error_counts.get(source, 0),
                )
                for source in sources
            ],
            kinds={kind: face_sums[column] for kind, column in _KIND_COLUMN.items()},
            lastLinkAt=db.get_meta(conn, "link:at"),
        )
    finally:
        conn.close()


# -- /works -------------------------------------------------------------------


def _fts_phrase(text: str) -> str:
    """The input as one FTS5 phrase, so operators and punctuation in it stay literal."""
    return '"' + text.replace('"', '""') + '"'


def _search_clause(conn: sqlite3.Connection, key: str, params: dict[str, Any]) -> str:
    """WHERE fragment for a title search over `entries_fts.text` (see `search_text` in catalog/text.py).

    `key` is the user's input after `fold_for_search`, the same folding the stored text got.
    3+ characters use the trigram index. 1-2 characters cannot, so they scan the search text
    itself (about 25 MB for 190k entries) - once, into a temp table, because the count and the
    page would otherwise scan it twice. That scan used to read every title column of `entries`
    (100 MB) instead.
    """
    if len(key) >= FTS_MIN_CHARS:
        params["fts"] = _fts_phrase(key)
        return "cs.cluster_id IN (SELECT cluster_id FROM entries_fts WHERE entries_fts MATCH :fts)"
    # The temp table lives in this request's connection only (a read-only catalog still allows it).
    conn.execute("CREATE TEMP TABLE short_hits (cluster_id TEXT PRIMARY KEY) WITHOUT ROWID")
    # lower() only folds A-Z, which is exactly the case difference search_text() leaves to it.
    conn.execute(
        "INSERT OR IGNORE INTO short_hits SELECT cluster_id FROM entries_fts WHERE instr(lower(text), ?) > 0",
        (key,),
    )
    return "cs.cluster_id IN (SELECT cluster_id FROM short_hits)"


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def _parse_faces(kind: Optional[str], has: Optional[str]) -> list[str]:
    """`kind` and the comma separated `has` as has_* columns (a work must have all of them)."""
    names: list[str] = []
    if kind:
        names.append(kind)
    if has:
        names.extend(part.strip() for part in has.split(",") if part.strip())
    columns: list[str] = []
    for name in names:
        column = _KIND_COLUMN.get(name)
        if column is None:
            raise _unprocessable(f"unknown kind: {name}")
        if column not in columns:
            columns.append(column)
    return columns


# Last day covered by next_date, given next_precision (day when unknown).
_PERIOD_END = (
    "CASE cs.next_precision "
    "WHEN 'month' THEN date(cs.next_date, 'start of month', '+1 month', '-1 day') "
    "WHEN 'year' THEN date(cs.next_date, 'start of year', '+1 year', '-1 day') "
    "ELSE cs.next_date END"
)

# 本で絞った「近日」: 作品ごとに次に出る本（期間の終わりが今日以降で最も近い巻）。アニメの次の話ではなく本の発売で並べる。
_BOOK_END = (
    "CASE e.start_precision "
    "WHEN 'month' THEN date(e.start_date, 'start of month', '+1 month', '-1 day') "
    "WHEN 'year' THEN date(e.start_date, 'start of year', '+1 year', '-1 day') "
    "ELSE e.start_date END"
)
_NEXT_BOOK_CTE = (
    "WITH nb AS (SELECT cid, d, p, t, d_end FROM ("
    "SELECT c.cluster_id AS cid, e.start_date AS d, e.start_precision AS p, e.title AS t, "
    f"{_BOOK_END} AS d_end, "
    f"ROW_NUMBER() OVER (PARTITION BY c.cluster_id ORDER BY {_BOOK_END}, e.start_date, e.id) AS rn "
    "FROM entries e JOIN clusters c ON c.entry_id = e.id "
    f"WHERE e.kind = 'book' AND e.start_date >= :year_start AND {_BOOK_END} >= :today"
    ") WHERE rn = 1) "
)
_TRAILING_VOLUME_RE = re.compile(r"(?:[(（\[［]\s*(\d+)\s*[)）\]］]|\s(\d+))$")


def _book_next_label(work_title: str, book_title: str) -> str:
    """'執愛サイケデリカ(2)' -> '2巻'. Without a volume number: the part after the work title, or the title."""
    title = unicodedata.normalize("NFKC", book_title).strip()
    match = _TRAILING_VOLUME_RE.search(title)
    if match:
        return f"{match.group(1) or match.group(2)}巻"
    base = unicodedata.normalize("NFKC", work_title).strip()
    if base and title.startswith(base):
        rest = title[len(base):].strip(" :：-‐~〜・")
        if rest.isdigit():  # '…なってました30' のように巻数が題名に直に付いている
            return f"{rest}巻"
        return rest or title
    return title


_ORDER_BY: dict[str, str] = {
    "popularity": "cs.popularity DESC, cs.title, cs.cluster_id",
    "recent": "cs.last_year DESC, cs.popularity DESC, cs.title, cs.cluster_id",
    # 期間の終わりで並べる（確定日 → その月中 → その年中）。月初・年初で並べると「2026年中」が先頭に来る
    "upcoming": f"{_PERIOD_END} ASC, cs.next_date ASC, cs.popularity DESC, cs.title, cs.cluster_id",
    "title": "cs.title_norm, cs.title, cs.cluster_id",
}

_SUMMARY_COLUMNS = (
    "cs.cluster_id, cs.title, cs.kinds, cs.entry_count, cs.anime_status, cs.first_year, cs.last_year, "
    "cs.next_date, cs.next_precision, cs.next_label, cs.popularity, cs.cover"
)


_ORIGIN_COLUMNS = ("origin", "novel_cover", "manga_cover")
_ORIGINS = ("novel", "manga", "anime")
_NOVEL_BOOK_FORMATS = ("小説", "文庫")
_MANGA_BOOK_FORMATS = ("漫画", "コミック")


def _origin_columns(conn: sqlite3.Connection, alias: str = "cs") -> str:
    """The original medium's columns, or NULLs on a catalog linked before they existed (the API never alters the DB)."""
    present = {row[1] for row in conn.execute("PRAGMA table_info(cluster_summary)")}
    return ", ".join(f"{alias}.{name}" if name in present else f"NULL AS {name}" for name in _ORIGIN_COLUMNS)


def _origin(value: Any) -> Optional[str]:
    return value if value in _ORIGINS else None


def _book_cover(book_format: Any, novel_cover: Any, manga_cover: Any) -> Optional[str]:
    """A book shows its work's novel or manga cover by its format; never another medium's art (an anime visual)."""
    if book_format in _NOVEL_BOOK_FORMATS:
        return novel_cover or None
    if book_format in _MANGA_BOOK_FORMATS:
        return manga_cover or None
    return None


def _kinds(raw: Optional[str]) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except ValueError:
        return []
    present = {value for value in values if value in _KIND_RANK} if isinstance(values, list) else set()
    return sorted(present, key=_KIND_RANK.__getitem__)


def _focused_covers(
    conn: sqlite3.Connection,
    cluster_ids: Sequence[str],
    *,
    status: Optional[str],
    year: Optional[int],
    season: Optional[str],
) -> dict[str, str]:
    """Covers of the AniList anime entries that match the status / season filter, per cluster.

    The work's own cover is usually its most popular (first) season; when the list is
    narrowed to what airs now or in one season, the matching season's cover is what the
    viewer expects to see.
    """
    if not cluster_ids or (status is None and season is None):
        return {}
    marks = ", ".join(f":c{i}" for i in range(len(cluster_ids)))
    params: dict[str, Any] = {f"c{i}": cid for i, cid in enumerate(cluster_ids)}
    where = [
        f"c.cluster_id IN ({marks})",
        "e.kind = 'anime'",
        "e.source = 'anilist'",
        "e.cover IS NOT NULL AND e.cover <> ''",
    ]
    if status is not None:
        where.append("e.status = :status")
        params["status"] = status
    if season is not None and year is not None:
        where.append("e.season_year = :year AND e.season = :season")
        params["year"] = year
        params["season"] = season
    rows = conn.execute(
        "SELECT c.cluster_id, e.cover FROM clusters c JOIN entries e ON e.id = c.entry_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY CASE WHEN e.format = 'TV' THEN 0 ELSE 1 END, COALESCE(e.popularity, -1) DESC, e.id",
        params,
    ).fetchall()
    covers: dict[str, str] = {}
    for row in rows:
        covers.setdefault(row[0], row[1])
    return covers


def _summary(row: sqlite3.Row, cover: Optional[str] = None) -> CatalogWorkSummary:
    return CatalogWorkSummary(
        id=row["cluster_id"],
        title=row["title"],
        kinds=_kinds(row["kinds"]),
        entryCount=row["entry_count"],
        animeStatus=row["anime_status"],
        firstYear=row["first_year"],
        lastYear=row["last_year"],
        nextDate=row["next_date"],
        nextPrecision=row["next_precision"],
        nextLabel=row["next_label"],
        popularity=row["popularity"] or 0,
        cover=cover or row["cover"],
        origin=_origin(row["origin"]) if "origin" in row.keys() else None,
    )


@router.get("/works", response_model=CatalogWorkPage)
def list_works(
    q: str = Query("", description="title search, ignoring width and case: trigram index for 3+ characters, scan for 1-2"),
    kind: Optional[str] = Query(None, description="works that have this face"),
    has: Optional[str] = Query(None, description="comma separated faces, all required"),
    status: Optional[str] = Query(None, description="anime status (RELEASING / NOT_YET_RELEASED / FINISHED)"),
    year: Optional[int] = Query(None),
    season: Optional[str] = Query(None, description="WINTER / SPRING / SUMMER / FALL (needs year)"),
    sort: CatalogSort = Query("popularity"),
    page: int = Query(1),
    per: int = Query(DEFAULT_PER),
) -> CatalogWorkPage:
    faces = _parse_faces(kind, has)
    season_key = (season or "").strip().upper() or None
    if season_key is not None:
        if season_key not in _SEASONS:
            raise _unprocessable(f"unknown season: {season}")
        if year is None:
            raise _unprocessable("season needs year")
    page = max(page, 1)
    per = min(max(per, 1), MAX_PER)

    where: list[str] = []
    params: dict[str, Any] = {}
    where.extend(f"cs.{column} = 1" for column in faces)
    if status:
        where.append("cs.anime_status = :status")
        params["status"] = status
    if year is not None:
        where.append("cs.first_year <= :year AND cs.last_year >= :year")
        params["year"] = year
        if season_key is not None:
            where.append("instr(cs.seasons, :season) > 0")
            params["season"] = f'"{year:04d}-{season_key}"'
    book_focus = sort == "upcoming" and kind == "book"
    if book_focus:
        today = timeutil.today_jst()
        params["today"] = today
        params["year_start"] = f"{today[:4]}-01-01"
    elif sort == "upcoming":
        today = timeutil.today_jst()
        # next_date of a month / year precision date is the first day of that month / year, but it
        # still lies ahead until the period ends. The year-start bound keeps summary_next usable.
        where.append(f"cs.next_date >= :year_start AND {_PERIOD_END} >= :today")
        params["today"] = today
        params["year_start"] = f"{today[:4]}-01-01"
    key = fold_for_search(q)[:MAX_QUERY_LENGTH]

    with _open() as conn:
        if key:
            where.append(_search_clause(conn, key, params))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        if book_focus:
            source = f"{_NEXT_BOOK_CTE}SELECT {{cols}} FROM cluster_summary cs JOIN nb ON nb.cid = cs.cluster_id {where_sql}"
            total = conn.execute(source.format(cols="COUNT(*)"), params).fetchone()[0]
            rows = conn.execute(
                source.format(cols=f"{_SUMMARY_COLUMNS}, {_origin_columns(conn)}, nb.d AS book_date, nb.p AS book_precision, nb.t AS book_title")
                + " ORDER BY nb.d_end, nb.d, cs.popularity DESC, cs.title, cs.cluster_id LIMIT :limit OFFSET :offset",
                {**params, "limit": per, "offset": (page - 1) * per},
            ).fetchall()
        else:
            total = conn.execute(f"SELECT COUNT(*) FROM cluster_summary cs {where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLUMNS}, {_origin_columns(conn)} FROM cluster_summary cs {where_sql} "
                f"ORDER BY {_ORDER_BY[sort]} LIMIT :limit OFFSET :offset",
                {**params, "limit": per, "offset": (page - 1) * per},
            ).fetchall()
        covers = _focused_covers(
            conn, [row["cluster_id"] for row in rows], status=status or None, year=year, season=season_key
        )
    items = [_summary(row, covers.get(row["cluster_id"])) for row in rows]
    if book_focus:
        items = [
            item.model_copy(
                update={
                    "nextDate": row["book_date"],
                    "nextPrecision": row["book_precision"] or "day",
                    "nextLabel": _book_next_label(row["title"], row["book_title"]),
                }
            )
            for item, row in zip(items, rows)
        ]
    return CatalogWorkPage(total=total, page=page, per=per, items=items)


# -- /works/{cluster_id} --------------------------------------------------------


def _entry(row: sqlite3.Row, link: Optional[tuple[str, float]] = None) -> CatalogEntry:
    data = db.row_to_dict(row)
    return CatalogEntry(
        id=data["id"],
        source=data["source"],
        externalId=data["external_id"],
        kind=data["kind"],
        format=data["format"],
        title=data["title"],
        titleNative=data["title_native"],
        titleRomaji=data["title_romaji"],
        titleEnglish=data["title_english"],
        synonyms=data["synonyms"],
        status=data["status"],
        startDate=data["start_date"],
        startPrecision=data["start_precision"],
        endDate=data["end_date"],
        endPrecision=data["end_precision"],
        season=data["season"],
        seasonYear=data["season_year"],
        episodes=data["episodes"],
        chapters=data["chapters"],
        volumes=data["volumes"],
        genres=data["genres"],
        studios=data["studios"],
        creators=data["creators"],
        popularity=data["popularity"],
        score=data["score"],
        url=data["url"],
        cover=data["cover"],
        sourceMaterial=data.get("source_material"),
        extra=data["extra"],
        linkMethod=link[0] if link else None,
        linkConfidence=link[1] if link else None,
    )


def _member_order(row: sqlite3.Row) -> tuple[Any, ...]:
    """AniList first, then novel -> manga -> anime -> web-novel -> book, then oldest start date."""
    start = row["start_date"]
    return (
        row["source"] != "anilist",
        _KIND_RANK.get(row["kind"], len(_KIND_RANK)),
        start is None,
        start or "",
        row["id"],
    )


def _best_links(rows: Iterable[sqlite3.Row]) -> dict[str, tuple[str, float]]:
    """entry id -> (method, confidence) of its strongest link; `rows` come strongest first."""
    best: dict[str, tuple[str, float]] = {}
    for row in rows:
        best.setdefault(row["a_id"], (row["method"], row["confidence"]))
    return best


@router.get("/works/{cluster_id:path}", response_model=CatalogWorkDetail)
def get_work(cluster_id: str) -> CatalogWorkDetail:
    with _open() as conn:
        summary = conn.execute(
            f"SELECT cs.cluster_id, cs.title, cs.kinds, cs.cover, {_origin_columns(conn)} "
            "FROM cluster_summary cs WHERE cs.cluster_id = ?",
            (cluster_id,),
        ).fetchone()
        if summary is None:
            raise HTTPException(status_code=404, detail=WORK_NOT_FOUND)

        members = sorted(
            conn.execute(
                "SELECT e.* FROM clusters c JOIN entries e ON e.id = c.entry_id WHERE c.cluster_id = ?",
                (cluster_id,),
            ),
            key=_member_order,
        )
        # How each non-AniList member joined: its strongest link into this work.
        joined = _best_links(
            conn.execute(
                "SELECT l.a_id, l.method, l.confidence FROM links l "
                "JOIN clusters ca ON ca.entry_id = l.a_id AND ca.cluster_id = :cid "
                "JOIN clusters cb ON cb.entry_id = l.b_id AND cb.cluster_id = :cid "
                "ORDER BY l.confidence DESC, l.b_id",
                {"cid": cluster_id},
            ).fetchall()
        )
        # Candidates: low-confidence links from outside rows to an AniList member of this work.
        candidate_links = _best_links(
            conn.execute(
                "SELECT l.a_id, l.method, l.confidence FROM links l "
                "JOIN clusters cb ON cb.entry_id = l.b_id AND cb.cluster_id = :cid "
                "LEFT JOIN clusters ca ON ca.entry_id = l.a_id "
                "WHERE l.confidence < :threshold AND (ca.cluster_id IS NULL OR ca.cluster_id <> :cid) "
                "ORDER BY l.confidence DESC, l.b_id",
                {"cid": cluster_id, "threshold": LINK_THRESHOLD},
            ).fetchall()
        )
        candidate_ids = sorted(candidate_links, key=lambda a_id: (-candidate_links[a_id][1], a_id))[:MAX_CANDIDATES]
        candidate_rows: dict[str, sqlite3.Row] = {}
        if candidate_ids:
            marks = ", ".join("?" * len(candidate_ids))
            for row in conn.execute(f"SELECT * FROM entries WHERE id IN ({marks})", candidate_ids):
                candidate_rows[row["id"]] = row

    return CatalogWorkDetail(
        id=summary["cluster_id"],
        title=summary["title"],
        kinds=_kinds(summary["kinds"]),
        entries=[_entry(row, None if row["source"] == "anilist" else joined.get(row["id"])) for row in members],
        candidates=[
            _entry(candidate_rows[a_id], candidate_links[a_id]) for a_id in candidate_ids if a_id in candidate_rows
        ],
        cover=summary["cover"],
        origin=_origin(summary["origin"]),
        novelCover=summary["novel_cover"],
        mangaCover=summary["manga_cover"],
    )


# -- /events --------------------------------------------------------------------

MAX_EVENT_DAYS = 62  # both ends included
MAX_EVENTS = 600
_EVENT_KINDS = ("anime", "book")
_START_FORMATS = ("TV", "TV_SHORT", "ONA", "MOVIE")  # the formats of an anime that has a start day to show
_DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CLOCK_RE = re.compile(r"([0-9]{1,2}):([0-5][0-9])")
_NUMBER = r"([0-9]+|[一二三四五六七八九十]+)"
# 第2期 / Season 2 / シーズン2 / 2nd Season in the title of a season (a cour or a bare '3期生' is not one).
_SEASON_LABELS = (
    re.compile(f"第{_NUMBER}期(?!生)"),
    re.compile(r"(?<![A-Za-z])(?:season|シーズン)\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"(?<![0-9])([0-9]+)(?:st|nd|rd|th)\s*season", re.IGNORECASE),
)
_VOLUME_LABEL_RE = re.compile(r"([0-9]+)巻")
_ANIME_EVENT_COLUMNS = (
    "e.id AS entry_id, e.title AS entry_title, e.cover AS entry_cover, c.cluster_id, "
    "cs.title AS work_title, cs.cover AS work_cover, cs.popularity"
)
# Unary '+' on kind / source keeps SQLite on the start_date index: a month holds a few thousand rows, while
# the kind / source indexes it would pick instead cover 100k books (about 10x slower on the 196k-row catalog).
_EVENT_JOINS = "FROM entries e JOIN clusters c ON c.entry_id = e.id JOIN cluster_summary cs ON cs.cluster_id = c.cluster_id "
_EVENT_DAY = "(e.start_precision = 'day' OR (e.start_precision IS NULL AND length(e.start_date) = 10))"


def _season_label(title: Optional[str]) -> Optional[str]:
    """'薬屋のひとりごと 第3期' / 'アオのハコ Season２' -> '第3期'. None when the title names no season."""
    text = unicodedata.normalize("NFKC", title or "")
    for pattern in _SEASON_LABELS:
        match = pattern.search(text)
        if match:
            return f"第{match.group(1)}期"
    return None


def _event_period(first: str, last: str) -> tuple[str, str]:
    """`from` / `to` as given when both are YYYY-MM-DD days, `to` is not before `from` and the span is at most 62 days."""
    days: list[date] = []
    for name, value in (("from", first), ("to", last)):
        try:
            if not _DAY_RE.fullmatch(value):
                raise ValueError(value)
            days.append(date.fromisoformat(value))
        except ValueError:
            raise _unprocessable(f"{name} must be a date (YYYY-MM-DD): {value}") from None
    if days[1] < days[0]:
        raise _unprocessable("to is before from")
    if (days[1] - days[0]).days + 1 > MAX_EVENT_DAYS:
        raise _unprocessable(f"the period is at most {MAX_EVENT_DAYS} days")
    return first, last


def _event_kinds(kind: Optional[str]) -> list[str]:
    names = [part.strip() for part in (kind or "").split(",") if part.strip()]
    for name in names:
        if name not in _EVENT_KINDS:
            raise _unprocessable(f"unknown kind: {name}")
    return [name for name in _EVENT_KINDS if name in names] or list(_EVENT_KINDS)


def _anime_start_events(conn: sqlite3.Connection, first: str, last: str) -> list[CatalogEvent]:
    """A TV / ONA season starting or a film opening on a day of the period (AniList rows with a day-precision date)."""
    rows = conn.execute(
        f"SELECT {_ANIME_EVENT_COLUMNS}, e.title_native, e.format, e.start_date {_EVENT_JOINS}"
        "WHERE +e.kind = 'anime' AND +e.source = 'anilist' AND e.start_date BETWEEN ? AND ? "
        f"AND {_EVENT_DAY} AND e.format IN ({', '.join('?' * len(_START_FORMATS))})",
        (first, last, *_START_FORMATS),
    ).fetchall()
    events: list[CatalogEvent] = []
    for row in rows:
        movie = row["format"] == "MOVIE"
        season = None if movie else _season_label(row["entry_title"]) or _season_label(row["title_native"])
        events.append(
            CatalogEvent(
                clusterId=row["cluster_id"],
                entryId=row["entry_id"],
                title=row["work_title"],
                kind="anime",
                event="movie" if movie else "season-start",
                date=row["start_date"],
                label="公開" if movie else f"{season} 放送開始" if season else "放送開始",
                cover=row["entry_cover"] or None,  # the season's own art only (the work cover is the original's: novel / manga)
                popularity=row["popularity"] or 0,
            )
        )
    return events


def _airing_moment(day: Any, clock: Any) -> Optional[tuple[str, Optional[str]]]:
    """AniList's next airing (broadcast day, 'HH:MM' that reads 24:30 for 00:30 the next day) as the real JST (day, 'HH:MM').

    The clock is None when it is missing or unreadable; None altogether when the day is not a date.
    """
    if not isinstance(day, str) or not _DAY_RE.fullmatch(day):
        return None
    try:
        when = date.fromisoformat(day)
    except ValueError:
        return None
    match = _CLOCK_RE.fullmatch(clock) if isinstance(clock, str) else None
    if match is None or int(match.group(1)) >= 48:
        return day, None
    hour = int(match.group(1))
    if hour >= 24:
        when, hour = when + timedelta(days=1), hour - 24
    return when.isoformat(), f"{hour:02d}:{match.group(2)}"


MAX_PROJECTED_WEEKS = 12  # one cour after the harvested next episode when the show's length is unknown


def _weekly_airings(day: str, clock: Any, episode: Any, total: Any) -> list[tuple[str, Any, Any, bool]]:
    """The harvested next airing (broadcast day) and the same slot every week after it, as (day, clock, episode, estimated).

    Projected only while the episode number is known: up to the show's last episode, or MAX_PROJECTED_WEEKS weeks.
    """
    airings: list[tuple[str, Any, Any, bool]] = [(day, clock, episode, False)]
    if not isinstance(episode, int) or isinstance(episode, bool) or not _DAY_RE.fullmatch(day):
        return airings
    weeks = MAX_PROJECTED_WEEKS
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        weeks = min(weeks, total - episode)
    start = date.fromisoformat(day)
    for week in range(1, weeks + 1):
        airings.append(((start + timedelta(weeks=week)).isoformat(), clock, episode + week, True))
    return airings


def _episode_events(
    conn: sqlite3.Connection, first: str, last: str, starts: set[tuple[str, str]]
) -> list[CatalogEvent]:
    """The airing anime's episodes in the period (AniList RELEASING rows that carry `extra.nextAiringEpisode`).

    The next airing is a snapshot from the last harvest; the weeks after it are projected on the same slot
    (`estimated`) so the calendar does not empty out past the harvest week. One episode per work and day.
    `starts` holds (entry, day) of the start events already listed: an entry's first episode is not
    listed again next to them.
    """
    # The stored day is the broadcast day, which is the day before the real one for 24:00 and later; a projection
    # may start from a next airing up to MAX_PROJECTED_WEEKS weeks before the period.
    earliest = (date.fromisoformat(first) - timedelta(weeks=MAX_PROJECTED_WEEKS, days=1)).isoformat()
    rows = conn.execute(
        f"SELECT {_ANIME_EVENT_COLUMNS}, e.episodes AS total, "
        "json_extract(e.extra, '$.nextAiringEpisode.date') AS day, "
        "json_extract(e.extra, '$.nextAiringEpisode.time') AS clock, "
        "json_extract(e.extra, '$.nextAiringEpisode.episode') AS episode "
        f"{_EVENT_JOINS}"
        "WHERE e.kind = 'anime' AND e.source = 'anilist' AND e.status = 'RELEASING' "
        "AND json_extract(e.extra, '$.nextAiringEpisode.date') BETWEEN ? AND ?",
        (earliest, last),
    ).fetchall()
    chosen: dict[tuple[str, str], tuple[tuple[bool, str, str], CatalogEvent]] = {}
    for row in rows:
        if not isinstance(row["day"], str):
            continue
        for broadcast, clock_text, episode, estimated in _weekly_airings(row["day"], row["clock"], row["episode"], row["total"]):
            moment = _airing_moment(broadcast, clock_text)
            if moment is None:
                continue
            day, clock = moment
            if not first <= day <= last or (row["entry_id"], day) in starts:
                continue
            event = CatalogEvent(
                clusterId=row["cluster_id"],
                entryId=row["entry_id"],
                title=row["work_title"],
                kind="anime",
                event="episode",
                date=day,
                time=clock,
                label=f"第{episode}話" if isinstance(episode, int) and not isinstance(episode, bool) else "次回放送",
                cover=row["entry_cover"] or None,  # the season's own art only (the work cover is the original's: novel / manga)
                popularity=row["popularity"] or 0,
                estimated=estimated,
            )
            # a listed airing beats a projection; then the earlier clock, then the entry
            order = (estimated, clock is None, clock or "", row["entry_id"])
            key = (row["cluster_id"], day)
            if key not in chosen or order < chosen[key][0]:
                chosen[key] = (order, event)
    return [event for _, event in chosen.values()]


def _volume_rank(label: str) -> int:
    match = _VOLUME_LABEL_RE.fullmatch(label)
    return int(match.group(1)) if match else sys.maxsize


def _book_events(conn: sqlite3.Connection, first: str, last: str) -> list[CatalogEvent]:
    """Books on sale on a day of the period, of works that have an AniList row (cluster_summary.popularity > 0).

    A work's books of one day are one event: the smaller volume.
    """
    rows = conn.execute(
        "SELECT e.id AS entry_id, e.title AS entry_title, e.start_date, e.format, c.cluster_id, "
        f"cs.title AS work_title, cs.popularity, {_origin_columns(conn)} "
        f"{_EVENT_JOINS}"
        "WHERE +e.kind = 'book' AND e.start_date BETWEEN ? AND ? AND e.start_precision = 'day' AND cs.popularity > 0",
        (first, last),
    ).fetchall()
    chosen: dict[tuple[str, str], tuple[tuple[int, str], CatalogEvent]] = {}
    for row in rows:
        label = _book_next_label(row["work_title"], row["entry_title"])
        key = (row["cluster_id"], row["start_date"])
        order = (_volume_rank(label), row["entry_id"])
        if key in chosen and chosen[key][0] <= order:
            continue
        chosen[key] = (
            order,
            CatalogEvent(
                clusterId=row["cluster_id"],
                entryId=row["entry_id"],
                title=row["work_title"],
                kind="book",
                event="release",
                date=row["start_date"],
                label=label,
                cover=_book_cover(row["format"], row["novel_cover"], row["manga_cover"]),
                popularity=row["popularity"] or 0,
            ),
        )
    return [event for _, event in chosen.values()]


def _event_order(event: CatalogEvent) -> tuple[Any, ...]:
    """Day, then the time of day (events without one last), the more popular work, then a fixed tie-break."""
    return (
        event.date,
        event.time is None,
        event.time or "",
        -event.popularity,
        event.clusterId,
        _EVENT_KINDS.index(event.kind),
        event.entryId,
    )


@router.get("/events", response_model=CatalogEventPage)
def list_events(
    from_: str = Query(..., alias="from", description="first day, YYYY-MM-DD"),
    to: str = Query(..., description="last day (included), YYYY-MM-DD; at most 62 days from `from`"),
    kind: Optional[str] = Query(None, description="comma separated: anime, book (both when omitted)"),
) -> CatalogEventPage:
    first, last = _event_period(from_, to)
    kinds = _event_kinds(kind)
    events: list[CatalogEvent] = []
    with _open() as conn:
        if "anime" in kinds:
            events = _anime_start_events(conn, first, last)
            events += _episode_events(conn, first, last, {(event.entryId, event.date) for event in events})
        if "book" in kinds:
            events += _book_events(conn, first, last)
    events.sort(key=_event_order)
    return CatalogEventPage(items=events[:MAX_EVENTS], truncated=len(events) > MAX_EVENTS)


# -- /export --------------------------------------------------------------------

_EXPORT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
}


def _stream(fmt: str) -> Iterator[bytes]:
    """Encoded chunks of the export. The connection lives (and closes) inside the generator."""
    # Starlette pulls a sync iterator from worker threads, one next() at a time, which may differ.
    with _open(check_same_thread=False) as conn:
        chunks = export.csv_chunks(conn) if fmt == "csv" else export.json_chunks(conn)
        try:
            for chunk in chunks:
                yield chunk.encode("utf-8")
        finally:
            chunks.close()  # release the cursor before the connection goes (a download may be cancelled)


@router.get("/export")
def export_catalog(format: str = Query("csv", description="csv or json")) -> StreamingResponse:
    if format not in export.FORMATS:
        raise _unprocessable(f"unknown format: {format}")
    # Fail with 404 now (not mid-stream) when there is nothing to export.
    with _open():
        pass
    return StreamingResponse(
        _stream(format),
        media_type=_EXPORT_TYPES[format],
        headers={"Content-Disposition": f"attachment; filename={export.export_filename(format)}"},
    )
