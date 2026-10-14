"""Catalog export: `backend.catalog.export` and `GET /api/catalog/export`."""

from __future__ import annotations

import csv
import inspect
import io
import json
import math
import sqlite3
import threading
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend import timeutil
from backend.api import catalog as catalog_api
from backend.catalog import db, export
from backend.catalog.__main__ import main as catalog_main
from backend.tests.catalog.test_api import (  # noqa: F401  (fixtures are re-used from test_api)
    A100,
    E,
    build_big_catalog,
    build_small_catalog,
    built,
    catalog_path,
    client,
)

BOM = b"\xef\xbb\xbf"
DAY = "2026-10-14"


@pytest.fixture()
def conn(built) -> sqlite3.Connection:
    # a plain connection (no row_factory), like any other caller might pass
    connection = sqlite3.connect(built)
    yield connection
    connection.close()


@pytest.fixture()
def fixed_day(monkeypatch):
    monkeypatch.setattr(export, "today_jst", lambda: DAY)


def parse_csv(data: bytes) -> list[dict[str, str]]:
    assert data.startswith(BOM)
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))


def csv_bytes(connection: sqlite3.Connection) -> bytes:
    buffer = io.StringIO(newline="")
    export.write_csv(connection, buffer)
    return buffer.getvalue().encode("utf-8")


# -- iter_rows ----------------------------------------------------------------


def test_iter_rows_returns_every_entry_once_in_id_order(conn):
    rows = export.iter_rows(conn)
    assert inspect.isgenerator(rows)  # rows are pulled one at a time
    result = list(rows)
    assert len(result) == 25
    assert [row["id"] for row in result] == sorted(row["id"] for row in result)
    assert all(tuple(row) == export.COLUMNS for row in result)


def test_iter_rows_fields(conn):
    rows = {row["id"]: row for row in export.iter_rows(conn)}
    anime = rows["anilist:102"]
    assert anime["cluster_id"] == A100
    assert (anime["source"], anime["external_id"], anime["kind"], anime["format"]) == ("anilist", "102", "anime", "TV")
    assert anime["title"] == "薬屋のひとりごと" and anime["title_english"] == "The Apothecary Diaries"
    assert anime["status"] == "RELEASING"
    assert (anime["start_date"], anime["start_precision"], anime["end_date"]) == ("2023-10-22", "day", None)
    assert (anime["season"], anime["season_year"], anime["episodes"]) == ("FALL", 2023, 24)
    assert anime["genres"] == ["Drama", "Mystery"] and anime["studios"] == ["TOHO animation", "OLM"]
    assert anime["synonyms"] == [] and anime["creators"] == []
    assert (anime["popularity"], anime["score"]) == (90000, 87)
    assert rows["syoboi:5000"]["title_reading"] == "くすりやのひとりごと"
    assert (rows["anilist:101"]["end_date"], rows["anilist:101"]["end_precision"]) == ("2020-01-01", "year")
    assert rows["anilist:200"]["synonyms"] == []
    assert rows["anilist:202"]["synonyms"] == ["Ascendance of a Bookworm"]


def test_iter_rows_link_columns(conn):
    rows = {row["id"]: row for row in export.iter_rows(conn)}
    link = lambda row: (row["link_b_id"], row["link_method"], row["link_confidence"])  # noqa: E731
    assert link(rows["syoboi:5000"]) == ("anilist:102", "title-year", 0.95)
    assert link(rows["ndl:books/9784000000010"]) == ("anilist:100", "series", 0.85)
    assert link(rows["syoboi:9999"]) == ("anilist:102", "title-exact", 0.7)
    # three links: only the strongest is exported, and the entry stays one row
    assert link(rows["narou:n1234a"]) == ("anilist:100", "title-exact", 0.9)
    assert link(rows["kakuyomu:77"]) == ("anilist:101", "title-prefix", 0.65)
    # AniList entries are link targets, not sources
    assert link(rows["anilist:100"]) == (None, None, None)
    assert link(rows["narou:n0001a"]) == (None, None, None)


def test_iter_rows_cluster_id(conn):
    rows = {row["id"]: row for row in export.iter_rows(conn)}
    assert rows["anilist:100"]["cluster_id"] == A100
    assert rows["narou:n1234a"]["cluster_id"] == A100
    assert rows["kakuyomu:77"]["cluster_id"] == "kakuyomu:77"
    assert rows["syoboi:9999"]["cluster_id"] is None  # harvested after the last link run


def test_iter_rows_before_linking_has_no_cluster_or_link(tmp_path):
    connection = db.connect(tmp_path / "unlinked.db")
    db.upsert_entries(connection, [E("anilist", "1", "anime", "A"), E("narou", "n1a", "web-novel", "B")])
    rows = list(export.iter_rows(connection))
    assert [row["id"] for row in rows] == ["anilist:1", "narou:n1a"]
    assert all(row["cluster_id"] is None and row["link_b_id"] is None for row in rows)
    connection.close()


def test_iter_rows_does_not_hold_the_catalog_in_memory(tmp_path):
    path = build_big_catalog(tmp_path / "big.db", entries=30_000, works=20_000)
    connection = sqlite3.connect(path)
    tracemalloc.start()
    try:
        count = sum(1 for _ in export.iter_rows(connection))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        connection.close()
    assert count == 30_000
    assert peak < 5_000_000  # holding 30k rows as dicts would take several times this


def test_iter_rows_can_be_abandoned_midway(conn):
    rows = export.iter_rows(conn)
    next(rows)
    rows.close()
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 25


# -- CSV ----------------------------------------------------------------------


def test_csv_starts_with_a_bom_and_the_header(conn):
    data = csv_bytes(conn)
    assert data[:3] == BOM
    assert data[3:6] != BOM  # exactly one
    header = data.decode("utf-8-sig").splitlines()[0]
    assert header.split(",") == list(export.COLUMNS)
    assert header.startswith("cluster_id,id,source,")
    assert {"cluster_id", "source", "title", "kind", "status", "start_date", "popularity", "link_b_id", "link_method",
            "link_confidence"} <= set(export.COLUMNS)


def test_csv_rows(conn):
    rows = {row["id"]: row for row in parse_csv(csv_bytes(conn))}
    assert len(rows) == 25
    anime = rows["anilist:102"]
    assert anime["genres"] == "Drama、Mystery"  # lists are joined with 「、」
    assert anime["studios"] == "TOHO animation、OLM"
    assert anime["synonyms"] == "" and anime["end_date"] == ""  # null is an empty cell
    assert anime["popularity"] == "90000" and anime["start_date"] == "2023-10-22"
    assert anime["cluster_id"] == A100 and anime["link_b_id"] == ""
    assert rows["narou:n1234a"]["link_confidence"] == "0.9"
    assert rows["syoboi:9999"]["cluster_id"] == "" and rows["syoboi:9999"]["link_b_id"] == "anilist:102"
    assert rows["anilist:202"]["synonyms"] == "Ascendance of a Bookworm"


def test_csv_quotes_commas_quotes_and_newlines(tmp_path):
    connection = db.connect(tmp_path / "quirks.db")
    db.upsert_entries(
        connection,
        [
            E("anilist", "1", "manga", 'A, "B"\nC', creators=["山田、太郎", "Doe, John"], synonyms=["x"]),
            E("narou", "n2b", "web-novel", "=SUM(A1)", url="https://example.com/?a=1&b=2"),
        ],
    )
    rows = {row["id"]: row for row in parse_csv(csv_bytes(connection))}
    assert rows["anilist:1"]["title"] == 'A, "B"\nC'
    assert rows["anilist:1"]["creators"] == "山田、太郎、Doe, John"
    assert rows["narou:n2b"]["title"] == "'=SUM(A1)"  # neutralized so spreadsheets do not run it
    connection.close()


def test_csv_of_an_empty_catalog_is_just_the_header(tmp_path):
    connection = db.connect(tmp_path / "empty.db")
    data = csv_bytes(connection)
    assert data.startswith(BOM)
    assert data.decode("utf-8-sig").splitlines() == [",".join(export.COLUMNS)]
    connection.close()


# -- JSON ---------------------------------------------------------------------


def json_text(connection: sqlite3.Connection) -> str:
    buffer = io.StringIO()
    export.write_json(connection, buffer)
    return buffer.getvalue()


def test_json_is_one_array_with_a_row_per_entry(conn):
    text = json_text(conn)
    data = json.loads(text)
    assert isinstance(data, list) and len(data) == 25
    assert [row["id"] for row in data] == [row["id"] for row in export.iter_rows(conn)]
    assert all(list(row) == list(export.COLUMNS) for row in data)
    assert "薬屋のひとりごと" in text  # not \u-escaped
    assert not text.startswith("﻿")


def test_json_values_keep_their_types(conn):
    rows = {row["id"]: row for row in json.loads(json_text(conn))}
    anime = rows["anilist:102"]
    assert anime["genres"] == ["Drama", "Mystery"] and anime["synonyms"] == []
    assert anime["popularity"] == 90000 and anime["season_year"] == 2023 and anime["end_date"] is None
    assert rows["narou:n1234a"]["link_confidence"] == 0.9
    assert rows["narou:n1234a"]["link_b_id"] == "anilist:100"
    assert rows["syoboi:9999"]["cluster_id"] is None


def test_json_of_an_empty_catalog_is_an_empty_array(tmp_path):
    connection = db.connect(tmp_path / "empty.db")
    assert json.loads(json_text(connection)) == []
    connection.close()


@pytest.mark.parametrize("batch", [1, 2, 7, 500])
def test_chunking_does_not_change_the_output(conn, monkeypatch, batch):
    reference_csv, reference_json = csv_bytes(conn), json_text(conn)
    monkeypatch.setattr(export, "BATCH_ROWS", batch)
    assert csv_bytes(conn) == reference_csv
    assert json_text(conn) == reference_json
    assert len(list(export.csv_chunks(conn))) == math.ceil(25 / batch)  # 25 rows plus the header, no empty tail


# -- write_files --------------------------------------------------------------


def test_write_files_creates_the_directory_and_both_files(built, conn, tmp_path, fixed_day):
    out = tmp_path / "out" / "catalog"
    paths = export.write_files(conn, out)
    assert paths == [out / "catalog-20261014.csv", out / "catalog-20261014.json"]
    assert sorted(path.name for path in out.iterdir()) == ["catalog-20261014.csv", "catalog-20261014.json"]
    assert len(parse_csv(paths[0].read_bytes())) == 25
    assert len(json.loads(paths[1].read_text(encoding="utf-8"))) == 25
    assert paths[0].read_bytes()[:3] == BOM and paths[0].read_bytes()[3:6] != BOM
    assert not paths[1].read_bytes().startswith(BOM)


def test_write_files_one_format_in_the_requested_order(conn, tmp_path, fixed_day):
    assert export.write_files(conn, tmp_path / "a", formats=("json",)) == [tmp_path / "a" / "catalog-20261014.json"]
    assert [p.suffix for p in export.write_files(conn, tmp_path / "b", formats=("json", "csv"))] == [".json", ".csv"]
    assert [p.suffix for p in export.write_files(conn, tmp_path / "c", formats=["csv"])] == [".csv"]


def test_write_files_overwrites_and_leaves_no_temporary_files(conn, tmp_path, fixed_day):
    out = tmp_path / "out"
    export.write_files(conn, out)
    export.write_files(conn, out)
    assert sorted(path.name for path in out.iterdir()) == ["catalog-20261014.csv", "catalog-20261014.json"]


def test_write_files_rejects_an_unknown_format_before_touching_the_disk(conn, tmp_path):
    out = tmp_path / "never"
    with pytest.raises(ValueError, match="xlsx"):
        export.write_files(conn, out, formats=("csv", "xlsx"))
    assert not out.exists()


def test_a_failing_export_leaves_no_file_behind(conn, tmp_path, fixed_day, monkeypatch):
    def broken(_conn: Any):
        yield next(iter(export.iter_rows(_conn)))
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(export, "iter_rows", broken)
    with pytest.raises(RuntimeError):
        export.write_files(conn, tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_file_names_use_the_japanese_date(conn, tmp_path, monkeypatch):
    instant = datetime(2026, 10, 13, 15, 30, tzinfo=timezone.utc)  # 00:30 on the 14th in Japan

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(timeutil, "datetime", FrozenDatetime)
    assert [p.name for p in export.write_files(conn, tmp_path / "out")] == [
        "catalog-20261014.csv",
        "catalog-20261014.json",
    ]
    assert export.export_filename("csv") == "catalog-20261014.csv"
    assert export.export_filename("json", "2025-01-02") == "catalog-20250102.json"
    with pytest.raises(ValueError):
        export.export_filename("xml")


# -- the CLI ------------------------------------------------------------------


def test_cli_export_writes_both_files(built, tmp_path, fixed_day):
    out = tmp_path / "local" / "catalog"
    assert catalog_main(["export", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["catalog-20261014.csv", "catalog-20261014.json"]
    assert len(json.loads((out / "catalog-20261014.json").read_text(encoding="utf-8"))) == 25


def test_cli_export_one_format(built, tmp_path, fixed_day):
    out = tmp_path / "out"
    assert catalog_main(["export", "--format", "csv", "--out", str(out)]) == 0
    assert [p.name for p in out.iterdir()] == ["catalog-20261014.csv"]


# -- GET /api/catalog/export --------------------------------------------------


def test_export_csv_download(built, client, fixed_day, tmp_path):
    response = client.get("/api/catalog/export", params={"format": "csv"})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == "attachment; filename=catalog-20261014.csv"
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.content[:3] == BOM
    assert len(parse_csv(response.content)) == 25
    # the download is byte for byte what `python -m backend.catalog export` writes
    (path,) = export.write_files(sqlite3.connect(built), tmp_path / "out", formats=("csv",))
    assert response.content == path.read_bytes()


def test_export_json_download(built, client, fixed_day, tmp_path):
    response = client.get("/api/catalog/export", params={"format": "json"})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == "attachment; filename=catalog-20261014.json"
    assert response.headers["content-type"] == "application/json"
    assert len(response.json()) == 25
    (path,) = export.write_files(sqlite3.connect(built), tmp_path / "out", formats=("json",))
    assert response.content == path.read_bytes()


def test_export_defaults_to_csv(built, client):
    response = client.get("/api/catalog/export")
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].endswith(".csv")


def test_export_count_matches_the_catalog(built, client):
    entries = sqlite3.connect(built).execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    assert len(client.get("/api/catalog/export", params={"format": "json"}).json()) == entries
    assert len(parse_csv(client.get("/api/catalog/export", params={"format": "csv"}).content)) == entries


def test_export_rejects_an_unknown_format(built, client):
    assert client.get("/api/catalog/export", params={"format": "xlsx"}).status_code == 422


def test_export_of_a_missing_catalog_is_404(catalog_path, client):
    response = client.get("/api/catalog/export", params={"format": "json"})
    assert response.status_code == 404 and response.json() == {"detail": "catalog not built"}
    assert not catalog_path.exists()


def test_export_file_name_follows_the_japanese_date(built, client, monkeypatch):
    instant = datetime(2026, 10, 13, 15, 30, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(timeutil, "datetime", FrozenDatetime)
    assert client.get("/api/catalog/export").headers["content-disposition"].endswith("catalog-20261014.csv")


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_streamed_export_can_be_pulled_from_a_different_thread_each_time(built, monkeypatch, fmt):
    """The server pulls a sync stream from worker threads, so consecutive chunks may come from different ones."""
    monkeypatch.setattr(export, "BATCH_ROWS", 2)
    stream = catalog_api._stream(fmt)
    chunks: list[bytes] = []
    errors: list[BaseException] = []

    def pull() -> None:
        try:
            chunks.append(next(stream))
        except StopIteration:
            chunks.append(b"")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    while not chunks or chunks[-1] != b"":
        thread = threading.Thread(target=pull)
        thread.start()
        thread.join()
        assert not errors, errors
    assert len(chunks) > 5
    body = b"".join(chunks)
    if fmt == "json":
        assert len(json.loads(body)) == 25
    else:
        assert len(parse_csv(body)) == 25


def test_abandoning_a_stream_closes_its_connection(built, monkeypatch):
    opened: list[sqlite3.Connection] = []
    real_connect = catalog_api._connect

    def tracking_connect(**kwargs):
        connection = real_connect(**kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(catalog_api, "_connect", tracking_connect)
    monkeypatch.setattr(export, "BATCH_ROWS", 2)
    stream = catalog_api._stream("csv")
    next(stream)
    stream.close()
    (connection,) = opened
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_export_does_not_modify_the_catalog(built, client):
    before = Path(built).read_bytes()
    client.get("/api/catalog/export", params={"format": "json"})
    client.get("/api/catalog/export", params={"format": "csv"})
    assert Path(built).read_bytes() == before


def test_csv_cell_neutralizes_formula_prefixes():
    from backend.catalog.export import _csv_cell

    assert _csv_cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    assert _csv_cell("+1") == "'+1"
    assert _csv_cell("@cmd") == "'@cmd"
    assert _csv_cell(["=a", "b"]) == "'=a、b"
    assert _csv_cell("薬屋のひとりごと") == "薬屋のひとりごと"
    assert _csv_cell(42) == 42
    assert _csv_cell(None) == ""
