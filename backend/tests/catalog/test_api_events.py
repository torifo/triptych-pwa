"""`GET /api/catalog/events` (calendar events of a period) against a synthetic catalog DB."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from backend.api import catalog as catalog_api
from backend.catalog import db
from backend.tests.catalog.test_api import (  # noqa: F401  (fixtures are re-used from test_api)
    E,
    add_work,
    build_small_catalog,
    built,
    catalog_path,
    client,
)

URL = "/api/catalog/events"
OCT = {"from": "2026-10-01", "to": "2026-10-31"}


def anime(ext: str, title: str, date: str | None, fmt: str = "TV", *, source: str = "anilist", precision: str | None = "day", **fields: Any) -> db.Entry:
    return E(source, ext, "anime", title, format=fmt, start_date=date, start_precision=precision, **fields)


def book(ext: str, title: str, date: str | None, *, precision: str | None = "day", **fields: Any) -> db.Entry:
    return E("ndl", ext, "book", title, format="文庫", start_date=date, start_precision=precision, **fields)


def build_events_catalog(path: Path) -> Path:
    """A few works around 2026-10, hand made to cover every rule of the events endpoint."""
    conn = db.connect(path)
    add_work(
        conn, "anilist:1", "薬屋のひとりごと",
        [
            anime("11", "薬屋のひとりごと 第3期", "2026-10-02", "TV", cover="https://img/e11.jpg"),
            anime("12", "薬屋のひとりごと 劇場版", "2026-10-16", "MOVIE"),  # no cover of its own: the work's
            anime("13", "薬屋のひとりごと 第2期", "2026-09-30", "TV"),  # the day before the range
            anime("14", "薬屋のひとりごと 特別編", "2026-10-03", "SPECIAL"),
            anime("15", "薬屋のひとりごと OVA", "2026-10-03", "OVA"),
            anime("16", "薬屋のひとりごと MV", "2026-10-03", "MUSIC"),
            book("9784000000015", "薬屋のひとりごと(15)", "2026-10-10"),
            book("9784000000014", "薬屋のひとりごと(14)", "2026-10-10"),  # same work, same day: the smaller volume wins
            book("9784000000016", "薬屋のひとりごと(16)", "2026-10-31"),  # last day of the range
            book("9784000000017", "薬屋のひとりごと(17)", "2026-11-01"),
        ],
        first_year=2011, last_year=2026, popularity=90000, cover="https://img/w1.jpg",
        origin="novel", novel_cover="https://img/ln1.jpg", manga_cover="https://img/manga1.jpg",
    )
    add_work(
        conn, "anilist:2", "無職転生",
        [
            anime("21", "無職転生 Season２", "2026-10-02", "ONA", cover="https://img/e21.jpg"),
            anime("22", "無職転生 小説版", "2026-10-02", "TV", source="syoboi"),  # not AniList
        ],
        first_year=2014, last_year=2026, popularity=70000, cover="https://img/w2.jpg",
    )
    add_work(
        conn, "anilist:3", "ショート",
        [anime("31", "ショート", "2026-10-02", "TV_SHORT")],  # the same popularity as anilist:2: by cluster id
        first_year=2026, last_year=2026, popularity=70000,
    )
    add_work(
        conn, "anilist:4", "日付の粗い作品",
        [
            anime("41", "月だけ", "2026-10-01", "TV", precision="month"),
            anime("42", "年だけ", "2026-01-01", "TV", precision="year"),
            anime("43", "精度なし", "2026-10-05", "TV", precision=None),  # a full date without precision counts
            anime("44", "日付なし", None, "TV", precision=None),
        ],
        first_year=2026, last_year=2026, popularity=1000,
    )
    add_work(
        conn, "ndl:books/9784000000100", "知られていない本",
        [
            book("9784000000100", "知られていない本 3", "2026-10-05"),
            book("9784000000101", "知られていない本 4", "2026-10-06", precision="month"),
        ],
        first_year=2026, last_year=2026, popularity=0,  # no AniList row in the work
    )
    add_work(
        conn, "anilist:5", "本の多い作品",
        [
            book("9784000000201", "本の多い作品(2)", "2026-10-02"),
            book("9784000000202", "本の多い作品(10)", "2026-10-02"),  # 10 > 2: numeric, not alphabetical
            book("9784000000203", "本の多い作品 外伝", "2026-10-09"),
            book("9784000000204", "本の多い作品(3)", "2026-10-09"),  # a volume number beats a title
            book("9784000000205", "本の多い作品(4)", "2026-10-12", precision="month"),
        ],
        first_year=2020, last_year=2026, popularity=500, cover=None,
    )
    # harvested after the last link run: in no cluster, so it has no work to show
    db.upsert_entries(conn, [anime("91", "リンク前", "2026-10-04", "TV")])
    conn.close()
    return path


@pytest.fixture()
def events_catalog(catalog_path) -> Path:
    return build_events_catalog(catalog_path)


def get(client, **params: Any) -> dict[str, Any]:
    response = client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def keys(body: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(item["date"], item["kind"], item["entryId"]) for item in body["items"]]


# -- not built / validation ------------------------------------------------------


def test_404_before_the_catalog_is_built(catalog_path, client):
    response = client.get(URL, params=OCT)
    assert response.status_code == 404
    assert response.json() == {"detail": "catalog not built"}
    assert not catalog_path.exists()


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"from": "2026-10-01"},
        {"to": "2026-10-31"},
        {"from": "2026-13-01", "to": "2026-13-31"},
        {"from": "2026-10-32", "to": "2026-11-02"},
        {"from": "2026-02-30", "to": "2026-03-02"},
        {"from": "2026-10-1", "to": "2026-10-31"},
        {"from": "20261001", "to": "20261031"},
        {"from": "2026-W41-1", "to": "2026-10-31"},
        {"from": "abc", "to": "2026-10-31"},
        {"from": "2026-10-01", "to": ""},
        {"from": "2026-10-31", "to": "2026-10-01"},  # reversed
        {"from": "2026-10-01", "to": "2026-12-02"},  # 63 days
        {"from": "2026-10-01", "to": "2027-10-01"},
        {"from": "2026-10-01", "to": "2026-10-31", "kind": "manga"},
        {"from": "2026-10-01", "to": "2026-10-31", "kind": "anime,novel"},
    ],
)
def test_bad_parameters_are_422(events_catalog, client, params):
    assert client.get(URL, params=params).status_code == 422


def test_validation_comes_before_the_404(catalog_path, client):
    assert client.get(URL, params={"from": "nope", "to": "2026-10-31"}).status_code == 422
    assert not catalog_path.exists()


def test_the_period_is_at_most_62_days_and_both_ends_count(events_catalog, client):
    assert client.get(URL, params={"from": "2026-10-01", "to": "2026-12-01"}).status_code == 200  # 31 + 30 + 1 = 62
    assert client.get(URL, params={"from": "2026-10-01", "to": "2026-12-02"}).status_code == 422
    one_day = get(client, **{"from": "2026-10-02", "to": "2026-10-02"})
    assert {item["date"] for item in one_day["items"]} == {"2026-10-02"} and one_day["items"]


def test_a_period_may_span_a_year_end_and_a_leap_day(events_catalog, client):
    assert get(client, **{"from": "2026-12-15", "to": "2027-01-15"}) == {"items": [], "truncated": False}
    assert get(client, **{"from": "2028-02-01", "to": "2028-03-31"}) == {"items": [], "truncated": False}  # 29 + 31 = 60


# -- anime --------------------------------------------------------------------------


def anime_items(client, **params: Any) -> list[dict[str, Any]]:
    return get(client, kind="anime", **{**OCT, **params})["items"]


def test_an_anime_event_has_every_field(events_catalog, client):
    items = anime_items(client)
    first = next(item for item in items if item["entryId"] == "anilist:11")
    assert first == {
        "clusterId": "anilist:1",
        "entryId": "anilist:11",
        "title": "薬屋のひとりごと",
        "kind": "anime",
        "event": "season-start",
        "date": "2026-10-02",
        "time": None,
        "label": "第3期 放送開始",
        "cover": "https://img/e11.jpg",
        "popularity": 90000,
        "estimated": False,
    }


def test_anime_events_are_tv_tv_short_ona_and_movie_only(events_catalog, client):
    items = anime_items(client)
    assert {item["entryId"] for item in items} == {"anilist:11", "anilist:12", "anilist:21", "anilist:31", "anilist:43"}
    assert {"anilist:13", "anilist:14", "anilist:15", "anilist:16"}.isdisjoint(item["entryId"] for item in items)


def test_a_movie_is_a_movie_event_and_the_others_season_starts(events_catalog, client):
    events = {item["entryId"]: (item["event"], item["label"]) for item in anime_items(client)}
    assert events["anilist:12"] == ("movie", "公開")
    assert events["anilist:11"] == ("season-start", "第3期 放送開始")
    assert events["anilist:21"] == ("season-start", "第2期 放送開始")  # 'Season２' (full-width) in the entry title
    assert events["anilist:31"] == ("season-start", "放送開始")
    assert events["anilist:43"] == ("season-start", "放送開始")


def test_an_anime_shows_only_its_own_cover_never_the_works_original_art(events_catalog, client):
    covers = {item["entryId"]: item["cover"] for item in anime_items(client)}
    assert covers["anilist:11"] == "https://img/e11.jpg"  # the row's own cover
    assert covers["anilist:12"] is None  # no cover of its own: the text cover, not the work's (novel / manga) art
    assert covers["anilist:31"] is None


def test_only_anilist_day_precision_rows_count(events_catalog, client):
    ids = {item["entryId"] for item in anime_items(client)}
    assert "syoboi:22" not in ids  # not AniList
    assert {"anilist:41", "anilist:42", "anilist:44"}.isdisjoint(ids)  # month / year precision, no date
    assert "anilist:43" in ids  # no precision, but a date down to the day
    assert "anilist:91" not in ids  # in no work


def test_the_period_includes_both_ends(events_catalog, client):
    assert [i["date"] for i in anime_items(client, **{"from": "2026-09-30", "to": "2026-09-30"})] == ["2026-09-30"]
    assert anime_items(client, **{"from": "2026-10-01", "to": "2026-10-01"}) == []
    assert {i["date"] for i in anime_items(client, **{"from": "2026-10-16", "to": "2026-10-16"})} == {"2026-10-16"}
    assert anime_items(client, **{"from": "2026-10-17", "to": "2026-10-30"}) == []


# -- books --------------------------------------------------------------------------


def book_items(client, **params: Any) -> list[dict[str, Any]]:
    return get(client, kind="book", **{**OCT, **params})["items"]


def test_a_book_event_has_every_field(events_catalog, client):
    items = book_items(client)
    last = next(item for item in items if item["date"] == "2026-10-31")
    assert last == {
        "clusterId": "anilist:1",
        "entryId": "ndl:9784000000016",
        "title": "薬屋のひとりごと",
        "kind": "book",
        "event": "release",
        "date": "2026-10-31",
        "time": None,
        "label": "16巻",
        "cover": "https://img/ln1.jpg",  # a novel book: the work's novel cover, never an anime visual
        "popularity": 90000,
        "estimated": False,
    }


def test_books_of_works_without_an_anilist_row_are_left_out(events_catalog, client):
    ids = {item["entryId"] for item in book_items(client)}
    assert "ndl:9784000000100" not in ids  # popularity 0
    assert "ndl:9784000000101" not in ids


def test_only_day_precision_books_count_and_the_period_is_inclusive(events_catalog, client):
    ids = {item["entryId"] for item in book_items(client)}
    assert "ndl:9784000000205" not in ids  # month precision
    assert "ndl:9784000000017" not in ids  # 2026-11-01
    assert "ndl:9784000000016" in ids  # 2026-10-31


def test_one_book_per_work_and_day_the_smaller_volume(events_catalog, client):
    items = book_items(client, **{"from": "2026-10-10", "to": "2026-10-10"})
    assert [(item["entryId"], item["label"]) for item in items] == [("ndl:9784000000014", "14巻")]
    by_day = {(item["clusterId"], item["date"]): item["label"] for item in book_items(client)}
    assert by_day[("anilist:5", "2026-10-02")] == "2巻"  # numeric order, not '10巻' < '2巻'
    assert by_day[("anilist:5", "2026-10-09")] == "3巻"  # a volume number beats '外伝'


def test_a_work_with_books_on_different_days_has_one_event_each(events_catalog, client):
    mine = [item for item in book_items(client) if item["clusterId"] == "anilist:1"]
    assert [(item["date"], item["label"]) for item in mine] == [("2026-10-10", "14巻"), ("2026-10-31", "16巻")]


def test_a_book_without_a_volume_number_gets_its_title_as_the_label(events_catalog, client):
    conn = db.connect(events_catalog)
    add_work(
        conn, "anilist:6", "単巻の作品",
        [book("9784000000301", "単巻の作品 完全版", "2026-10-20")], first_year=2026, last_year=2026, popularity=10,
    )
    conn.close()
    item = next(i for i in book_items(client) if i["clusterId"] == "anilist:6")
    assert item["label"] == catalog_api._book_next_label("単巻の作品", "単巻の作品 完全版") == "完全版"
    assert item["cover"] is None


# -- next episodes of airing anime ---------------------------------------------------


def airing(ext: str, title: str, nxt: Any, *, status: str = "RELEASING", fmt: str = "TV", source: str = "anilist", **fields: Any) -> db.Entry:
    """An airing anime whose next episode is `nxt` (AniList's broadcast-day notation: 24:30 is 00:30 the next day)."""
    return anime(ext, title, fields.pop("date", "2026-04-05"), fmt, source=source, status=status, extra={"nextAiringEpisode": nxt}, **fields)


def build_episode_works(path: Path) -> None:
    conn = db.connect(path)
    for cluster, title, popularity, cover, members in [
        ("anilist:7", "放送中A", 80000, "https://img/w7.jpg", [
            airing("701", "放送中A", {"date": "2026-10-10", "time": "23:30", "episode": 5}),
        ]),
        ("anilist:8", "深夜B", 60000, None, [
            airing("801", "深夜B", {"date": "2026-10-10", "time": "24:30", "episode": 6}, fmt="TV_SHORT", cover="https://img/e801.jpg"),
        ]),
        ("anilist:9", "月末C", 100, None, [
            airing("901", "月末C", {"date": "2026-10-31", "time": "24:15", "episode": 9}),  # 11/01 00:15
        ]),
        ("anilist:10", "月初D", 90, None, [
            airing("1001", "月初D", {"date": "2026-09-30", "time": "25:00", "episode": 3}),  # 10/01 01:00
        ]),
        ("anilist:11", "二つE", 40000, None, [
            airing("1101", "二つE 第2期", {"date": "2026-10-20", "time": "22:00", "episode": 3}),
            airing("1102", "二つE 短編", {"date": "2026-10-14", "time": "22:00", "episode": 2}, fmt="ONA"),
        ]),
        ("anilist:12", "放送前F", 5000, None, [
            airing("1201", "放送前F", {"date": "2026-10-20", "time": "22:00", "episode": 1}, status="NOT_YET_RELEASED"),
            airing("1202", "放送終了F", {"date": "2026-10-21", "time": "22:00", "episode": 4}, status="FINISHED"),
        ]),
        ("anilist:13", "予定なしG", 4000, None, [
            anime("1301", "予定なしG", "2026-04-05", "TV", status="RELEASING"),
            airing("1302", "ほかの出典G", {"date": "2026-10-22", "time": "22:00", "episode": 8}, source="syoboi"),
        ]),
        ("anilist:14", "始まるH", 20, None, [
            airing("1401", "始まるH", {"date": "2026-10-20", "time": "23:00", "episode": 1}, date="2026-10-20"),
        ]),
        ("anilist:15", "時刻なしI", 30000, None, [
            airing("1501", "時刻なしI", {"date": "2026-10-12", "episode": 4}),
        ]),
        ("anilist:16", "壊れたJ", 3000, None, [
            airing("1601", "壊れたJ", {"date": "garbage", "time": "99:99", "episode": 1}),
            airing("1602", "壊れたJ", "oops"),
            airing("1603", "壊れたJ", {"date": "2026-10-13", "time": "20:00"}),  # no episode number
        ]),
        ("anilist:17", "時刻不明K", 2000, None, [
            airing("1701", "時刻不明K", {"date": "2026-10-17", "time": "7pm", "episode": 2}),  # unreadable time: the date only
        ]),
    ]:
        add_work(conn, cluster, title, members, first_year=2026, last_year=2026, popularity=popularity, cover=cover)
    conn.close()


@pytest.fixture()
def episodes_catalog(events_catalog) -> Path:
    build_episode_works(events_catalog)
    return events_catalog


def episode_items(client, **params: Any) -> list[dict[str, Any]]:
    """The next episodes as harvested (not the weekly projections after them)."""
    return [item for item in anime_items(client, **params) if item["event"] == "episode" and not item["estimated"]]


def projected_items(client, cluster: str, **params: Any) -> list[tuple[str, str | None, str, bool]]:
    return [
        (item["date"], item["time"], item["label"], item["estimated"])
        for item in anime_items(client, **params)
        if item["event"] == "episode" and item["clusterId"] == cluster
    ]


def test_an_episode_event_has_every_field(episodes_catalog, client):
    items = {item["entryId"]: item for item in episode_items(client)}
    assert items["anilist:701"] == {
        "clusterId": "anilist:7",
        "entryId": "anilist:701",
        "title": "放送中A",
        "kind": "anime",
        "event": "episode",
        "date": "2026-10-10",
        "time": "23:30",
        "label": "第5話",
        "cover": None,  # no cover of its own: the text cover, not the work's
        "popularity": 80000,
        "estimated": False,
    }
    assert items["anilist:801"]["cover"] == "https://img/e801.jpg"


def test_the_clock_is_the_real_one_not_the_broadcast_day(episodes_catalog, client):
    items = {item["entryId"]: (item["date"], item["time"]) for item in episode_items(client)}
    assert items["anilist:801"] == ("2026-10-11", "00:30")  # '24:30' of the 10th
    assert items["anilist:1001"] == ("2026-10-01", "01:00")  # '25:00' of 09/30 is in October
    assert "anilist:901" not in items  # '24:15' of 10/31 is already November
    november = {i["entryId"]: (i["date"], i["time"]) for i in episode_items(client, **{"from": "2026-11-01", "to": "2026-11-30"})}
    assert november == {"anilist:901": ("2026-11-01", "00:15")}


def test_only_releasing_anilist_anime_with_a_next_episode_count(episodes_catalog, client):
    ids = {item["entryId"] for item in episode_items(client)}
    assert {"anilist:1201", "anilist:1202", "anilist:1301", "syoboi:1302"}.isdisjoint(ids)  # not airing / no next / not AniList
    assert {"anilist:1601", "anilist:1602"}.isdisjoint(ids)  # unreadable data is skipped, not an error


def test_an_episode_without_a_clock_has_no_time_and_one_without_a_number_a_generic_label(episodes_catalog, client):
    items = {item["entryId"]: item for item in episode_items(client)}
    assert (items["anilist:1501"]["time"], items["anilist:1501"]["label"]) == (None, "第4話")
    assert (items["anilist:1603"]["time"], items["anilist:1603"]["label"]) == ("20:00", "次回放送")
    assert (items["anilist:1701"]["date"], items["anilist:1701"]["time"]) == ("2026-10-17", None)


def test_one_episode_per_work_and_day(episodes_catalog, client):
    # 二つE has two airing rows on different weekdays: each keeps its own week, never two on one day
    mine = [(item["entryId"], item["date"], item["label"]) for item in anime_items(client) if item["clusterId"] == "anilist:11" and item["event"] == "episode"]
    assert mine == [
        ("anilist:1102", "2026-10-14", "第2話"),
        ("anilist:1101", "2026-10-20", "第3話"),
        ("anilist:1102", "2026-10-21", "第3話"),
        ("anilist:1101", "2026-10-27", "第4話"),
        ("anilist:1102", "2026-10-28", "第4話"),
    ]
    days = [(item["clusterId"], item["date"]) for item in anime_items(client) if item["event"] == "episode"]
    assert len(days) == len(set(days))


# -- weekly projection after the harvested next episode ---------------------------------


def test_an_airing_show_is_projected_weekly_up_to_its_last_episode(catalog_path, client):
    conn = db.connect(catalog_path)
    add_work(conn, "anilist:50", "全7話", [airing("5001", "全7話", {"date": "2026-10-10", "time": "23:30", "episode": 5}, episodes=7)], popularity=10)
    conn.close()
    assert projected_items(client, "anilist:50") == [
        ("2026-10-10", "23:30", "第5話", False),
        ("2026-10-17", "23:30", "第6話", True),
        ("2026-10-24", "23:30", "第7話", True),
    ]


def test_a_projection_keeps_the_late_night_clock_and_reaches_later_months(catalog_path, client):
    conn = db.connect(catalog_path)
    add_work(conn, "anilist:51", "深夜", [airing("5101", "深夜", {"date": "2026-10-10", "time": "24:30", "episode": 2})], popularity=10)
    conn.close()
    november = projected_items(client, "anilist:51", **{"from": "2026-11-01", "to": "2026-11-30"})
    assert november == [
        ("2026-11-01", "00:30", "第5話", True),  # '24:30' of 10/31
        ("2026-11-08", "00:30", "第6話", True),
        ("2026-11-15", "00:30", "第7話", True),
        ("2026-11-22", "00:30", "第8話", True),
        ("2026-11-29", "00:30", "第9話", True),
    ]


def test_a_projection_without_a_known_total_stops_after_a_cour(catalog_path, client):
    conn = db.connect(catalog_path)
    add_work(conn, "anilist:52", "長寿", [airing("5201", "長寿", {"date": "2026-10-03", "time": "18:00", "episode": 100})], popularity=10)
    conn.close()
    autumn = projected_items(client, "anilist:52", **{"from": "2026-10-01", "to": "2026-12-01"})
    assert autumn[0] == ("2026-10-03", "18:00", "第100話", False)
    assert len(autumn) == 9 and autumn[-1] == ("2026-11-28", "18:00", "第108話", True)
    december = projected_items(client, "anilist:52", **{"from": "2026-12-01", "to": "2026-12-31"})
    assert december[-1] == ("2026-12-26", "18:00", "第112話", True)  # 12 weeks after the harvested one
    assert projected_items(client, "anilist:52", **{"from": "2027-01-01", "to": "2027-01-31"}) == []


def test_a_next_episode_without_a_number_is_not_projected(episodes_catalog, client):
    assert [d[:3] for d in projected_items(client, "anilist:16")] == [("2026-10-13", "20:00", "次回放送")]


def test_the_first_episode_of_a_new_series_is_not_listed_twice(episodes_catalog, client):
    day = [item for item in anime_items(client, **{"from": "2026-10-20", "to": "2026-10-20"}) if item["clusterId"] == "anilist:14"]
    assert [(item["event"], item["label"], item["time"]) for item in day] == [("season-start", "放送開始", None)]


def test_the_other_events_have_no_time(episodes_catalog, client):
    items = get(client, **OCT)["items"]
    assert items and all(item["time"] is None for item in items if item["event"] != "episode")
    assert {item["event"] for item in items} == {"season-start", "movie", "release", "episode"}


def test_episodes_belong_to_the_anime_kind(episodes_catalog, client):
    assert not [i for i in get(client, kind="book", **OCT)["items"] if i["event"] == "episode"]
    assert {i["kind"] for i in get(client, kind="anime", **OCT)["items"] if i["event"] == "episode"} == {"anime"}


def test_events_with_a_time_come_first_within_a_day(episodes_catalog, client):
    body = get(client, **OCT)
    assert body["truncated"] is False
    body = {**body, "items": [item for item in body["items"] if not item["estimated"]]}
    assert keys(body) == [
        ("2026-10-01", "anime", "anilist:1001"),  # 01:00
        ("2026-10-02", "anime", "anilist:11"),
        ("2026-10-02", "anime", "anilist:21"),
        ("2026-10-02", "anime", "anilist:31"),
        ("2026-10-02", "book", "ndl:9784000000201"),
        ("2026-10-05", "anime", "anilist:43"),
        ("2026-10-09", "book", "ndl:9784000000204"),
        ("2026-10-10", "anime", "anilist:701"),  # 23:30 before the book of a more popular work (no time)
        ("2026-10-10", "book", "ndl:9784000000014"),
        ("2026-10-11", "anime", "anilist:801"),  # 00:30
        ("2026-10-12", "anime", "anilist:1501"),  # no clock
        ("2026-10-13", "anime", "anilist:1603"),  # 20:00
        ("2026-10-14", "anime", "anilist:1102"),
        ("2026-10-16", "anime", "anilist:12"),
        ("2026-10-17", "anime", "anilist:1701"),  # a clock nobody can read: no time
        ("2026-10-20", "anime", "anilist:1101"),  # 二つE's other series, on its own day
        ("2026-10-20", "anime", "anilist:1401"),
        ("2026-10-31", "book", "ndl:9784000000016"),
    ]


def test_within_one_day_events_sort_by_time_then_popularity(catalog_path, client):
    conn = db.connect(catalog_path)
    for cluster, popularity, clock in [("anilist:1", 10, "21:00"), ("anilist:2", 90, "21:00"), ("anilist:3", 50, "20:30"), ("anilist:4", 95, None)]:
        nxt = {"date": "2026-10-10", "episode": 1 + popularity, **({"time": clock} if clock else {})}
        add_work(conn, cluster, f"作品{cluster}", [airing(f"e{cluster[-1]}", f"作品{cluster}", nxt)], popularity=popularity)
    conn.close()
    assert [(i["clusterId"], i["time"]) for i in get(client, **OCT)["items"] if i["date"] == "2026-10-10"] == [
        ("anilist:3", "20:30"), ("anilist:2", "21:00"), ("anilist:1", "21:00"), ("anilist:4", None),
    ]


def test_episodes_count_toward_the_limit(episodes_catalog, client, monkeypatch):
    everything = get(client, **OCT)["items"]
    monkeypatch.setattr(catalog_api, "MAX_EVENTS", len(everything) - 1)
    cut = get(client, **OCT)
    assert cut["truncated"] is True and cut["items"] == everything[:-1]


# -- kinds, order and truncation --------------------------------------------------


def test_kind_defaults_to_anime_and_book(events_catalog, client):
    both = get(client, **OCT)
    assert both == get(client, kind="anime,book", **OCT) == get(client, kind="book, anime", **OCT)
    assert both == get(client, kind="", **OCT)
    assert {item["kind"] for item in both["items"]} == {"anime", "book"}
    assert {item["kind"] for item in get(client, kind="anime", **OCT)["items"]} == {"anime"}
    assert {item["kind"] for item in get(client, kind="book", **OCT)["items"]} == {"book"}
    assert len(both["items"]) == len(anime_items(client)) + len(book_items(client))


def test_events_are_ordered_by_date_then_popularity_then_cluster(events_catalog, client):
    body = get(client, **OCT)
    assert body["truncated"] is False
    assert keys(body) == [
        ("2026-10-02", "anime", "anilist:11"),  # popularity 90000
        ("2026-10-02", "anime", "anilist:21"),  # 70000: cluster anilist:2 before anilist:3
        ("2026-10-02", "anime", "anilist:31"),
        ("2026-10-02", "book", "ndl:9784000000201"),  # 500
        ("2026-10-05", "anime", "anilist:43"),
        ("2026-10-09", "book", "ndl:9784000000204"),
        ("2026-10-10", "book", "ndl:9784000000014"),
        ("2026-10-16", "anime", "anilist:12"),
        ("2026-10-31", "book", "ndl:9784000000016"),
    ]
    popularity = [item["popularity"] for item in body["items"] if item["date"] == "2026-10-02"]
    assert popularity == sorted(popularity, reverse=True)


def test_truncated_when_there_are_more_than_the_limit(events_catalog, client, monkeypatch):
    assert catalog_api.MAX_EVENTS == 600
    everything = get(client, **OCT)
    count = len(everything["items"])
    monkeypatch.setattr(catalog_api, "MAX_EVENTS", count)
    assert get(client, **OCT) == {"items": everything["items"], "truncated": False}  # exactly the limit: not truncated
    monkeypatch.setattr(catalog_api, "MAX_EVENTS", count - 1)
    cut = get(client, **OCT)
    assert cut["truncated"] is True
    assert cut["items"] == everything["items"][: count - 1]  # the earliest ones are kept


def test_the_limit_keeps_the_front_of_the_period_after_merging_both_kinds(events_catalog, client, monkeypatch):
    monkeypatch.setattr(catalog_api, "MAX_EVENTS", 5)
    body = get(client, **OCT)
    assert body["truncated"] is True
    assert [(item["date"], item["kind"]) for item in body["items"]] == [
        ("2026-10-02", "anime"), ("2026-10-02", "anime"), ("2026-10-02", "anime"), ("2026-10-02", "book"), ("2026-10-05", "anime"),
    ]


def test_more_than_the_limit_in_the_real_constant(catalog_path, client):
    conn = db.connect(catalog_path)
    conn.execute("PRAGMA synchronous = OFF")
    for n in range(650):
        member = anime(str(n), f"作品{n}", f"2026-10-{n % 28 + 1:02d}", "TV")
        add_work(conn, f"anilist:{n}", member.title, [member], popularity=n + 1)
    conn.close()
    body = get(client, **OCT)
    assert len(body["items"]) == 600 and body["truncated"] is True
    dates = [item["date"] for item in body["items"]]
    assert dates == sorted(dates)


# -- read-only / robustness ----------------------------------------------------------


def test_the_request_never_writes_to_the_catalog(events_catalog, client):
    before = hashlib.sha256(events_catalog.read_bytes()).hexdigest()
    assert get(client, **OCT)["items"]
    assert hashlib.sha256(events_catalog.read_bytes()).hexdigest() == before


def test_an_empty_period_is_an_empty_list(events_catalog, client):
    assert get(client, **{"from": "2030-01-01", "to": "2030-01-31"}) == {"items": [], "truncated": False}


def test_a_catalog_that_is_only_harvested_has_no_events_and_does_not_fail(catalog_path, client):
    conn = db.connect(catalog_path)
    db.upsert_entries(conn, [anime("1", "A", "2026-10-02", "TV")])
    conn.close()
    assert get(client, **OCT) == {"items": [], "truncated": False}  # no clusters yet


# -- season label ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, expected",
    [
        ("薬屋のひとりごと 第3期", "第3期"),
        ("薬屋のひとりごと 第３期", "第3期"),
        ("無職転生 第二期", "第二期"),
        ("アオのハコ Season２", "第2期"),
        ("探偵はもう、死んでいる。Season2", "第2期"),
        ("佐々木とピーちゃん シーズン２", "第2期"),
        ("Re:ゼロ 2nd Season", "第2期"),
        ("夜桜さんちの大作戦 第2期 第2クール", "第2期"),
        ("らんま1/2 (2024) 第3期", "第3期"),
        ("ダイヤのA actⅡ -Second Season- 第2クール", None),  # a cour is not a season
        ("千歳くんはラムネ瓶のなか 2クール", None),
        ("魔法少女育成計画 restart", None),
        ("ホタルの嫁入り", None),
        ("ホロライブ3期生", None),  # a bare '3期' (or a '第3期生') is not a season marker
        ("第3期生募集", None),
        ("", None),
    ],
)
def test_season_label(title, expected):
    assert catalog_api._season_label(title) == expected


def test_a_book_takes_the_novel_or_manga_cover_of_its_work_never_an_anime(catalog_path, client):
    conn = db.connect(catalog_path)
    add_work(
        conn, "anilist:60", "お隣",
        [
            E("anilist", "601", "anime", "お隣", cover="https://img/s1.jpg"),
            E("ndl", "9784000000601", "book", "お隣 12", format="小説", start_date="2026-10-15", start_precision="day"),
            E("ndl", "9784000000602", "book", "お隣 7", format="漫画", start_date="2026-10-16", start_precision="day"),
            E("ndl", "9784000000603", "book", "お隣 公式ガイド", format="その他", start_date="2026-10-17", start_precision="day"),
        ],
        popularity=300000, cover="https://img/ln.jpg", origin="novel", novel_cover="https://img/ln.jpg", manga_cover="https://img/manga.jpg",
    )
    add_work(
        conn, "anilist:61", "漫画なし",
        [
            E("anilist", "611", "anime", "漫画なし", cover="https://img/anime.jpg"),
            E("ndl", "9784000000611", "book", "漫画なし 1", format="漫画", start_date="2026-10-15", start_precision="day"),
        ],
        popularity=1000, cover="https://img/anime.jpg", origin="anime",
    )
    conn.close()
    covers = {item["entryId"]: item["cover"] for item in book_items(client)}
    assert covers["ndl:9784000000601"] == "https://img/ln.jpg"
    assert covers["ndl:9784000000602"] == "https://img/manga.jpg"
    assert covers["ndl:9784000000603"] is None  # neither a novel nor a manga: the text cover
    assert covers["ndl:9784000000611"] is None  # no manga cover in the work: not the anime's
