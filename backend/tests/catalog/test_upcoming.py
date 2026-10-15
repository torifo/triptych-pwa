"""Tests for backend/catalog/upcoming.py: the JPRO rows the NDL series API reads (tmp_path DBs only)."""

from __future__ import annotations

from datetime import date

from backend.catalog import db, upcoming
from backend.catalog.harvest import jpro

from .test_harvest_jpro import recorded_records


def _write(path, entries) -> None:
    conn = db.connect(path)
    db.upsert_entries(conn, entries)
    conn.close()


def test_reads_only_the_rows_the_jpro_harvester_wrote(tmp_path):
    path = tmp_path / "catalog.db"
    entries, _ = jpro.entries_of(recorded_records(), jpro.release_floor(date(2026, 10, 15)))
    catalogued = db.Entry(
        source="ndl", external_id="9784000000001", kind="book", title="目録済み 1",
        start_date="2026-11-01", start_precision="month", extra={"kindHint": "novel", "series": "目録済み"},
    )
    _write(path, [*entries, catalogued])

    books = {b.isbn: b for b in upcoming.load(path)}

    assert set(books) == {e.external_id for e in entries}
    yamato = books["9784065448236"]
    assert (yamato.kind, yamato.title, yamato.volume, yamato.series_title) == (
        "manga", "MADE IN YAMATO(1)", None, "ヤンマガKCスペシャル",
    )
    assert (yamato.date, yamato.precision, yamato.publisher, yamato.creators) == (
        "2026-09-17", "day", "講談社", ("卯月まさゆき",),
    )
    assert not yamato.children
    assert books["9784593106486"].children  # 児童 中学生以下対象
    assert books["9784825102354"].volume == "14"


def test_a_missing_or_broken_catalog_gives_nothing(tmp_path):
    assert upcoming.load(tmp_path / "none.db") == []
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database at all" * 100)
    assert upcoming.load(broken) == []
    empty = tmp_path / "empty.db"
    import sqlite3

    sqlite3.connect(empty).close()  # a file with no tables
    assert upcoming.load(empty) == []


def test_the_rows_are_read_again_after_the_catalog_changes(tmp_path):
    path = tmp_path / "catalog.db"
    entries, _ = jpro.entries_of(recorded_records(), jpro.release_floor(date(2026, 10, 15)))
    _write(path, entries[:2])
    first = upcoming.load(path)
    assert len(first) == 2
    assert upcoming.load(path) is first  # unchanged file: the kept list

    _write(path, entries[2:4])
    assert len(upcoming.load(path)) == 4


def test_default_path_is_the_catalog_db(tmp_path, monkeypatch):
    path = tmp_path / "catalog.db"
    entries, _ = jpro.entries_of(recorded_records(), jpro.release_floor(date(2026, 10, 15)))
    _write(path, entries[:1])
    monkeypatch.setenv(db.DB_ENV_VAR, str(path))
    assert [b.isbn for b in upcoming.load()] == [entries[0].external_id]
