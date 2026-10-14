"""Tests for the カクヨム catalog harvester (backend/catalog/harvest/kakuyomu.py).

No live requests: httpx.MockTransport plus a fake clock.

Fixtures (recorded from https://kakuyomu.jp on 2026-10-14):
- kakuyomu_genre_fantasy_popular.html: `/search?genre_name=fantasy&order=popular`,
  the first page (20 works), trimmed to the Apollo nodes the harvester reads
  (the search connection, the Work nodes and their UserAccount nodes) and to
  the fields it uses. `genre_name=fantasy` is applied as `genres: ["FANTASY"]`.
- kakuyomu_robots.txt: the live robots.txt (/search is allowed).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pytest

from backend.catalog import db, runtime
from backend.catalog.harvest import kakuyomu

FIXTURES = Path(__file__).parent / "fixtures"
quiet = lambda message: None  # noqa: E731
ROBOTS = (FIXTURES / "kakuyomu_robots.txt").read_text(encoding="utf-8")
GENRE_NAMES = [name for name, _ in kakuyomu.GENRES]


class FakeClock:
    """Time only moves when someone sleeps, so pacing is observable and instant."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return datetime(2026, 10, 14, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(seconds, 0.0)


def work(i: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "__typename": "Work",
        "id": str(1000000 + i),
        "title": f"作品{i}",
        "alternateTitle": None,
        "author": {"__ref": f"UserAccount:{i}"},
        "alternateAuthorName": None,
        "publishedAt": "2020-01-01T00:00:00Z",
        "lastEpisodePublishedAt": "2026-10-01T00:00:00Z",
        "totalReviewPoint": 100000 - i,
        "genre": "FANTASY",
        "serialStatus": "RUNNING",
        "publicEpisodeCount": 10 + i,
        "totalCharacterCount": 12345,
        "hasPublication": False,
        "publicationEntryPath": None,
        "tagLabels": ["異世界", "チート"],
    }
    base.update(over)
    return base


def search_html(works: list[dict[str, Any]], has_next: bool, genre: str = "FANTASY", offset: int = 0) -> str:
    state: dict[str, Any] = {
        "ROOT_QUERY": {
            "__typename": "Query",
            f'searchWorks({{"first":20,"genres":["{genre}"],"offset":{offset},"order":"POPULAR"}})': {
                "__typename": "SearchWorkConnection",
                "nodes": [{"__ref": f"Work:{w['id']}"} for w in works],
                "pageInfo": {"hasNextPage": has_next, "hasPreviousPage": offset > 0},
                "totalCount": 99999,
            },
        }
    }
    for w in works:
        state[f"Work:{w['id']}"] = w
        ref = w["author"]["__ref"]
        state[ref] = {"__typename": "UserAccount", "id": ref.split(":")[1], "name": "acct", "activityName": f"作者{ref.split(':')[1]}"}
    data = {"query": {}, "page": "/search", "props": {"pageProps": {"__APOLLO_STATE__": state}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data, ensure_ascii=False)
        + "</script></body></html>"
    )


class FakeKakuyomu:
    """robots.txt plus a paged, genre-filtered /search (20 works per page)."""

    def __init__(self, clock: FakeClock, counts: Optional[dict[str, int]] = None, robots: str = ROBOTS) -> None:
        self.clock = clock
        self.counts = counts or {}
        self.robots = robots
        self.robots_status = 200
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []
        self.intercept: Optional[Callable[[dict[str, str]], Optional[httpx.Response]]] = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.clock.monotonic())
        if request.url.path == "/robots.txt":
            return httpx.Response(self.robots_status, text=self.robots)
        assert request.url.path == "/search", request.url
        params = dict(request.url.params)
        if self.intercept is not None:
            override = self.intercept(params)
            if override is not None:
                return override
        name = params["genre_name"]
        page = int(params.get("page", "1"))
        total = self.counts.get(name, 0)
        offset = (page - 1) * 20
        works = [work(i, id=f"{GENRE_NAMES.index(name) + 1}{i:07d}") for i in range(offset, min(offset + 20, total))]
        return httpx.Response(200, text=search_html(works, has_next=offset + 20 < total, genre=name.upper(), offset=offset))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def searches(self, genre: Optional[str] = None) -> list[dict[str, str]]:
        params = [dict(r.url.params) for r in self.requests if r.url.path == "/search"]
        return [p for p in params if genre is None or p["genre_name"] == genre]

    def robots_requests(self) -> int:
        return len([r for r in self.requests if r.url.path == "/robots.txt"])


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "catalog.db")
    yield connection
    connection.close()


def entry_rows(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM entries WHERE source = 'kakuyomu' ORDER BY id").fetchall()
    return [db.row_to_dict(r) for r in rows]


async def run(fake: FakeKakuyomu, conn, **kwargs: Any) -> runtime.HarvestResult:
    async with fake.client() as client:
        return await kakuyomu.harvest(conn, client, clock=fake.clock, log=quiet, **kwargs)


# --- parsing and conversion -----------------------------------------------


def recorded_page() -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    html = (FIXTURES / "kakuyomu_genre_fantasy_popular.html").read_text(encoding="utf-8")
    return kakuyomu.parse_search_page(html)


def test_genre_table_matches_the_site_filter_list():
    assert GENRE_NAMES == [
        "fantasy", "action", "sf", "love_story", "romance", "drama",
        "horror", "mystery", "nonfiction", "history", "criticism", "others",
    ]  # fmt: skip
    assert dict(kakuyomu.GENRES)["fantasy"] == "異世界ファンタジー"


def test_recorded_page_has_twenty_works_and_a_next_page():
    state, works, has_next = recorded_page()
    assert len(works) == 20
    assert has_next is True
    assert state["ROOT_QUERY"]["__typename"] == "Query"


def test_to_entry_converts_a_recorded_work():
    state, works, _ = recorded_page()
    entry = kakuyomu.to_entry(state, works[0], "異世界ファンタジー")
    assert entry is not None
    assert (entry.source, entry.external_id, entry.kind) == ("kakuyomu", "1177354054891139802", "web-novel")
    assert entry.id == "kakuyomu:1177354054891139802"
    assert entry.title == "転移したら山の中だった。反動で強さよりも快適さを選びました。"
    assert entry.creators == ["じゃがバター"]  # the account's activityName
    assert entry.genres == ["異世界ファンタジー"]
    assert entry.tags[:3] == ["異世界転移", "剣と魔法", "好きなことだけやる"]
    assert entry.status == "連載中"  # serialStatus RUNNING
    assert entry.episodes == 764
    assert entry.popularity == 73836
    assert (entry.start_date, entry.start_precision) == ("2019-10-12", "day")  # 11:36Z -> 20:36 JST
    assert entry.url == "https://kakuyomu.jp/works/1177354054891139802"
    assert entry.extra == {
        "lastUpdate": "2026-10-14",  # 2026-10-13T16:00:19Z is already the 14th in JST
        "hasPublication": True,
        "publicationIsbn": None,  # /publication/entry/2020060402 is not an ISBN
        "totalCharacterCount": 1599539,
    }


def test_completed_works_and_isbns_come_through():
    state, works, _ = recorded_page()
    by_id = {w["id"]: w for w in works}
    done = kakuyomu.to_entry(state, by_id["1177354054891965415"], "異世界ファンタジー")
    assert done is not None
    assert done.status == "完結"  # serialStatus COMPLETED
    assert done.extra["publicationIsbn"] == "9784040764658"
    unpublished = kakuyomu.to_entry(state, by_id["1177354055504974517"], "異世界ファンタジー")
    assert unpublished is not None
    assert unpublished.extra["hasPublication"] is False
    assert unpublished.extra["publicationIsbn"] is None


@pytest.mark.parametrize(
    "path,isbn",
    [
        ("/publication/entry/9784049521788", "9784049521788"),
        ("/publication/entry/9791234567896", "9791234567896"),
        ("/publication/entry/2020060402", None),
        ("/publication/entry/97840495217889", None),  # 14 digits
        ("", None),
        (None, None),
    ],
)
def test_publication_isbn(path, isbn):
    assert kakuyomu.publication_isbn(path) == isbn


def test_alternate_author_name_wins_over_the_account_name():
    html = search_html(
        [
            work(1, alternateAuthorName="逢沢大介", author={"__ref": "UserAccount:1"}),
            work(2, alternateAuthorName="   "),
            work(3, publishedAt=None, lastEpisodePublishedAt=None, tagLabels=None, totalReviewPoint=None),
        ],
        has_next=False,
    )
    state, works, _ = kakuyomu.parse_search_page(html)
    entries = [kakuyomu.to_entry(state, w, "異世界ファンタジー") for w in works]
    assert entries[0] is not None and entries[0].creators == ["逢沢大介"]
    assert entries[1] is not None and entries[1].creators == ["作者2"]
    bare = entries[2]
    assert bare is not None
    assert (bare.start_date, bare.start_precision, bare.popularity, bare.tags) == (None, None, None, [])
    assert bare.extra["lastUpdate"] is None


def test_to_entry_skips_works_without_a_numeric_id_or_title():
    state, _, _ = kakuyomu.parse_search_page(search_html([work(1)], has_next=False))
    assert kakuyomu.to_entry(state, work(1, id="abc"), "x") is None
    assert kakuyomu.to_entry(state, work(1, title=" "), "x") is None


def test_parse_search_page_rejects_unexpected_pages():
    with pytest.raises(kakuyomu.KakuyomuError, match="embedded search data"):
        kakuyomu.parse_search_page("<html>maintenance</html>")
    broken = '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>'
    with pytest.raises(kakuyomu.KakuyomuError, match="search page structure"):
        kakuyomu.parse_search_page(broken)
    no_connection = search_html([], has_next=False).replace("searchWorks(", "otherQuery(")
    with pytest.raises(kakuyomu.KakuyomuError, match="search page structure"):
        kakuyomu.parse_search_page(no_connection)


# --- harvest --------------------------------------------------------------


async def test_harvest_walks_genres_and_pages_politely(conn):
    clock = FakeClock()
    fake = FakeKakuyomu(clock, {"fantasy": 45, "sf": 3})
    result = await run(fake, conn)

    assert result.source == "kakuyomu"
    assert result.fetched == 48
    assert (result.slices_done, result.slices_skipped, result.slices_error) == (12, 0, 0)
    assert fake.robots_requests() == 1  # fetched once, before the first search
    assert fake.requests[0].url.path == "/robots.txt"
    assert fake.searches("fantasy") == [
        {"genre_name": "fantasy", "order": "popular"},
        {"genre_name": "fantasy", "order": "popular", "page": "2"},
        {"genre_name": "fantasy", "order": "popular", "page": "3"},
    ]  # page 3 reports no next page: stop
    assert len(fake.searches("sf")) == 1
    assert [p["genre_name"] for p in fake.searches() if "page" not in p] == GENRE_NAMES  # one first page each
    assert all(r.headers["user-agent"] == runtime.USER_AGENT for r in fake.requests)
    gaps = [b - a for a, b in zip(fake.times, fake.times[1:])]
    assert gaps and min(gaps) >= 1.0  # 1 req/s, robots.txt included
    states = conn.execute("SELECT slice_key, status FROM harvest_state WHERE source = 'kakuyomu'").fetchall()
    assert {r["slice_key"] for r in states} == {f"genre:{name}" for name in GENRE_NAMES}
    assert {r["status"] for r in states} == {"done"}
    labels = {r["genres"] for r in conn.execute("SELECT genres FROM entries")}
    assert labels == {
        json.dumps(["異世界ファンタジー"], ensure_ascii=False),
        json.dumps(["SF"], ensure_ascii=False),
    }
    assert len(entry_rows(conn)) == 48


async def test_a_genre_is_capped_at_fifty_pages(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 2000})
    result = await run(fake, conn)

    pages = [int(p.get("page", "1")) for p in fake.searches("fantasy")]
    assert pages == list(range(1, 51))
    assert result.fetched == 1000
    state = db.slice_status(conn, "kakuyomu", "genre:fantasy")
    assert (state["status"], state["fetched"]) == ("done", 1000)


async def test_repeated_works_on_shifting_pages_are_stored_once(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 40})
    page1 = [work(i, id=f"900{i}") for i in range(20)]
    page2 = [work(i, id=f"900{i}") for i in range(19, 39)]  # the ranking moved: work 19 again
    fake.intercept = lambda p: (
        httpx.Response(200, text=search_html(page2 if p.get("page") == "2" else page1, has_next=p.get("page") != "2"))
        if p["genre_name"] == "fantasy"
        else None
    )
    result = await run(fake, conn)

    assert result.fetched == 39
    assert len(entry_rows(conn)) == 39


async def test_rerun_skips_done_slices_and_does_not_duplicate_rows(conn):
    await run(FakeKakuyomu(FakeClock(), {"fantasy": 25}), conn)
    second = FakeKakuyomu(FakeClock(), {"fantasy": 25})
    result = await run(second, conn)

    assert second.requests == []  # nothing to do, not even robots.txt
    assert (result.slices_skipped, result.slices_done, result.fetched) == (12, 0, 0)

    db.reset_source(conn, "kakuyomu")  # --fresh
    third = FakeKakuyomu(FakeClock(), {"fantasy": 25})
    result = await run(third, conn)
    assert result.fetched == 25
    assert len(entry_rows(conn)) == 25


async def test_a_failed_page_marks_the_slice_error_and_resumes_at_that_page(conn):
    clock = FakeClock()
    fake = FakeKakuyomu(clock, {"fantasy": 45, "romance": 2})
    fail = {"on": True}

    def intercept(params: dict[str, str]) -> Optional[httpx.Response]:
        if fail["on"] and params["genre_name"] == "fantasy" and params.get("page") == "3":
            return httpx.Response(502, text="bad gateway")
        return None

    fake.intercept = intercept
    first = await run(fake, conn)

    assert len([p for p in fake.searches("fantasy") if p.get("page") == "3"]) == 4  # first try + 3 retries
    assert (first.slices_error, first.slices_done) == (1, 11)
    assert "genre:fantasy page=3" in first.errors[0]
    state = db.slice_status(conn, "kakuyomu", "genre:fantasy")
    assert (state["status"], state["cursor"], state["fetched"]) == ("error", "3", 40)
    assert db.is_slice_done(conn, "kakuyomu", "genre:romance")  # the other genres went on
    assert len(entry_rows(conn)) == 42

    fail["on"] = False
    fake.requests.clear()
    second = await run(fake, conn)

    assert fake.robots_requests() == 1
    assert [p.get("page") for p in fake.searches()] == ["3"]  # only the unfinished page
    assert (second.slices_skipped, second.slices_done, second.slices_error) == (11, 1, 0)
    assert second.fetched == 5
    assert len(entry_rows(conn)) == 47


async def test_a_page_without_embedded_data_is_an_error_slice(conn):
    fake = FakeKakuyomu(FakeClock(), {"sf": 3})
    fake.intercept = lambda p: httpx.Response(200, text="<html>メンテナンス中</html>") if p["genre_name"] == "fantasy" else None
    result = await run(fake, conn)

    assert (result.slices_error, result.slices_done) == (1, 11)
    assert "embedded search data" in result.errors[0]
    assert db.slice_status(conn, "kakuyomu", "genre:fantasy")["status"] == "error"
    assert result.fetched == 3


async def test_a_client_error_status_is_not_retried(conn):
    fake = FakeKakuyomu(FakeClock())
    fake.intercept = lambda p: httpx.Response(403, text="forbidden") if p["genre_name"] == "fantasy" else None
    result = await run(fake, conn)

    assert len(fake.searches("fantasy")) == 1
    assert "HTTP 403" in result.errors[0]


# --- robots.txt -----------------------------------------------------------


async def test_robots_disallowing_search_errors_every_slice_without_fetching_it(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 45}, robots="User-agent: *\nDisallow: /search\n")
    result = await run(fake, conn)

    assert fake.searches() == []
    assert fake.robots_requests() == 1
    assert (result.slices_error, result.slices_done, result.fetched) == (12, 0, 0)
    assert all("robots.txt disallows fetching /search" in e for e in result.errors)
    assert db.slice_status(conn, "kakuyomu", "genre:fantasy")["status"] == "error"
    assert entry_rows(conn) == []


async def test_robots_is_checked_per_path_and_query(conn):
    robots = "User-agent: *\nDisallow: /search?genre_name=fantasy&order=popular&page=\n"
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 45, "sf": 25}, robots=robots)
    result = await run(fake, conn)

    assert [p.get("page") for p in fake.searches("fantasy")] == [None]  # page 1 is fine, page 2 is not
    assert len(fake.searches("sf")) == 2
    assert (result.slices_error, result.slices_done) == (1, 11)
    assert "genre:fantasy page=2" in result.errors[0]
    state = db.slice_status(conn, "kakuyomu", "genre:fantasy")
    assert (state["status"], state["cursor"], state["fetched"]) == ("error", "2", 20)


async def test_robots_that_cannot_be_fetched_errors_every_slice_and_nothing_is_crawled(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 45})
    fake.robots_status = 404
    result = await run(fake, conn)

    assert fake.searches() == []
    assert fake.robots_requests() == 12  # tried again for every slice, never assumed allowed
    assert (result.slices_error, result.fetched) == (12, 0)
    assert "could not fetch robots.txt" in result.errors[0]


async def test_crawl_delay_from_robots_is_honored(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 45}, robots="User-agent: *\nCrawl-delay: 3\n")
    await run(fake, conn)

    search_times = [t for r, t in zip(fake.requests, fake.times) if r.url.path == "/search"]
    gaps = [b - a for a, b in zip(search_times, search_times[1:])]
    assert gaps and min(gaps) >= 3.0


# --- limit ----------------------------------------------------------------


async def test_limit_truncates_the_last_page_and_the_next_run_refetches_it(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 100})
    first = await run(fake, conn, limit=30)

    assert [p.get("page") for p in fake.searches()] == [None, "2"]
    assert first.fetched == 30
    assert first.slices_done == 0
    state = db.slice_status(conn, "kakuyomu", "genre:fantasy")
    assert (state["status"], state["cursor"], state["fetched"]) == ("pending", "2", 30)
    assert len(entry_rows(conn)) == 30

    fake.requests.clear()
    second = await run(fake, conn)
    assert [p.get("page") for p in fake.searches("fantasy")] == ["2", "3", "4", "5"]
    assert len(entry_rows(conn)) == 100  # page 2's first ten rows were upserted, not duplicated
    assert second.slices_done == 12


async def test_limit_on_a_page_boundary_resumes_at_the_next_page(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 100})
    first = await run(fake, conn, limit=20)

    assert len(fake.searches()) == 1
    assert first.fetched == 20
    state = db.slice_status(conn, "kakuyomu", "genre:fantasy")
    assert (state["status"], state["cursor"]) == ("pending", "2")

    fake.requests.clear()
    await run(fake, conn)
    assert [p.get("page") for p in fake.searches("fantasy")] == ["2", "3", "4", "5"]
    assert len(entry_rows(conn)) == 100


async def test_limit_spans_genres(conn):
    fake = FakeKakuyomu(FakeClock(), {"fantasy": 15, "action": 40, "sf": 40})
    result = await run(fake, conn, limit=25)

    assert [(p["genre_name"], p.get("page")) for p in fake.searches()] == [("fantasy", None), ("action", None)]
    assert result.fetched == 25
    assert result.slices_done == 1
    assert db.slice_status(conn, "kakuyomu", "genre:sf") is None
