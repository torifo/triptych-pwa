"""`/api/catalog/*` against a synthetic catalog DB (no network, no real catalog.db)."""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import timeutil
from backend.api import catalog as catalog_api
from backend.catalog import db
from backend.catalog.text import normalize_title, search_text

TODAY = "2026-10-14"


# -- synthetic catalog builders (also used by test_export.py) ------------------


def E(source: str, external_id: str, kind: str, title: str, **fields: Any) -> db.Entry:
    return db.Entry(source=source, external_id=external_id, kind=kind, title=title, **fields)


def _fts_text(entry: db.Entry) -> str:
    """The search text the linker would store for this entry."""
    return search_text(
        [entry.title, entry.title_native, entry.title_romaji, entry.title_english, entry.title_reading, *entry.synonyms]
    )


def add_work(
    conn: sqlite3.Connection,
    cluster_id: str,
    title: str,
    members: Sequence[db.Entry],
    *,
    anime_status: Optional[str] = None,
    first_year: Optional[int] = None,
    last_year: Optional[int] = None,
    seasons: Sequence[str] = (),
    next_date: Optional[str] = None,
    next_precision: Optional[str] = None,
    next_label: Optional[str] = None,
    popularity: int = 0,
    cover: Optional[str] = None,
) -> None:
    """Inserts members, their cluster rows, FTS rows and the cluster_summary row."""
    db.upsert_entries(conn, members)
    kinds = sorted({member.kind for member in members})
    for member in members:
        conn.execute("INSERT INTO clusters (entry_id, cluster_id) VALUES (?, ?)", (member.id, cluster_id))
        conn.execute(
            "INSERT INTO entries_fts (entry_id, cluster_id, text) VALUES (?, ?, ?)",
            (member.id, cluster_id, _fts_text(member)),
        )
    conn.execute(
        "INSERT INTO cluster_summary (cluster_id, title, title_norm, kinds, has_anime, has_manga, has_novel, "
        "has_web_novel, has_book, entry_count, anime_status, first_year, last_year, seasons, next_date, "
        "next_precision, next_label, popularity, cover) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cluster_id,
            title,
            normalize_title(title),
            json.dumps(kinds),
            int("anime" in kinds),
            int("manga" in kinds),
            int("novel" in kinds),
            int("web-novel" in kinds),
            int("book" in kinds),
            len(members),
            anime_status,
            first_year,
            last_year,
            json.dumps(list(seasons)),
            next_date,
            next_precision,
            next_label,
            popularity,
            cover,
        ),
    )
    conn.commit()


def add_links(conn: sqlite3.Connection, links: Sequence[tuple[str, str, str, float]]) -> None:
    conn.executemany("INSERT INTO links (a_id, b_id, method, confidence) VALUES (?, ?, ?, ?)", links)
    conn.commit()


# Cluster ids of the small catalog (popularity order: A100 A300 A200 A400 A500 N1 K77 A700 A800 A900 B1)
A100, A200, A300, A400, A500 = "anilist:100", "anilist:200", "anilist:300", "anilist:400", "anilist:500"
N1, B1, K77 = "narou:n0001a", "ndl:books/9784000000001", "kakuyomu:77"  # B1: ids may contain '/'
A700, A800, A900 = "anilist:700", "anilist:800", "anilist:900"
POPULARITY_ORDER = [A100, A300, A200, A400, A500, N1, K77, A700, A800, A900, B1]
RECENT_ORDER = [A400, A300, B1, A100, A200, A500, K77, N1, A800, A700, A900]


def build_small_catalog(path: Path) -> Path:
    """11 works / 25 entries / 9 links, hand made to cover every filter and sort."""
    conn = db.connect(path)
    add_work(
        conn, A100, "薬屋のひとりごと",
        [
            E("anilist", "100", "novel", "薬屋のひとりごと", format="NOVEL", start_date="2011-10-01", start_precision="day",
              creators=["日向夏"], url="https://anilist.co/manga/100"),
            E("anilist", "101", "manga", "薬屋のひとりごと", format="MANGA", start_date="2017-05-01", start_precision="month",
              end_date="2020-01-01", end_precision="year"),
            E("anilist", "102", "anime", "薬屋のひとりごと", format="TV", status="RELEASING", start_date="2023-10-22",
              start_precision="day", season="FALL", season_year=2023, episodes=24, genres=["Drama", "Mystery"],
              studios=["TOHO animation", "OLM"], title_english="The Apothecary Diaries", title_romaji="Kusuriya no Hitorigoto",
              popularity=90000, score=87),
            E("anilist", "105", "anime", "薬屋のひとりごと 劇場版", format="MOVIE", start_date="2024-06-01", start_precision="day"),
            E("anilist", "106", "anime", "薬屋のひとりごと 特別編", format="SPECIAL"),
            E("syoboi", "5000", "anime", "薬屋のひとりごと", title_reading="くすりやのひとりごと", start_date="2023-10-22",
              start_precision="day"),
            E("narou", "n1234a", "web-novel", "薬屋のひとりごと", status="完結", start_date="2012-01-01", start_precision="year"),
            E("ndl", "books/9784000000010", "book", "薬屋のひとりごと 1", format="文庫", start_date="2014-05-01",
              start_precision="month", extra={"isbn": "9784000000010", "publisher": "ヒーロー文庫"}),
        ],
        anime_status="RELEASING", first_year=2011, last_year=2025, seasons=["2023-FALL", "2025-WINTER"],
        next_date="2026-10-20", next_precision="day", next_label="第3期", popularity=90000,
    )
    add_work(
        conn, A200, "本好きの下剋上",
        [
            E("anilist", "200", "novel", "本好きの下剋上", format="NOVEL", start_date="2013-09-01", start_precision="day"),
            E("anilist", "201", "manga", "本好きの下剋上", format="MANGA", start_date="2015-01-01", start_precision="year"),
            E("anilist", "202", "anime", "本好きの下剋上", format="TV", status="FINISHED", start_date="2019-10-03",
              start_precision="day", synonyms=["Ascendance of a Bookworm"]),
        ],
        anime_status="FINISHED", first_year=2015, last_year=2022, seasons=["2019-FALL", "2020-SPRING", "2022-SPRING"],
        popularity=70000,
    )
    add_work(
        conn, A300, "転生したらスライムだった件",
        [
            E("anilist", "300", "novel", "転生したらスライムだった件", format="NOVEL", start_date="2014-01-01", start_precision="year"),
            E("anilist", "301", "manga", "転生したらスライムだった件", format="MANGA"),
            E("anilist", "302", "anime", "転生したらスライムだった件", format="TV", status="RELEASING", synonyms=["Slime"]),
        ],
        anime_status="RELEASING", first_year=2014, last_year=2026, seasons=["2018-FALL", "2021-WINTER", "2024-SPRING"],
        next_date=TODAY, next_precision="day", next_label="最新話", popularity=80000,
    )
    add_work(
        conn, A400, "無職転生",
        [
            E("anilist", "400", "novel", "無職転生", format="NOVEL"),
            E("anilist", "402", "anime", "無職転生", format="TV", status="NOT_YET_RELEASED"),
        ],
        anime_status="NOT_YET_RELEASED", first_year=2014, last_year=2027, seasons=["2021-WINTER", "2027-WINTER"],
        next_date="2027-01-04", next_precision="day", next_label="第3期", popularity=70000,
    )
    add_work(
        conn, A500, "Re:ゼロから始める異世界生活",
        [
            E("anilist", "500", "novel", "Re:ゼロから始める異世界生活", format="NOVEL"),
            E("anilist", "502", "anime", "Re:ゼロから始める異世界生活", format="TV", status="FINISHED"),
        ],
        anime_status="FINISHED", first_year=2014, last_year=2020, seasons=["2016-SPRING"],
        next_date="2026-10-13",  # yesterday: not upcoming
        popularity=60000,
    )
    add_work(
        conn, N1, "異世界のんびり農家",
        [E("narou", "n0001a", "web-novel", "異世界のんびり農家", start_date="2013-01-01", start_precision="year")],
        first_year=2013, last_year=2013, popularity=5000,
    )
    add_work(
        conn, B1, "新刊の小説",
        [E("ndl", "books/9784000000001", "book", "新刊の小説", format="文庫", start_date="2026-12-01", start_precision="month")],
        first_year=2026, last_year=2026, next_date="2026-12-01", next_precision="month", next_label="発売予定", popularity=0,
    )
    add_work(
        conn, K77, "薬屋のひとりごと外伝",
        [E("kakuyomu", "77", "web-novel", "薬屋のひとりごと外伝", start_date="2020-03-01", start_precision="day")],
        first_year=2020, last_year=2020, popularity=300,
    )
    add_work(
        conn, A700, "100%オレンジジュース",
        [E("anilist", "700", "manga", "100%オレンジジュース")], first_year=2010, last_year=2010, popularity=100,
    )
    add_work(
        conn, A800, "anchor_point",
        [E("anilist", "800", "manga", "anchor_point")], first_year=2012, last_year=2012, popularity=50,
    )
    add_work(
        conn, A900, "Abyss",
        [E("anilist", "900", "manga", "Abyss")], first_year=2005, last_year=2005, popularity=40,
    )
    # A candidate that is in no cluster yet (harvested after the last link run).
    db.upsert_entries(conn, [E("syoboi", "9999", "anime", "薬屋のひとりごと 別枠", format="TV")])
    add_links(
        conn,
        [
            ("syoboi:5000", "anilist:102", "title-year", 0.95),
            ("narou:n1234a", "anilist:100", "title-exact", 0.9),
            ("narou:n1234a", "anilist:101", "synonym", 0.85),
            ("narou:n1234a", "anilist:200", "title-prefix", 0.5),
            ("ndl:books/9784000000010", "anilist:100", "series", 0.85),
            ("kakuyomu:77", "anilist:100", "title-prefix", 0.6),
            ("kakuyomu:77", "anilist:101", "title-prefix", 0.65),
            ("syoboi:9999", "anilist:102", "title-exact", 0.7),
            # a member's weaker second link to another member of the same work: not a candidate
            ("ndl:books/9784000000010", "anilist:101", "title-prefix", 0.6),
        ],
    )
    db.set_meta(conn, "harvest:anilist:at", "2026-10-14T01:00:00+00:00")
    db.set_meta(conn, "harvest:syoboi:at", "2026-10-14T01:30:00+00:00")
    db.set_meta(conn, "harvest:narou:at", "2026-10-14T01:40:00+00:00")
    db.set_meta(conn, "harvest:narou:result", {"fetched": 2})
    db.set_meta(conn, "link:at", "2026-10-14T02:00:00+00:00")
    db.mark_slice(conn, "anilist", "ANIME:2024", "done", fetched=10)
    db.mark_slice(conn, "narou", "genre:101", "error", message="timeout")
    db.mark_slice(conn, "narou", "genre:102", "error", message="timeout")
    db.mark_slice(conn, "narou", "genre:103", "done")
    db.mark_slice(conn, "ndl", "913.6", "error", message="500")
    conn.close()
    return path


@pytest.fixture()
def catalog_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "catalog.db"
    monkeypatch.setenv(db.DB_ENV_VAR, str(path))
    return path


@pytest.fixture()
def built(catalog_path) -> Path:
    return build_small_catalog(catalog_path)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app_module.app)


def ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def works(client: TestClient, **params: Any) -> list[str]:
    return ids(client.get("/api/catalog/works", params=params))


@pytest.fixture()
def today(monkeypatch):
    monkeypatch.setattr(timeutil, "today_jst", lambda: TODAY)


# -- not built ----------------------------------------------------------------


def test_stats_before_the_catalog_is_built(catalog_path, client):
    response = client.get("/api/catalog/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["works"] == 0 and body["entries"] == 0 and body["sources"] == []
    assert not catalog_path.exists()  # reading never creates the file


@pytest.mark.parametrize(
    "url",
    ["/api/catalog/works", "/api/catalog/works/anilist:1", "/api/catalog/export", "/api/catalog/export?format=json"],
)
def test_everything_else_is_404_before_the_catalog_is_built(catalog_path, client, url):
    response = client.get(url)
    assert response.status_code == 404
    assert response.json() == {"detail": "catalog not built"}
    assert not catalog_path.exists()


def test_an_empty_file_counts_as_not_built(catalog_path, client):
    catalog_path.write_bytes(b"")
    assert client.get("/api/catalog/stats").json()["ready"] is False
    assert client.get("/api/catalog/works").json() == {"detail": "catalog not built"}


# -- /stats -------------------------------------------------------------------


def test_stats(built, client):
    body = client.get("/api/catalog/stats").json()
    assert body["ready"] is True
    assert (body["works"], body["entries"], body["links"]) == (11, 25, 9)
    assert body["kinds"] == {"anime": 5, "manga": 6, "novel": 5, "web-novel": 3, "book": 2}
    assert body["lastLinkAt"] == "2026-10-14T02:00:00+00:00"
    assert body["sources"] == [
        {"source": "anilist", "entries": 18, "lastHarvestAt": "2026-10-14T01:00:00+00:00", "errorSlices": 0},
        {"source": "syoboi", "entries": 2, "lastHarvestAt": "2026-10-14T01:30:00+00:00", "errorSlices": 0},
        {"source": "narou", "entries": 2, "lastHarvestAt": "2026-10-14T01:40:00+00:00", "errorSlices": 2},
        {"source": "kakuyomu", "entries": 1, "lastHarvestAt": None, "errorSlices": 0},
        {"source": "ndl", "entries": 2, "lastHarvestAt": None, "errorSlices": 1},
    ]


def test_stats_of_a_catalog_that_is_harvested_but_not_linked(catalog_path, client):
    conn = db.connect(catalog_path)
    db.upsert_entries(conn, [E("anilist", "1", "anime", "A"), E("narou", "n1a", "web-novel", "B")])
    conn.close()
    body = client.get("/api/catalog/stats").json()
    assert body["ready"] is True
    assert (body["works"], body["entries"], body["links"]) == (0, 2, 0)
    assert body["kinds"] == {"anime": 0, "manga": 0, "novel": 0, "web-novel": 0, "book": 0}
    assert body["lastLinkAt"] is None


def test_requests_open_the_catalog_read_only(built, client):
    before = hashlib.sha256(built.read_bytes()).hexdigest()
    with catalog_api._open() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO meta (key, value) VALUES ('x', '1')")
    for url in ("/api/catalog/stats", "/api/catalog/works?q=薬屋", f"/api/catalog/works/{A100}", "/api/catalog/export"):
        assert client.get(url).status_code == 200
    assert hashlib.sha256(built.read_bytes()).hexdigest() == before


def test_a_database_path_with_special_characters(tmp_path, monkeypatch, client):
    path = tmp_path / "a b#c%d?e" / "catalog.db"
    path.parent.mkdir()
    monkeypatch.setenv(db.DB_ENV_VAR, str(path))
    build_small_catalog(path)
    assert client.get("/api/catalog/stats").json()["works"] == 11


# -- /works: shape, filters ---------------------------------------------------


def test_work_summary_shape(built, client):
    body = client.get("/api/catalog/works").json()
    assert (body["total"], body["page"], body["per"]) == (11, 1, 50)
    first = body["items"][0]
    assert first == {
        "id": A100,
        "title": "薬屋のひとりごと",
        "kinds": ["novel", "manga", "anime", "web-novel", "book"],
        "entryCount": 8,
        "animeStatus": "RELEASING",
        "firstYear": 2011,
        "lastYear": 2025,
        "nextDate": "2026-10-20",
        "nextPrecision": "day",
        "nextLabel": "第3期",
        "popularity": 90000,
        "cover": None,
    }


def test_filter_by_kind(built, client):
    assert works(client, kind="anime") == [A100, A300, A200, A400, A500]
    assert works(client, kind="book") == [A100, B1]
    assert works(client, kind="web-novel") == [A100, N1, K77]
    assert works(client, kind="manga", per=100)[-3:] == [A700, A800, A900]


def test_filter_by_has_requires_every_face(built, client):
    assert works(client, has="manga,anime") == [A100, A300, A200]
    assert works(client, has="novel,anime,book") == [A100]
    assert works(client, has="book,web-novel") == [A100]
    assert works(client, has=" anime , novel ") == [A100, A300, A200, A400, A500]
    assert works(client, has="") == POPULARITY_ORDER
    assert works(client, kind="web-novel", has="anime") == [A100]
    assert works(client, kind="anime", has="anime") == [A100, A300, A200, A400, A500]


def test_filter_by_anime_status(built, client):
    assert works(client, status="RELEASING") == [A100, A300]
    assert works(client, status="FINISHED") == [A200, A500]
    assert works(client, status="NOT_YET_RELEASED") == [A400]
    assert works(client, status="CANCELLED") == []


def test_filter_by_year_is_inclusive_on_both_ends(built, client):
    assert works(client, year=2019) == [A100, A300, A200, A400, A500]
    assert works(client, year=2027) == [A400]
    assert works(client, year=1999) == []
    # A500 spans 2014..2020
    assert A500 in works(client, year=2014) and A500 in works(client, year=2020)
    assert A500 not in works(client, year=2013) and A500 not in works(client, year=2021)


def test_filter_by_year_matches_the_works_span(built, client):
    assert works(client, year=2013) == [A100, N1]  # A300/A400/A500 start in 2014
    assert works(client, year=2026) == [A300, A400, B1]  # A100 ends in 2025
    assert works(client, year=2005) == [A900]


def test_filter_by_season_needs_the_exact_year_and_season(built, client):
    assert works(client, year=2019, season="FALL") == [A200]
    assert works(client, year=2024, season="SPRING") == [A300]  # A100 spans 2024 but has no 2024 season
    assert works(client, year=2021, season="WINTER") == [A300, A400]
    assert works(client, year=2027, season="WINTER") == [A400]
    assert works(client, year=2019, season="SPRING") == []
    assert works(client, year=2020, season="fall") == []  # case is normalized, 2020-FALL does not exist
    assert works(client, year=2019, season="fall") == [A200]


@pytest.mark.parametrize(
    "params",
    [
        {"season": "FALL"},  # season without year
        {"year": 2020, "season": "AUTUMN"},
        {"kind": "movie"},
        {"has": "anime,movie"},
        {"sort": "newest"},
        {"year": "twenty"},
    ],
)
def test_invalid_parameters_are_422(built, client, params):
    assert client.get("/api/catalog/works", params=params).status_code == 422


def test_filters_combine(built, client):
    assert works(client, kind="anime", status="FINISHED", year=2020) == [A200, A500]
    assert works(client, kind="anime", status="FINISHED", year=2020, season="SPRING") == [A200]  # A500 has no 2020 season
    assert works(client, has="novel", year=2021, season="WINTER", status="NOT_YET_RELEASED") == [A400]


# -- /works: sort -------------------------------------------------------------


def test_default_sort_is_popularity_then_title(built, client):
    assert works(client) == POPULARITY_ORDER
    assert works(client, sort="popularity") == POPULARITY_ORDER
    # A200 and A400 tie on popularity: the title decides (本 < 無)
    assert works(client)[2:4] == [A200, A400]


def test_sort_recent(built, client):
    assert works(client, sort="recent") == RECENT_ORDER


def test_sort_title_ignores_case_and_punctuation(built, client):
    result = works(client, sort="title")
    assert result == sorted(POPULARITY_ORDER, key=_TITLES.__getitem__)
    # 'Abyss' sorts as 'abyss' and 'anchor_point' as 'anchorpoint', both ahead of 'Re:ゼロ...' ('reゼロ...');
    # a plain comparison would put the capitalized titles first
    assert result.index(A900) < result.index(A800) < result.index(A500)


_TITLES = {
    cid: key
    for cid, key in (
        (A100, normalize_title("薬屋のひとりごと")),
        (A200, normalize_title("本好きの下剋上")),
        (A300, normalize_title("転生したらスライムだった件")),
        (A400, normalize_title("無職転生")),
        (A500, normalize_title("Re:ゼロから始める異世界生活")),
        (N1, normalize_title("異世界のんびり農家")),
        (B1, normalize_title("新刊の小説")),
        (K77, normalize_title("薬屋のひとりごと外伝")),
        (A700, normalize_title("100%オレンジジュース")),
        (A800, normalize_title("anchor_point")),
        (A900, normalize_title("Abyss")),
    )
}


def test_sort_upcoming_keeps_today_and_later_only(built, client, today):
    result = client.get("/api/catalog/works", params={"sort": "upcoming"}).json()
    assert [item["id"] for item in result["items"]] == [A300, A100, B1, A400]
    assert result["total"] == 4
    # next_date == today is still upcoming; 2026-10-13 (A500) is not


def test_sort_upcoming_moves_with_today(built, client, monkeypatch):
    monkeypatch.setattr(timeutil, "today_jst", lambda: "2026-10-21")
    assert works(client, sort="upcoming") == [B1, A400]
    monkeypatch.setattr(timeutil, "today_jst", lambda: "2026-10-20")
    assert works(client, sort="upcoming") == [A100, B1, A400]
    monkeypatch.setattr(timeutil, "today_jst", lambda: "2030-01-01")
    assert works(client, sort="upcoming") == []


def test_sort_upcoming_uses_the_japanese_calendar_day(built, client, monkeypatch):
    # 2026-10-13 15:30 UTC is already 2026-10-14 00:30 in Japan.
    instant = datetime(2026, 10, 13, 15, 30, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(timeutil, "datetime", FrozenDatetime)
    assert works(client, sort="upcoming") == [A300, A100, B1, A400]


def test_sort_upcoming_against_the_real_clock(built, client):
    today = timeutil.today_jst()
    expected = sorted(
        (nd, cid)
        for cid, nd in {A100: "2026-10-20", A300: TODAY, A400: "2027-01-04", A500: "2026-10-13", B1: "2026-12-01"}.items()
        if nd >= today
    )
    assert works(client, sort="upcoming") == [cid for _, cid in expected]


def set_next(path: Path, **dates: tuple[Optional[str], Optional[str]]) -> None:
    """Rewrites next_date / next_precision of works, keyed by cluster id with ':' written as '_'."""
    conn = db.connect(path)
    for key, (next_date, precision) in dates.items():
        cluster_id = key.replace("_", ":", 1)
        conn.execute(
            "UPDATE cluster_summary SET next_date = ?, next_precision = ? WHERE cluster_id = ?",
            (next_date, precision, cluster_id),
        )
    conn.commit()
    conn.close()


def upcoming_on(client: TestClient, monkeypatch, day: str, **params: Any) -> list[str]:
    monkeypatch.setattr(timeutil, "today_jst", lambda: day)
    return works(client, sort="upcoming", **params)


def test_upcoming_keeps_a_month_precision_date_until_the_month_ends(built, client, monkeypatch):
    # next_date is the 1st of the month: "sometime in October 2026" must still count on the 14th and the 31st
    set_next(built, anilist_400=("2026-10-01", "month"), anilist_500=("2026-09-01", "month"))
    # 期間の終わりで並ぶ: A300 (10/14) → A100 (10/20) → A400 (10月中) → B1 (12月中)。A500 (9月中) は過ぎた
    assert upcoming_on(client, monkeypatch, "2026-10-14") == [A300, A100, A400, B1]
    assert upcoming_on(client, monkeypatch, "2026-10-31") == [A400, B1]
    assert upcoming_on(client, monkeypatch, "2026-11-01") == [B1]
    # B1 is "2026-12-01 (month)"
    assert upcoming_on(client, monkeypatch, "2026-12-31") == [B1]
    assert upcoming_on(client, monkeypatch, "2027-01-01") == []


def test_upcoming_keeps_a_year_precision_date_until_the_year_ends(built, client, monkeypatch):
    set_next(built, anilist_200=("2026-01-01", "year"), anilist_700=("2025-01-01", "year"))
    # A200 (2026年中) は年末扱いで B1 (12月中) と並び、同じ終わりなら始まりの早い A200 が先。A700 (2025年) は過ぎた
    assert upcoming_on(client, monkeypatch, "2026-10-14") == [A300, A100, A200, B1, A400]
    assert upcoming_on(client, monkeypatch, "2026-12-31") == [A200, B1, A400]
    assert upcoming_on(client, monkeypatch, "2027-01-01") == [A400]


def test_upcoming_month_end_follows_the_calendar(built, client, monkeypatch):
    set_next(built, anilist_400=("2028-02-01", "month"))  # leap year
    assert upcoming_on(client, monkeypatch, "2028-02-29") == [A400]
    assert upcoming_on(client, monkeypatch, "2028-03-01") == []
    set_next(built, anilist_400=("2027-02-01", "month"))
    assert upcoming_on(client, monkeypatch, "2027-02-28") == [A400]
    assert upcoming_on(client, monkeypatch, "2027-03-01") == []


def test_upcoming_day_and_unknown_precision_are_compared_as_they_are(built, client, monkeypatch):
    set_next(built, anilist_400=("2026-10-14", "day"), anilist_500=("2026-10-14", None))
    # all three of today's works tie on the date, so the popularity decides
    assert upcoming_on(client, monkeypatch, "2026-10-14") == [A300, A400, A500, A100, B1]
    assert upcoming_on(client, monkeypatch, "2026-10-15") == [A100, B1]


def test_upcoming_is_ordered_by_the_end_of_the_period(built, client, monkeypatch):
    # 「10月中」は月末扱いなので、確定日の 10/14 より後に並ぶ
    set_next(built, anilist_100=("2026-10-01", "month"))
    assert upcoming_on(client, monkeypatch, "2026-10-14") == [A300, A100, B1, A400]


def test_upcoming_total_counts_the_precision_aware_matches(built, client, monkeypatch):
    set_next(built, anilist_400=("2026-10-01", "month"))
    monkeypatch.setattr(timeutil, "today_jst", lambda: "2026-10-14")
    body = client.get("/api/catalog/works", params={"sort": "upcoming", "per": 2}).json()
    assert body["total"] == 4 and len(body["items"]) == 2


def test_upcoming_combines_with_filters(built, client, today):
    assert works(client, sort="upcoming", kind="book") == [A100, B1]
    assert works(client, sort="upcoming", has="anime", status="NOT_YET_RELEASED") == [A400]


# -- /works: search -----------------------------------------------------------


def test_search_three_or_more_characters_uses_fts(built, client):
    assert works(client, q="下剋上") == [A200]
    assert works(client, q="スライムだった") == [A300]
    assert works(client, q="Apothecary") == [A100]  # an English title of a member entry
    assert works(client, q="apothecary diaries") == [A100]  # case-insensitive
    assert works(client, q="Kusuriya") == [A100]
    assert works(client, q="Bookworm") == [A200]  # a synonym
    assert works(client, q="Slime") == [A300]


def test_fts_reports_each_work_once_even_if_many_members_match(built, client):
    body = client.get("/api/catalog/works", params={"q": "ひとりごと"}).json()
    assert [item["id"] for item in body["items"]] == [A100, K77]
    assert body["total"] == 2


def test_fts_matches_the_reading_of_any_member(built, client):
    assert works(client, q="くすりや") == [A100]


def test_search_one_or_two_characters_scans_the_search_text(built, client):
    assert works(client, q="薬屋") == [A100, K77]
    assert works(client, q="くす") == [A100]  # only a member's reading contains it
    assert works(client, q="Ap") == [A100]  # a member's English title
    assert works(client, q="Sl") == [A300]  # a synonym
    assert works(client, q="ab") == [A900]  # ASCII case-insensitive
    assert works(client, q="ひ") == [A100, K77]
    assert works(client, q="ゐ") == []


def test_like_wildcards_in_the_query_are_literal(built, client):
    assert works(client, q="%") == [A700]
    assert works(client, q="_") == [A800]
    assert works(client, q="0%") == [A700]
    assert works(client, q="\\") == []


@pytest.mark.parametrize(
    "q",
    ['"', "'", 'a"b"c', "Re:ゼロ", "NOT x", "OR OR", "abc*", "(abc", "abc)", "-ab", "NEAR(a b)", "col:val", "a AND", "\"\"\"", "%%%", "***"],
)
def test_search_input_is_never_parsed_as_query_syntax(built, client, q):
    response = client.get("/api/catalog/works", params={"q": q})
    assert response.status_code == 200
    assert isinstance(response.json()["total"], int)


def test_fts_phrase_keeps_operators_literal(built, client):
    assert works(client, q="Re:ゼロ") == [A500]  # ':' would be a column filter if unquoted
    assert works(client, q='"ゼロ') == []  # the quote is a character of the phrase
    assert works(client, q="Slime OR Apothecary") == []  # not an OR query


@pytest.fixture()
def widths(built) -> Path:
    """Works whose names use full-width / half-width / capital forms."""
    conn = db.connect(built)
    add_work(conn, "anilist:1000", "ＳＰＹ×ＦＡＭＩＬＹ", [E("anilist", "1000", "anime", "ＳＰＹ×ＦＡＭＩＬＹ")], popularity=30)
    add_work(conn, "syoboi:2000", "ﾄﾞﾗｺﾞﾝﾎﾞｰﾙ", [E("syoboi", "2000", "anime", "ﾄﾞﾗｺﾞﾝﾎﾞｰﾙ")], popularity=20)
    add_work(conn, "anilist:3000", "Ōkami", [E("anilist", "3000", "anime", "Ōkami")], popularity=10)
    conn.close()
    return built


def test_search_ignores_full_width_and_half_width_differences(widths, client):
    spy, dragon = "anilist:1000", "syoboi:2000"
    assert works(client, q="spy×family") == [spy]  # half-width query, full-width title
    assert works(client, q="ＳＰＹ×ＦＡＭＩＬＹ") == [spy]  # the same spelling as the title
    assert works(client, q="ｓｐｙ") == [spy]  # full-width, lowercase
    assert works(client, q="ドラゴン") == [dragon]  # full-width kana, half-width title
    assert works(client, q="ﾄﾞﾗｺﾞﾝ") == [dragon]  # half-width kana, half-width title
    assert works(client, q="Re：ゼロ") == [A500]  # full-width colon typed with an IME
    assert works(client, q="ＲＥ:ゼロ") == [A500]
    assert works(client, q="apothecary   diaries") == [A100]  # runs of spaces count once


def test_short_search_ignores_full_width_and_half_width_differences(widths, client):
    assert works(client, q="ＳＰ") == ["anilist:1000"]
    assert works(client, q="ﾄﾞﾗ") == ["syoboi:2000"]
    assert works(client, q="ドラ") == ["syoboi:2000"]
    assert works(client, q="Ａｂ") == [A900]
    assert works(client, q="ｙ") == works(client, q="y")  # one full-width letter is looked up as "y"
    assert works(client, q="ōk") == ["anilist:3000"]  # SQLite's lower() cannot fold the capital Ō ...
    assert works(client, q="ŌK") == ["anilist:3000"]  # ... so the folded name is in the text


def test_short_search_does_not_read_the_entries_table(built, client, monkeypatch):
    """The first version scanned every title column of `entries` (100 MB): 0.5 s on the real catalog."""
    statements: list[str] = []
    real_connect = catalog_api._connect

    def traced(**kwargs):
        conn = real_connect(**kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(catalog_api, "_connect", traced)
    assert works(client, q="薬屋") == [A100, K77]
    searched = [s for s in statements if "sqlite_master" not in s]
    # scanned once (the vtab's own helper statements name entries_fts_content etc.), not again for the page
    assert len([s for s in searched if re.search(r"\bentries_fts\b", s)]) == 1
    assert not [s for s in searched if re.search(r"\bentries\b", s)], searched


def test_blank_search_is_ignored(built, client):
    assert works(client, q="   ") == POPULARITY_ORDER


def test_search_combines_with_filters_and_sort(built, client, today):
    assert works(client, q="ひとりごと", kind="anime") == [A100]
    assert works(client, q="薬屋", kind="web-novel", sort="title") == [A100, K77]
    assert works(client, q="ひとりごと", sort="upcoming") == [A100]
    assert works(client, q="薬屋", status="FINISHED") == []
    body = client.get("/api/catalog/works", params={"q": "ひとりごと", "per": 1, "page": 2}).json()
    assert (body["total"], [item["id"] for item in body["items"]]) == (2, [K77])


def test_a_very_long_query_is_accepted(built, client):
    assert client.get("/api/catalog/works", params={"q": "あ" * 5000}).status_code == 200


# -- /works: paging -----------------------------------------------------------


def test_paging_walks_the_whole_list(built, client):
    seen: list[str] = []
    for page in (1, 2, 3):
        body = client.get("/api/catalog/works", params={"per": 4, "page": page}).json()
        assert (body["total"], body["page"], body["per"]) == (11, page, 4)
        seen += [item["id"] for item in body["items"]]
        assert len(body["items"]) == (4 if page < 3 else 3)
    assert seen == POPULARITY_ORDER


def test_page_past_the_end_is_empty_but_keeps_the_total(built, client):
    body = client.get("/api/catalog/works", params={"per": 4, "page": 9}).json()
    assert body["items"] == [] and body["total"] == 11 and body["page"] == 9


def test_paging_limits_are_clamped(built, client):
    body = client.get("/api/catalog/works", params={"per": 1000}).json()
    assert body["per"] == 100 and len(body["items"]) == 11
    body = client.get("/api/catalog/works", params={"per": 0, "page": 0}).json()
    assert (body["per"], body["page"], len(body["items"])) == (1, 1, 1)
    body = client.get("/api/catalog/works", params={"per": -5, "page": -3}).json()
    assert (body["per"], body["page"]) == (1, 1)


def test_total_counts_the_filtered_works_not_the_page(built, client):
    body = client.get("/api/catalog/works", params={"kind": "anime", "per": 2}).json()
    assert body["total"] == 5 and len(body["items"]) == 2


# -- /works/{cluster_id} ------------------------------------------------------


def test_detail_orders_anilist_first_then_kind_then_date(built, client):
    body = client.get(f"/api/catalog/works/{A100}").json()
    assert body["id"] == A100 and body["title"] == "薬屋のひとりごと"
    assert body["kinds"] == ["novel", "manga", "anime", "web-novel", "book"]
    assert [entry["id"] for entry in body["entries"]] == [
        "anilist:100",  # AniList: novel, manga, anime (by date, undated last) ...
        "anilist:101",
        "anilist:102",
        "anilist:105",
        "anilist:106",
        "syoboi:5000",  # ... then the others by kind: anime, web-novel, book
        "narou:n1234a",
        "ndl:books/9784000000010",
    ]


def test_detail_entry_fields(built, client):
    entries = {e["id"]: e for e in client.get(f"/api/catalog/works/{A100}").json()["entries"]}
    anime = entries["anilist:102"]
    assert anime["source"] == "anilist" and anime["externalId"] == "102" and anime["kind"] == "anime"
    assert anime["format"] == "TV" and anime["status"] == "RELEASING"
    assert anime["startDate"] == "2023-10-22" and anime["startPrecision"] == "day"
    assert anime["season"] == "FALL" and anime["seasonYear"] == 2023 and anime["episodes"] == 24
    assert anime["genres"] == ["Drama", "Mystery"] and anime["studios"] == ["TOHO animation", "OLM"]
    assert anime["titleEnglish"] == "The Apothecary Diaries" and anime["titleRomaji"] == "Kusuriya no Hitorigoto"
    assert anime["popularity"] == 90000 and anime["score"] == 87
    assert (anime["endDate"], anime["endPrecision"]) == (None, None)
    manga = entries["anilist:101"]
    assert (manga["startDate"], manga["startPrecision"]) == ("2017-05-01", "month")
    assert (manga["endDate"], manga["endPrecision"]) == ("2020-01-01", "year")
    assert entries["anilist:100"]["creators"] == ["日向夏"]
    assert entries["anilist:100"]["url"] == "https://anilist.co/manga/100"
    assert entries["ndl:books/9784000000010"]["extra"] == {"isbn": "9784000000010", "publisher": "ヒーロー文庫"}
    assert entries["ndl:books/9784000000010"]["volumes"] is None


def test_detail_link_method_and_confidence_for_non_anilist_members(built, client):
    entries = {e["id"]: e for e in client.get(f"/api/catalog/works/{A100}").json()["entries"]}
    for anilist_id in ("anilist:100", "anilist:101", "anilist:102", "anilist:105", "anilist:106"):
        assert entries[anilist_id]["linkMethod"] is None and entries[anilist_id]["linkConfidence"] is None
    assert (entries["syoboi:5000"]["linkMethod"], entries["syoboi:5000"]["linkConfidence"]) == ("title-year", 0.95)
    assert (entries["ndl:books/9784000000010"]["linkMethod"], entries["ndl:books/9784000000010"]["linkConfidence"]) == (
        "series",
        0.85,
    )
    # narou:n1234a links to two members (0.9 and 0.85) and to a member of another work (0.5): the best one in this work
    assert (entries["narou:n1234a"]["linkMethod"], entries["narou:n1234a"]["linkConfidence"]) == ("title-exact", 0.9)


def test_detail_candidates_are_low_confidence_rows_outside_the_work(built, client):
    body = client.get(f"/api/catalog/works/{A100}").json()
    assert [(c["id"], c["linkMethod"], c["linkConfidence"]) for c in body["candidates"]] == [
        ("syoboi:9999", "title-exact", 0.7),  # not in any cluster yet
        ("kakuyomu:77", "title-prefix", 0.65),  # in its own work; the best of its two links (0.6, 0.65)
    ]
    assert body["candidates"][0]["title"] == "薬屋のひとりごと 別枠"
    members = {entry["id"] for entry in body["entries"]}
    assert not members & {c["id"] for c in body["candidates"]}


def test_detail_candidates_follow_the_anilist_member_not_the_row(built, client):
    other = client.get(f"/api/catalog/works/{A200}").json()
    assert [(c["id"], c["linkConfidence"]) for c in other["candidates"]] == [("narou:n1234a", 0.5)]
    assert [e["id"] for e in other["entries"]] == ["anilist:200", "anilist:201", "anilist:202"]
    # narou:n1234a is a member of A100 via a stronger link, and is no candidate there
    assert "narou:n1234a" not in [c["id"] for c in client.get(f"/api/catalog/works/{A100}").json()["candidates"]]


def test_detail_candidates_are_strictly_below_the_merge_threshold(built, client):
    conn = db.connect(built)
    add_links(
        conn,
        [
            ("narou:n0001a", "anilist:300", "title-exact", 0.8),  # would have been merged: no candidate
            ("kakuyomu:77", "anilist:300", "title-prefix", 0.79),
        ],
    )
    conn.close()
    body = client.get(f"/api/catalog/works/{A300}").json()
    assert [(c["id"], c["linkConfidence"]) for c in body["candidates"]] == [("kakuyomu:77", 0.79)]


def test_detail_of_a_work_without_candidates(built, client):
    body = client.get(f"/api/catalog/works/{A400}").json()
    assert body["candidates"] == []
    assert [e["id"] for e in body["entries"]] == ["anilist:400", "anilist:402"]


def test_detail_of_a_single_non_anilist_work_with_a_slash_in_its_id(built, client):
    body = client.get(f"/api/catalog/works/{B1}").json()
    assert body["id"] == B1 and body["kinds"] == ["book"]
    assert [e["id"] for e in body["entries"]] == [B1]
    assert body["entries"][0]["linkMethod"] is None


def test_detail_of_a_low_confidence_row_that_is_its_own_work(built, client):
    body = client.get(f"/api/catalog/works/{K77}").json()
    assert [e["id"] for e in body["entries"]] == [K77]
    assert body["entries"][0]["linkMethod"] is None  # it did not join anything
    assert body["candidates"] == []


def test_detail_of_an_unknown_work_is_404(built, client):
    response = client.get("/api/catalog/works/anilist:424242")
    assert response.status_code == 404
    assert response.json() == {"detail": "work not found"}
    assert client.get("/api/catalog/works/narou:").status_code == 404


def test_listed_work_ids_open_in_the_detail(built, client):
    for cid in works(client):
        assert client.get(f"/api/catalog/works/{cid}").json()["id"] == cid


# -- performance (100k entries / 60k works) -----------------------------------

_KANA = list("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわ")
BIG_ENTRIES, BIG_WORKS = 100_000, 60_000
PERF_LIMIT = 1.5  # seconds; the target is 0.3, this only guards against order-of-magnitude regressions


def build_big_catalog(path: Path, entries: int = BIG_ENTRIES, works: int = BIG_WORKS, seed: int = 7) -> Path:
    rng = random.Random(seed)
    conn = db.connect(path)
    conn.execute("PRAGMA synchronous = OFF")
    kind_by_source = {"anilist": ("anime", "manga", "novel"), "narou": ("web-novel",), "ndl": ("book",)}
    members: list[list[tuple[str, str, str, str]]] = [[] for _ in range(works)]  # (id, source, kind, title)
    entry_rows: list[tuple] = []
    fts_rows: list[tuple] = []
    for n in range(entries):
        cluster = n if n < works else rng.randrange(works)
        source = "anilist" if n % 5 < 3 else ("narou" if n % 5 == 3 else "ndl")
        kind = rng.choice(kind_by_source[source])
        title = "".join(rng.choices(_KANA, k=rng.randint(3, 7)))
        if n % 7 == 0:
            title += "の物語"  # a very common trigram
        if n == 12345:
            title = "ネプチューン紀行"  # a unique one
        entry_id = f"{source}:{n}"
        year = rng.randint(1990, 2026)
        start = f"{year}-{rng.randint(1, 12):02d}-01"
        entry_rows.append(
            (
                entry_id, source, str(n), kind, "TV", title, title, None, None, None, "[]",
                "FINISHED", start, "day", rng.randint(0, 300000), rng.randint(0, 100), f"https://example.com/{n}",
            )
        )
        fts_rows.append((entry_id, f"c{cluster}", title))
        members[cluster].append((entry_id, source, kind, title))
    conn.executemany(
        "INSERT INTO entries (id, source, external_id, kind, format, title, title_native, title_romaji, "
        "title_english, title_reading, synonyms, status, start_date, start_precision, popularity, score, url, "
        "fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-10-14T00:00:00+00:00')",
        entry_rows,
    )
    conn.executemany("INSERT INTO entries_fts (entry_id, cluster_id, text) VALUES (?, ?, ?)", fts_rows)
    cluster_rows, summary_rows = [], []
    for cluster, group in enumerate(members):
        cid = f"c{cluster}"
        kinds = sorted({kind for _, _, kind, _ in group})
        first = rng.randint(1990, 2024)
        last = first + rng.randint(0, 2)
        popularity = 0 if rng.random() < 0.3 else rng.randint(1, 200000)
        next_date = f"2026-{rng.randint(10, 12)}-{rng.randint(1, 28):02d}" if rng.random() < 0.05 else None
        summary_rows.append(
            (
                cid, group[0][3], normalize_title(group[0][3]), json.dumps(kinds),
                int("anime" in kinds), int("manga" in kinds), int("novel" in kinds), int("web-novel" in kinds),
                int("book" in kinds), len(group), rng.choice(("FINISHED", "RELEASING", "NOT_YET_RELEASED", None)),
                first, last, json.dumps([f"{first}-{rng.choice(('WINTER', 'SPRING', 'SUMMER', 'FALL'))}"]),
                next_date, "day" if next_date else None, popularity,
            )
        )
        cluster_rows.extend((entry_id, cid) for entry_id, _, _, _ in group)
    conn.executemany("INSERT INTO clusters (entry_id, cluster_id) VALUES (?, ?)", cluster_rows)
    conn.executemany(
        "INSERT INTO cluster_summary (cluster_id, title, title_norm, kinds, has_anime, has_manga, has_novel, "
        "has_web_novel, has_book, entry_count, anime_status, first_year, last_year, seasons, next_date, "
        "next_precision, popularity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        summary_rows,
    )
    links = [(f"narou:{n}", f"anilist:{n - 3}", "title-exact", 0.9) for n in range(3, entries, 5)]
    conn.executemany("INSERT INTO links (a_id, b_id, method, confidence) VALUES (?, ?, ?, ?)", links)
    conn.commit()
    conn.close()
    return path


@pytest.fixture(scope="module")
def big_catalog(tmp_path_factory) -> Path:
    return build_big_catalog(tmp_path_factory.mktemp("big") / "catalog.db")


PERF_URLS = {
    "default": {},
    "default per=100": {"per": 100},
    "recent": {"sort": "recent"},
    "title": {"sort": "title"},
    "upcoming": {"sort": "upcoming"},
    "kind=anime": {"kind": "anime"},
    "has=anime,manga": {"has": "anime,manga"},
    "year+season": {"year": 2010, "season": "FALL"},
    "page 100": {"page": 100},
    "q rare (FTS)": {"q": "ネプチューン"},
    "q common (FTS)": {"q": "の物語"},
    "q common (FTS) + kind": {"q": "の物語", "kind": "anime"},
    "q 2 chars rare (scan)": {"q": "ゐゑ"},
    "q 2 chars common (scan)": {"q": "物語"},
    "q 1 char (scan)": {"q": "あ"},
}


def _measure(client: TestClient, url: str, params: dict[str, Any], runs: int = 3) -> tuple[float, dict]:
    times = []
    body: dict = {}
    for _ in range(runs):
        started = time.perf_counter()
        response = client.get(url, params=params)
        times.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
        body = response.json()
    return min(times), body


def test_works_stay_fast_on_100k_entries(big_catalog, monkeypatch):
    monkeypatch.setenv(db.DB_ENV_VAR, str(big_catalog))
    monkeypatch.setattr(timeutil, "today_jst", lambda: TODAY)
    client = TestClient(app_module.app)

    stats = client.get("/api/catalog/stats").json()
    assert (stats["works"], stats["entries"]) == (BIG_WORKS, BIG_ENTRIES)

    report = []
    for label, params in PERF_URLS.items():
        elapsed, body = _measure(client, "/api/catalog/works", params)
        report.append(f"{label:28} {elapsed * 1000:7.1f} ms  total={body['total']}")
        assert elapsed < PERF_LIMIT, f"{label}: {elapsed:.3f}s"
    print("\n" + "\n".join(report))

    # the searches really searched
    assert _measure(client, "/api/catalog/works", {"q": "ネプチューン"}, 1)[1]["total"] == 1
    assert _measure(client, "/api/catalog/works", {"q": "ゐゑ"}, 1)[1]["total"] == 0
    common = _measure(client, "/api/catalog/works", {"q": "の物語"}, 1)[1]["total"]
    assert common > 5000
    assert _measure(client, "/api/catalog/works", {"q": "物語"}, 1)[1]["total"] == common


def test_detail_and_stats_stay_fast_on_100k_entries(big_catalog, monkeypatch):
    monkeypatch.setenv(db.DB_ENV_VAR, str(big_catalog))
    client = TestClient(app_module.app)
    elapsed, body = _measure(client, "/api/catalog/works/c0", {})
    assert body["id"] == "c0" and body["entries"]
    assert elapsed < PERF_LIMIT
    elapsed, _ = _measure(client, "/api/catalog/stats", {})
    assert elapsed < PERF_LIMIT
