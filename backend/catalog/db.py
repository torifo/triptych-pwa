"""Catalog database access (backend/catalog.db). Reference data only."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

CATALOG_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = CATALOG_DIR.parent / "catalog.db"
DB_ENV_VAR = "TRIPTYCH_CATALOG_DB"

KINDS = ("anime", "manga", "novel", "web-novel", "book")


def db_path() -> Path:
    return Path(os.environ.get(DB_ENV_VAR) or DEFAULT_DB_PATH)


def connect(path: Optional[Path | str] = None, *, create: bool = True) -> sqlite3.Connection:
    """Opens the catalog DB (creating the schema when `create`)."""
    target = Path(path) if path is not None else db_path()
    if not create and not target.exists():
        raise FileNotFoundError(target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if create:
        init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript((CATALOG_DIR / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Entry:
    """One row of `entries`. List/dict fields are serialized to JSON on write."""

    source: str
    external_id: str
    kind: str
    title: str
    format: Optional[str] = None
    title_native: Optional[str] = None
    title_romaji: Optional[str] = None
    title_english: Optional[str] = None
    title_reading: Optional[str] = None
    synonyms: list[str] = field(default_factory=list)
    status: Optional[str] = None
    start_date: Optional[str] = None
    start_precision: Optional[str] = None
    end_date: Optional[str] = None
    end_precision: Optional[str] = None
    season: Optional[str] = None
    season_year: Optional[int] = None
    episodes: Optional[int] = None
    chapters: Optional[int] = None
    volumes: Optional[int] = None
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    creators: list[str] = field(default_factory=list)
    popularity: Optional[int] = None
    score: Optional[int] = None
    source_material: Optional[str] = None
    url: Optional[str] = None
    cover: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return entry_id(self.source, self.external_id)


def entry_id(source: str, external_id: str) -> str:
    return f"{source}:{external_id}"


_JSON_FIELDS = ("synonyms", "genres", "tags", "studios", "creators", "extra")


def upsert_entry(conn: sqlite3.Connection, entry: Entry, *, fetched_at: Optional[str] = None) -> None:
    if entry.kind not in KINDS:
        raise ValueError(f"unknown kind: {entry.kind}")
    row = asdict(entry)
    for key in _JSON_FIELDS:
        row[key] = json.dumps(row[key], ensure_ascii=False)
    row["id"] = entry.id
    row["fetched_at"] = fetched_at or utc_now()
    columns = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "id")
    conn.execute(
        f"INSERT INTO entries ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        row,
    )


def upsert_entries(conn: sqlite3.Connection, entries: Iterable[Entry]) -> int:
    count = 0
    fetched_at = utc_now()
    for entry in entries:
        upsert_entry(conn, entry, fetched_at=fetched_at)
        count += 1
    conn.commit()
    return count


def replace_relations(
    conn: sqlite3.Connection, from_id: str, edges: Iterable[tuple[str, str]]
) -> None:
    """Replaces all outgoing edges of `from_id` with (to_id, relation_type) pairs."""
    conn.execute("DELETE FROM relations WHERE from_id = ?", (from_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO relations (from_id, to_id, relation_type) VALUES (?, ?, ?)",
        [(from_id, to_id, rtype) for to_id, rtype in edges],
    )


def slice_status(conn: sqlite3.Connection, source: str, slice_key: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM harvest_state WHERE source = ? AND slice_key = ?", (source, slice_key)
    ).fetchone()


def is_slice_done(conn: sqlite3.Connection, source: str, slice_key: str) -> bool:
    row = slice_status(conn, source, slice_key)
    return row is not None and row["status"] == "done"


def mark_slice(
    conn: sqlite3.Connection,
    source: str,
    slice_key: str,
    status: str,
    *,
    fetched: int = 0,
    cursor: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    if status not in ("pending", "done", "error"):
        raise ValueError(status)
    conn.execute(
        "INSERT INTO harvest_state (source, slice_key, status, fetched, cursor, message, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source, slice_key) DO UPDATE SET status = excluded.status, "
        "fetched = excluded.fetched, cursor = excluded.cursor, message = excluded.message, "
        "updated_at = excluded.updated_at",
        (source, slice_key, status, fetched, cursor, message, utc_now()),
    )
    conn.commit()


def reset_source(conn: sqlite3.Connection, source: str) -> None:
    """Forgets resume state so the next run re-fetches every slice (rows are kept and updated)."""
    conn.execute("DELETE FROM harvest_state WHERE source = ?", (source,))
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in _JSON_FIELDS:
        if key in data and isinstance(data[key], str):
            data[key] = json.loads(data[key])
    return data
