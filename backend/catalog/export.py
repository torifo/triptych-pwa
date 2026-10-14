"""Catalog export: every `entries` row as CSV / JSON.

One row per entry, in `entries.id` order, read through a cursor so the whole
catalog is never held in memory. `cluster_id` is null until the linker has run;
the `link_*` columns are filled for entries the linker attached to an AniList
entry (the strongest link when there are several).

CSV is UTF-8 with a BOM (Excel then opens it without garbling Japanese) and
list columns are joined with "、". JSON is one array of objects (lists stay
arrays), written as a stream.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence, TextIO

from ..timeutil import today_jst

FORMATS = ("csv", "json")
LIST_SEPARATOR = "、"
BATCH_ROWS = 500  # rows per streamed chunk

COLUMNS: tuple[str, ...] = (
    "cluster_id",
    "id",
    "source",
    "external_id",
    "kind",
    "format",
    "title",
    "title_native",
    "title_romaji",
    "title_english",
    "title_reading",
    "synonyms",
    "status",
    "start_date",
    "start_precision",
    "end_date",
    "end_precision",
    "season",
    "season_year",
    "episodes",
    "chapters",
    "volumes",
    "genres",
    "studios",
    "creators",
    "popularity",
    "score",
    "url",
    "link_b_id",
    "link_method",
    "link_confidence",
)
LIST_COLUMNS = ("synonyms", "genres", "studios", "creators")

_ENTRY_COLUMNS = tuple(
    c for c in COLUMNS if c != "cluster_id" and not c.startswith("link_")
)

# The strongest link of an entry (ties broken by b_id so the output is stable).
_ROWS_SQL = f"""
SELECT c.cluster_id AS cluster_id,
       {", ".join(f"e.{col} AS {col}" for col in _ENTRY_COLUMNS)},
       l.b_id AS link_b_id, l.method AS link_method, l.confidence AS link_confidence
FROM entries e
LEFT JOIN clusters c ON c.entry_id = e.id
LEFT JOIN links l ON l.a_id = e.id AND l.b_id = (
    SELECT b_id FROM links WHERE a_id = e.id ORDER BY confidence DESC, b_id LIMIT 1
)
ORDER BY e.id
"""


def iter_rows(conn: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    """Yields one dict (keys = COLUMNS) per entry. List columns come back as lists."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute(_ROWS_SQL):
            data = {column: row[column] for column in COLUMNS}
            for column in LIST_COLUMNS:
                data[column] = _decode_list(data[column])
            yield data
    finally:
        try:
            cursor.close()
        except sqlite3.ProgrammingError:
            pass  # the connection was closed first (an abandoned stream)


def _decode_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return [str(value)]
    return decoded if isinstance(decoded, list) else [decoded]


# 表計算ソフトが数式として解釈する先頭文字（第三者の題名が混ざるため、CSV だけ ' を前置する）
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list):
        value = LIST_SEPARATOR.join(str(item) for item in value)
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_chunks(conn: sqlite3.Connection) -> Iterator[str]:
    """The CSV as text chunks (the first one starts with the BOM)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    buffer.write("﻿")
    writer.writerow(COLUMNS)
    pending = 0
    for row in iter_rows(conn):
        writer.writerow([_csv_cell(row[column]) for column in COLUMNS])
        pending += 1
        if pending >= BATCH_ROWS:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            pending = 0
    tail = buffer.getvalue()
    if tail:
        yield tail


def json_chunks(conn: sqlite3.Connection) -> Iterator[str]:
    """The JSON array as text chunks (one object per line)."""
    parts: list[str] = []
    count = 0
    for row in iter_rows(conn):
        parts.append(("[\n" if count == 0 else ",\n") + json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        count += 1
        if len(parts) >= BATCH_ROWS:
            yield "".join(parts)
            parts = []
    if parts:
        yield "".join(parts)
    yield "\n]\n" if count else "[]\n"


def write_csv(conn: sqlite3.Connection, fp: TextIO) -> None:
    """Writes the CSV (BOM included) to a text stream opened with newline=""."""
    for chunk in csv_chunks(conn):
        fp.write(chunk)


def write_json(conn: sqlite3.Connection, fp: TextIO) -> None:
    for chunk in json_chunks(conn):
        fp.write(chunk)


def export_filename(fmt: str, day: Optional[str] = None) -> str:
    """`catalog-YYYYMMDD.<fmt>` for today (JST) unless `day` ("YYYY-MM-DD") is given."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown export format: {fmt}")
    stamp = (day or today_jst()).replace("-", "")
    return f"catalog-{stamp}.{fmt}"


def write_files(
    conn: sqlite3.Connection,
    out_dir: Path | str,
    formats: Sequence[str] = FORMATS,
) -> list[Path]:
    """Writes `catalog-YYYYMMDD.csv` / `.json` into `out_dir` (created) and returns the paths.

    Each file is written under a temporary name and moved into place at the end,
    so a failed export never leaves a half-written file with the final name.
    """
    unknown = [fmt for fmt in formats if fmt not in FORMATS]
    if unknown:
        raise ValueError(f"unknown export format: {', '.join(unknown)}")
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    day = today_jst()
    writers = {"csv": write_csv, "json": write_json}
    paths: list[Path] = []
    for fmt in formats:
        path = target / export_filename(fmt, day)
        partial = path.with_name(path.name + ".part")
        try:
            with open(partial, "w", encoding="utf-8", newline="") as fp:
                writers[fmt](conn, fp)
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)
        paths.append(path)
    return paths
