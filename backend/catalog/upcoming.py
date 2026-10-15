"""目録前の近刊: the catalog's JPRO rows, read for the collector's NDL series API.

The JPRO harvester (`harvest/jpro.py`) writes books NDL has not catalogued yet into the
`ndl:<isbn>` rows with `extra.via = "jpro"` and the record's raw fields in `extra.jpro`.
`backend.adapters.ndl.series()` adds the ones whose title starts with the series title, so a
Library edition gets an upcoming volume the title search did not return, and their kind
(from the JPRO genre words) replaces the adapter's guess for a record without NDC.

Read-only (`mode=ro`): the batch keeps writing while the collector reads. A missing or
unreadable catalog gives an empty list. The rows (a few thousand) are read once and kept
until the catalog file or its WAL changes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from . import db

VIA = "jpro"
_KINDS = ("novel", "manga")
_SQL = (
    "SELECT external_id, title, start_date, start_precision, creators, extra FROM entries "
    "WHERE source = 'ndl' AND kind = 'book' AND json_extract(extra, '$.via') = ?"
)


@dataclass(frozen=True)
class UpcomingBook:
    isbn: str
    kind: str  # novel | manga
    title: str  # the JPRO title (NFKC), the volume usually written in it
    volume: Optional[str]  # dcndl:volume when JPRO gives one
    series_title: Optional[str]  # the label as JPRO writes it ("MF文庫J", "マーマレードコミックス ; CMMBL227")
    publisher: Optional[str]
    creators: tuple[str, ...]
    date: str  # YYYY-MM-DD
    precision: str  # day | month | year
    children: bool  # a 児童 book (the adapter keeps children's lines apart)


_cache: dict[str, tuple[tuple[Any, ...], list[UpcomingBook]]] = {}


def _stamp(path: Path) -> Optional[tuple[Any, ...]]:
    """Changes whenever the catalog is written (the main file or its WAL).

    An empty WAL counts as none: a reader creates one (and the -shm) without writing anything.
    """
    try:
        main = path.stat()
    except OSError:
        return None
    try:
        wal = Path(f"{path}-wal").stat()
        wal_stamp: Optional[tuple[int, int]] = (wal.st_mtime_ns, wal.st_size) if wal.st_size else None
    except OSError:
        wal_stamp = None
    return (main.st_mtime_ns, main.st_size, wal_stamp)


def _book(isbn: str, title: str, start: Optional[str], precision: Optional[str], creators: str, extra: str) -> Optional[UpcomingBook]:
    try:
        data = json.loads(extra or "{}")
        names = json.loads(creators or "[]")
    except ValueError:
        return None
    if not isinstance(data, dict) or not start or precision not in ("day", "month", "year"):
        return None
    kind = data.get("kindHint")
    if kind not in _KINDS:
        return None
    raw = data.get("jpro") if isinstance(data.get("jpro"), dict) else {}
    labels = raw.get("labels") if isinstance(raw.get("labels"), list) else []
    return UpcomingBook(
        isbn=isbn,
        kind=kind,
        title=str(raw.get("title") or title),
        volume=raw.get("volume") or None,
        series_title=raw.get("seriesTitle") or None,
        publisher=data.get("publisher") or None,
        creators=tuple(str(name) for name in names if name) if isinstance(names, list) else (),
        date=start,
        precision=precision,
        children=any("児童" in str(label) for label in labels),
    )


def _read(path: Path) -> list[UpcomingBook]:
    # URI paths are percent-encoded so '?', '#' and '%' in a path stay literal.
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(_SQL, (VIA,)).fetchall()
    except sqlite3.Error:  # not a database, no `entries` yet
        return []
    finally:
        conn.close()
    books = (_book(*row) for row in rows)
    return [book for book in books if book is not None]


def load(path: Optional[Path | str] = None) -> list[UpcomingBook]:
    """Every JPRO row of the catalog (`TRIPTYCH_CATALOG_DB` / backend/catalog.db by default)."""
    target = Path(path) if path is not None else db.db_path()
    stamp = _stamp(target)
    if stamp is None:
        return []
    key = str(target)
    kept = _cache.get(key)
    if kept is not None and kept[0] == stamp:
        return kept[1]
    books = _read(target)
    _cache[key] = (stamp, books)
    return books
