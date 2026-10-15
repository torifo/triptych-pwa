"""`/api/catalog/*` — browse and export the catalog (backend/catalog.db).

Read-only: every request opens the catalog DB on its own with `mode=ro`, so
the batch commands (`python -m backend.catalog build / link`) can keep writing
to it. When the file does not exist yet, `/stats` answers `ready: false` and
the other endpoints answer 404 `{"detail": "catalog not built"}`.
See specs/catalog/design.md ("API").
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional, Sequence
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import timeutil
from ..catalog import db, export
from ..catalog.dto import (
    CatalogEntry,
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
            entries=sum(entry_counts.values()),
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
    if sort == "upcoming":
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
        total = conn.execute(f"SELECT COUNT(*) FROM cluster_summary cs {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLUMNS} FROM cluster_summary cs {where_sql} "
            f"ORDER BY {_ORDER_BY[sort]} LIMIT :limit OFFSET :offset",
            {**params, "limit": per, "offset": (page - 1) * per},
        ).fetchall()
        covers = _focused_covers(
            conn, [row["cluster_id"] for row in rows], status=status or None, year=year, season=season_key
        )
    items = [_summary(row, covers.get(row["cluster_id"])) for row in rows]
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
            "SELECT cluster_id, title, kinds FROM cluster_summary WHERE cluster_id = ?", (cluster_id,)
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
    )


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
