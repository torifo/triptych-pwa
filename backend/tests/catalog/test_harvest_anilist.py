"""AniList catalog harvester.

No network: responses come from httpx.MockTransport. The `anilist_*.json`
fixtures are real responses recorded from https://graphql.anilist.co on
2026-10-14 (50 per page; page 1 of a big result reports total=5000 and
hasNextPage=true even when the real set is smaller, which `FakeAniList`
imitates). The clock is fake: sleeps are recorded, never waited for.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

import httpx
import pytest

from backend.catalog import db
from backend.catalog.harvest import anilist as A
from backend.timeutil import JST, broadcast_day

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["data"]["Page"]


class FakeClock:
    """Sleeps are recorded and advance the clock instantly."""

    def __init__(self) -> None:
        self.elapsed = 0.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return START + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.sleeps.append(seconds)
            self.elapsed += seconds


def media(i: int, media_type: str = "ANIME", **overrides: Any) -> dict[str, Any]:
    """A minimal `Media` in the shape of the harvest query."""
    data: dict[str, Any] = {
        "id": i,
        "idMal": None,
        "type": media_type,
        "format": "TV" if media_type == "ANIME" else "MANGA",
        "status": "FINISHED",
        "source": None,
        "season": None,
        "seasonYear": None,
        "episodes": None,
        "chapters": None,
        "volumes": None,
        "popularity": 10,
        "averageScore": None,
        "genres": [],
        "tags": [],
        "title": {"native": f"作品{i}", "romaji": f"Sakuhin {i}", "english": None},
        "synonyms": [],
        "startDate": {"year": 2024, "month": 4, "day": 1},
        "endDate": {"year": None, "month": None, "day": None},
        "studios": {"nodes": []},
        "staff": {"edges": []},
        "externalLinks": [],
        "coverImage": {"medium": None},
        "siteUrl": f"https://anilist.co/anime/{i}",
        "nextAiringEpisode": None,
        "relations": {"edges": []},
    }
    data.update(overrides)
    return data


Pages = Union[list[list[dict[str, Any]]], Callable[[int], list[dict[str, Any]]]]


def full_pages(base: int, media_type: str = "ANIME") -> Callable[[int], list[dict[str, Any]]]:
    """Every page holds 50 distinct rows, forever."""
    return lambda page: [media(base + (page - 1) * 50 + n, media_type) for n in range(50)]


@dataclass
class Req:
    key: tuple
    page: int
    query: str
    variables: dict[str, Any]
    headers: dict[str, str]


@dataclass
class FakeAniList:
    routes: dict[tuple, Callable[[int], httpx.Response]] = field(default_factory=dict)
    requests: list[Req] = field(default_factory=list)

    def route(
        self,
        media_type: str,
        *,
        like: Optional[str] = None,
        greater: Optional[int] = None,
        lesser: Optional[int] = None,
        sort: str = "ID",
        pages: Optional[Pages] = None,
        respond: Optional[Callable[[int], httpx.Response]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        key = (media_type, like, greater, lesser, sort)
        if respond is not None:
            self.routes[key] = respond
            return
        self.routes[key] = lambda page: self.page_response(pages or [], page, headers or {})

    @staticmethod
    def page_response(pages: Pages, page: int, headers: dict[str, str]) -> httpx.Response:
        if callable(pages):
            rows, real_total = pages(page), None
        else:
            rows = pages[page - 1] if page <= len(pages) else []
            real_total = sum(len(p) for p in pages)
        # AniList reports total=5000 / hasNextPage=true for any result bigger than a page or two.
        big = real_total is None or real_total > 100
        info = {
            "total": 5000 if big else real_total,
            "currentPage": page,
            "lastPage": 100 if big else max(math.ceil(real_total / 50), 1),
            "hasNextPage": True if big else page * 50 < real_total,
        }
        return httpx.Response(
            200, json={"data": {"Page": {"pageInfo": info, "media": rows}}}, headers=headers
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        v = body["variables"]
        key = (v["type"], v.get("startLike"), v.get("startGreater"), v.get("startLesser"), v["sort"][0])
        self.requests.append(Req(key, v["page"], body["query"], v, dict(request.headers)))
        route = self.routes.get(key)
        if route is None:
            return self.page_response([], v["page"], {})
        return route(v["page"])

    def pages_for(self, media_type: str, like: Optional[str] = None, sort: str = "ID") -> list[int]:
        return [r.page for r in self.requests if r.key[0] == media_type and r.key[1] == like and r.key[4] == sort]


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


async def run(conn, fake: FakeAniList, clock: FakeClock, **kwargs: Any) -> A.HarvestResult:
    logs: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        result = await A.harvest(conn, client, clock=clock, log=logs.append, **kwargs)
    result.logs = logs  # type: ignore[attr-defined]
    return result


def only(conn, *keys: str) -> None:
    """Marks every planned slice done except `keys`, so a test touches just those."""
    for sl in A.plan_slices(START.year):
        if sl.key not in keys:
            db.mark_slice(conn, A.SOURCE, sl.key, "done")


def count(conn, table: str = "entries") -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --------------------------------------------------------------------------- slices


def test_plan_covers_pre_1960_every_year_the_far_future_dateless_and_popularity():
    slices = A.plan_slices(2026)
    keys = [s.key for s in slices]
    assert len(keys) == len(set(keys)) == 2 * (1 + (2029 - 1960 + 1) + 1 + 1) + 2
    assert keys[:3] == ["ANIME:<1960", "ANIME:1960", "ANIME:1961"]
    assert "ANIME:2029" in keys and "ANIME:2030" not in keys
    assert keys[70:74] == ["ANIME:2029", "ANIME:>2029", "ANIME:nodate", "MANGA:<1960"]
    assert keys[-2:] == ["ANIME:pop", "MANGA:pop"]  # complements run after every dated slice

    by_key = {s.key: s for s in slices}
    # Dated pre-1960 only: a bare startDate_lesser would also return every work without a date.
    old = by_key["MANGA:<1960"]
    assert old.variables(1) == {
        "page": 1, "type": "MANGA", "sort": ["ID"], "startGreater": 10000, "startLesser": 19600000,
    }
    assert "startDate_greater: $startGreater" in old.query and "startDate_lesser: $startLesser" in old.query
    assert "startDate_like" not in old.query
    year = by_key["ANIME:2024"]
    assert year.variables(3) == {"page": 3, "type": "ANIME", "sort": ["ID"], "startLike": "2024%"}
    assert year.month(4).key == "ANIME:202404" and year.month(4).start_like == "202404%"
    assert by_key["ANIME:>2029"].variables(1)["startGreater"] == 20291231
    assert by_key["MANGA:nodate"].variables(1) == {"page": 1, "type": "MANGA", "sort": ["ID"], "startLesser": 10000}
    pop = by_key["ANIME:pop"]
    assert pop.variables(1) == {"page": 1, "type": "ANIME", "sort": ["POPULARITY_DESC"]}
    assert pop.max_pages == 40
    assert "startDate_" not in pop.query and "$start" not in pop.query


def test_query_filters_japanese_non_adult_with_50_per_page():
    query = A.plan_slices(2026)[1].query
    assert 'countryOfOrigin: "JP"' in query
    assert "isAdult: false" in query
    assert "perPage: 50" in query
    assert "relationType(version: 2)" in query and "nextAiringEpisode" in query


async def test_pages_through_a_year_and_stops_on_a_short_page(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(n) for n in range(50)], [media(100 + n) for n in range(50)], [media(200 + n) for n in range(7)]])
    result = await run(conn, fake, clock)

    # AniList kept saying hasNextPage=true; the short third page is what ends the slice.
    assert fake.pages_for("ANIME", "2024%") == [1, 2, 3]
    assert count(conn) == 107
    assert result.fetched == 107 and result.slices_done == 1 and result.slices_error == 0
    state = db.slice_status(conn, "anilist", "ANIME:2024")
    assert (state["status"], state["fetched"]) == ("done", 107)
    first = fake.requests[0]
    assert first.headers["user-agent"] == "triptych/0.1 (personal tracker)"
    assert first.variables == {"page": 1, "type": "ANIME", "sort": ["ID"], "startLike": "2024%"}


async def test_a_small_year_is_done_after_one_request(conn, clock):
    only(conn, "ANIME:2029")
    page = fixture("anilist_anime_2029_p1.json")
    assert page["pageInfo"]["total"] == 2 and page["pageInfo"]["hasNextPage"] is False  # as recorded
    fake = FakeAniList()
    fake.route("ANIME", like="2029%", pages=[page["media"]])
    result = await run(conn, fake, clock)
    assert len(fake.requests) == 1
    assert count(conn) == 2 and result.slices_done == 1
    assert {r[0] for r in conn.execute("SELECT start_precision FROM entries")} == {"year"}  # only the year is known


async def test_year_with_more_than_100_pages_is_split_by_month(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=full_pages(1_000_000))
    fake.route("ANIME", like="202403%", pages=[[media(9_000 + n) for n in range(3)]])
    result = await run(conn, fake, clock)

    assert fake.pages_for("ANIME", "2024%") == list(range(1, 101))  # never page 101
    months = sorted(r.key[1] for r in fake.requests if r.key[1] != "2024%")
    assert months == [f"2024{m:02d}%" for m in range(1, 13)]
    assert count(conn) == 5000 + 3
    for month in range(1, 13):
        assert db.is_slice_done(conn, "anilist", f"ANIME:2024{month:02d}")
    year = db.slice_status(conn, "anilist", "ANIME:2024")
    assert (year["status"], year["message"]) == ("done", "split by month")
    assert result.slices_done == 13 and result.slices_error == 0


async def test_split_year_resumes_with_the_unfinished_months_only(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=full_pages(1_000_000))
    fake.route("ANIME", like="202405%", respond=lambda page: httpx.Response(400, json={"errors": [{"message": "boom", "status": 400}]}))
    first = await run(conn, fake, clock)

    assert first.slices_error == 1
    assert db.slice_status(conn, "anilist", "ANIME:202405")["status"] == "error"
    year = db.slice_status(conn, "anilist", "ANIME:2024")
    assert (year["status"], year["cursor"]) == ("pending", "split")  # not done while a month is missing

    second_fake = FakeAniList()
    second_fake.route("ANIME", like="202405%", pages=[[media(77)]])
    second = await run(conn, second_fake, clock)
    assert [r.key[1] for r in second_fake.requests] == ["202405%"]  # no re-fetch of the 100 pages
    assert db.is_slice_done(conn, "anilist", "ANIME:2024")
    assert second.slices_skipped == 11 + (len(A.plan_slices(2026)) - 1)  # 11 done months + the other plan slices


async def test_old_dateless_and_far_future_works_each_have_their_own_slice(conn, clock):
    only(conn, "MANGA:<1960", "MANGA:>2029", "MANGA:nodate")
    fake = FakeAniList()
    fake.route("MANGA", greater=10000, lesser=19600000, pages=[[media(1, "MANGA", startDate={"year": 1955, "month": None, "day": None})]])
    fake.route("MANGA", greater=20291231, pages=[[media(2, "MANGA", startDate={"year": 2031, "month": 4, "day": None})]])
    nodate = {"year": None, "month": None, "day": None}
    fake.route("MANGA", lesser=10000, pages=[[media(3, "MANGA", startDate=nodate), media(4, "MANGA", startDate=nodate, format="NOVEL")]])
    result = await run(conn, fake, clock)

    assert result.slices_done == 3 and result.slices_error == 0
    rows = {r["id"]: (r["kind"], r["start_date"], r["start_precision"]) for r in conn.execute("SELECT * FROM entries")}
    assert rows == {
        "anilist:1": ("manga", "1955-01-01", "year"),
        "anilist:2": ("manga", "2031-04-01", "month"),
        "anilist:3": ("manga", None, None),
        "anilist:4": ("novel", None, None),
    }


async def test_a_slice_that_hits_the_wall_is_done_but_reported_truncated(conn, clock):
    only(conn, "MANGA:nodate")
    fake = FakeAniList()
    fake.route("MANGA", lesser=10000, pages=full_pages(1, "MANGA"))
    result = await run(conn, fake, clock)
    assert fake.pages_for("MANGA", None) == list(range(1, 101))
    state = db.slice_status(conn, "anilist", "MANGA:nodate")
    assert (state["status"], state["message"]) == ("done", "truncated at 5000 rows")
    assert result.errors == ["MANGA:nodate: truncated at 5000 rows"]


async def test_popularity_slice_stops_at_2000_rows(conn, clock):
    only(conn, "MANGA:pop")
    fake = FakeAniList()
    fake.route("MANGA", sort="POPULARITY_DESC", pages=full_pages(1, "MANGA"))
    result = await run(conn, fake, clock)
    assert fake.pages_for("MANGA", None, "POPULARITY_DESC") == list(range(1, 41))
    assert count(conn) == 2000
    assert db.is_slice_done(conn, "anilist", "MANGA:pop") and result.slices_error == 0


# --------------------------------------------------------------------------- resume


async def test_done_slices_are_skipped(conn, clock):
    only(conn, "ANIME:2024", "ANIME:2025")
    db.mark_slice(conn, "anilist", "ANIME:2024", "done", fetched=5)
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(1)]])
    fake.route("ANIME", like="2025%", pages=[[media(2)]])
    result = await run(conn, fake, clock)
    assert [r.key[1] for r in fake.requests] == ["2025%"]
    assert result.slices_skipped == len(A.plan_slices(2026)) - 1 and result.slices_done == 1
    assert [r["id"] for r in conn.execute("SELECT id FROM entries")] == ["anilist:2"]


async def test_fresh_reset_fetches_done_slices_again(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(1)]])
    await run(conn, fake, clock)
    db.reset_source(conn, "anilist")
    only(conn, "ANIME:2024")
    again = FakeAniList()
    again.route("ANIME", like="2024%", pages=[[media(1, title={"native": "改題", "romaji": "x", "english": None})]])
    await run(conn, again, clock)
    assert len(again.requests) == 1
    assert [(r["id"], r["title"]) for r in conn.execute("SELECT id, title FROM entries")] == [("anilist:1", "改題")]


@pytest.mark.parametrize("status", ["error", "pending"])
async def test_unfinished_slice_resumes_from_its_cursor(conn, clock, status):
    only(conn, "ANIME:2024")
    db.mark_slice(conn, "anilist", "ANIME:2024", status, fetched=100, cursor="3")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[], [], [media(n) for n in range(7)]])
    result = await run(conn, fake, clock)
    assert fake.pages_for("ANIME", "2024%") == [3]
    state = db.slice_status(conn, "anilist", "ANIME:2024")
    assert (state["status"], state["fetched"], state["cursor"]) == ("done", 107, None)
    assert result.slices_done == 1


async def test_failed_page_marks_the_slice_error_and_the_run_goes_on(conn, clock):
    only(conn, "ANIME:2024", "ANIME:2025")
    fake = FakeAniList()
    full = [[media(n) for n in range(50)], [media(100 + n) for n in range(50)], [media(200 + n) for n in range(10)]]

    def flaky(page: int) -> httpx.Response:
        if page == 3:
            return httpx.Response(400, json={"errors": [{"message": "Page depth exceeds maximum allowed", "status": 400}]})
        return FakeAniList.page_response(full, page, {})

    fake.route("ANIME", like="2024%", respond=flaky)
    fake.route("ANIME", like="2025%", pages=[[media(900)]])
    result = await run(conn, fake, clock)

    assert result.slices_error == 1 and result.slices_done == 1
    assert "ANIME:2024 page 3" in result.errors[0] and "Page depth exceeds" in result.errors[0]
    state = db.slice_status(conn, "anilist", "ANIME:2024")
    assert (state["status"], state["cursor"], state["fetched"]) == ("error", "3", 100)
    assert db.is_slice_done(conn, "anilist", "ANIME:2025")  # the next slice still ran
    assert count(conn) == 101  # the two good pages were committed

    healed = FakeAniList()
    healed.route("ANIME", like="2024%", pages=[[], [], full[2]])
    await run(conn, healed, clock)
    assert healed.pages_for("ANIME", "2024%") == [3]  # resumed at the failed page
    assert count(conn) == 111
    assert db.is_slice_done(conn, "anilist", "ANIME:2024")


async def test_server_errors_are_retried_with_backoff_then_the_slice_errors(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", respond=lambda page: httpx.Response(503, text="down"))
    result = await run(conn, fake, clock)
    assert len(fake.requests) == 4  # 1 try + 3 retries
    # backoff 2, 4, 8, 16 s (after each failed try); 1.0 is the pacer topping up to 3 s between tries
    assert clock.sleeps == [2.0, 1.0, 4.0, 8.0, 16.0]
    assert result.slices_error == 1
    state = db.slice_status(conn, "anilist", "ANIME:2024")
    assert (state["status"], state["cursor"]) == ("error", "1")


# --------------------------------------------------------------------------- limit


async def test_limit_stops_mid_slice_and_leaves_it_resumable(conn, clock):
    only(conn, "ANIME:2024", "ANIME:2025")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=full_pages(1))
    fake.route("ANIME", like="2025%", pages=[[media(5000)]])
    result = await run(conn, fake, clock, limit=70)

    assert result.fetched == 70 and count(conn) == 70
    assert fake.pages_for("ANIME", "2024%") == [1, 2]
    assert fake.pages_for("ANIME", "2025%") == []  # nothing after the limit
    assert result.slices_done == 0
    state = db.slice_status(conn, "anilist", "ANIME:2024")
    assert (state["status"], state["cursor"], state["fetched"]) == ("pending", "2", 70)


async def test_limit_inside_the_first_page(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=full_pages(1))
    result = await run(conn, fake, clock, limit=20)
    assert result.fetched == 20 and count(conn) == 20 and len(fake.requests) == 1
    assert not db.is_slice_done(conn, "anilist", "ANIME:2024")


# --------------------------------------------------------------------------- pacing


async def test_requests_are_paced_three_seconds_apart(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(n) for n in range(50)], [media(100 + n) for n in range(50)], [media(200)]])
    await run(conn, fake, clock)
    assert len(fake.requests) == 3
    assert clock.sleeps == [3.0, 3.0]


async def test_waits_until_the_reset_when_few_requests_remain(conn, clock):
    only(conn, "ANIME:2024", "ANIME:2025")
    reset = int(START.timestamp()) + 25
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(1)]], headers={"X-RateLimit-Limit": "30", "X-RateLimit-Remaining": "3", "X-RateLimit-Reset": str(reset)})
    fake.route("ANIME", like="2025%", pages=[[media(2)]])
    result = await run(conn, fake, clock)
    assert result.slices_done == 2
    assert clock.sleeps == [26.0]  # 25 s to the reset + 1 s margin; the 3 s pacing is already covered


async def test_waits_a_minute_when_few_remain_and_no_reset_header(conn, clock):
    only(conn, "ANIME:2024", "ANIME:2025")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(1)]], headers={"X-RateLimit-Remaining": "1"})
    fake.route("ANIME", like="2025%", pages=[[media(2)]])
    await run(conn, fake, clock)
    assert clock.sleeps == [60.0]


async def test_no_extra_wait_while_enough_requests_remain(conn, clock):
    only(conn, "ANIME:2024", "ANIME:2025")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(1)]], headers={"X-RateLimit-Remaining": "4"})
    fake.route("ANIME", like="2025%", pages=[[media(2)]])
    await run(conn, fake, clock)
    assert clock.sleeps == [3.0]


async def test_no_trailing_wait_after_the_last_request(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(1)]], headers={"X-RateLimit-Remaining": "0"})
    await run(conn, fake, clock)
    assert clock.sleeps == []


async def test_429_waits_for_retry_after_and_retries(conn, clock):
    only(conn, "ANIME:2024")
    fake = FakeAniList()
    calls = {"n": 0}

    def limited(page: int) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")
        return FakeAniList.page_response([[media(1)]], page, {})

    fake.route("ANIME", like="2024%", respond=limited)
    result = await run(conn, fake, clock)
    assert len(fake.requests) == 2
    assert 7.0 in clock.sleeps
    assert result.slices_done == 1 and result.slices_error == 0 and count(conn) == 1


# --------------------------------------------------------------------------- conversion


def test_anime_fixture_becomes_an_entry():
    page = fixture("anilist_anime_2023_pop_p1.json")
    yakuya = next(m for m in page["media"] if m["id"] == 161645)
    entry = A.to_entry(yakuya)
    assert entry is not None
    assert (entry.id, entry.kind, entry.format) == ("anilist:161645", "anime", "TV")
    assert entry.title == "薬屋のひとりごと"
    assert entry.title_romaji == "Kusuriya no Hitorigoto" and entry.title_english == "The Apothecary Diaries"
    assert "Drugstore Soliloquy" in entry.synonyms and len(entry.synonyms) == len(yakuya["synonyms"])
    assert (entry.start_date, entry.start_precision) == ("2023-10-22", "day")
    assert (entry.end_date, entry.end_precision) == ("2024-03-24", "day")
    assert (entry.status, entry.season, entry.season_year, entry.episodes) == ("FINISHED", "FALL", 2023, 24)
    assert entry.studios == ["OLM", "TOHO animation STUDIO"] and entry.creators == []
    assert entry.genres == ["Drama", "Mystery"]
    assert (entry.popularity, entry.score, entry.source_material) == (334755, 88, "LIGHT_NOVEL")
    assert entry.url == "https://anilist.co/anime/161645" and entry.cover.startswith("https://")
    assert entry.extra == {"idMal": 54492, "streaming": ["Crunchyroll", "Netflix", "Hulu"]}
    # 27 tags in the response: only rank >= 60, best 8 kept, in rank order.
    assert entry.tags == ["Medicine", "Female Protagonist", "Ancient China", "Drugs", "Historical", "Detective", "Conspiracy", "Politics"]


def test_relations_use_prefixed_ids_and_version_2_types():
    page = fixture("anilist_anime_2023_pop_p1.json")
    yakuya = next(m for m in page["media"] if m["id"] == 161645)
    edges = A.relation_edges(yakuya)
    assert ("anilist:99026", "SOURCE") in edges
    assert all(to_id.startswith("anilist:") for to_id, _ in edges)
    assert len(edges) == len(yakuya["relations"]["edges"])


def test_novel_and_manga_kinds_and_creators():
    page = fixture("anilist_novel_honzuki.json")
    by_id = {m["id"]: m for m in page["media"]}
    novel = A.to_entry(by_id[87383])  # format NOVEL
    manga = A.to_entry(by_id[96775])  # format MANGA
    assert (novel.kind, novel.format) == ("novel", "NOVEL")
    assert (manga.kind, manga.format) == ("manga", "MANGA")
    assert novel.creators == ["香月美夜", "椎名優"]  # Story + Illustration, native names; the translator is dropped
    assert manga.creators == ["鈴華", "香月美夜"]  # Story & Art + Original Story; Character Design is dropped
    assert novel.studios == [] and (novel.volumes, novel.chapters) == (3, 85)
    assert novel.extra["idMal"] == 86119
    assert A.to_entry({**by_id[87383], "format": "ONE_SHOT"}).kind == "manga"


def test_unit_conversions_for_dates_titles_and_tags():
    entry = A.to_entry(
        media(
            5,
            title={"native": None, "romaji": "Romaji Only", "english": "English"},
            startDate={"year": 2020, "month": None, "day": None},
            endDate={"year": 2021, "month": 5, "day": None},
            tags=[
                {"name": "low", "rank": 59, "isAdult": False},
                {"name": "adult", "rank": 99, "isAdult": True},
                {"name": "ok", "rank": 60, "isAdult": False},
                {"name": "best", "rank": 100, "isAdult": False},
            ]
            + [{"name": f"t{n}", "rank": 70 + n, "isAdult": False} for n in range(10)],
        )
    )
    assert entry.title == "Romaji Only"
    assert (entry.start_date, entry.start_precision) == ("2020-01-01", "year")
    assert (entry.end_date, entry.end_precision) == ("2021-05-01", "month")
    assert len(entry.tags) == 8 and entry.tags[0] == "best" and "adult" not in entry.tags
    assert "low" not in entry.tags and "ok" not in entry.tags  # pushed out by higher ranks

    assert A.to_entry(media(6, title={"native": None, "romaji": None, "english": "Only English"})).title == "Only English"
    assert A.to_entry(media(7, title={"native": None, "romaji": None, "english": None})) is None
    undated = A.to_entry(media(8, startDate={"year": None, "month": None, "day": None}))
    assert (undated.start_date, undated.start_precision) == (None, None)


def test_next_airing_episode_uses_the_broadcast_day():
    page = fixture("anilist_anime_202610_p1.json")
    airing = next(m for m in page["media"] if m["nextAiringEpisode"])
    entry = A.to_entry(airing)
    day, clock_time = broadcast_day(airing["nextAiringEpisode"]["airingAt"])
    assert entry.extra["nextAiringEpisode"] == {
        "date": day,
        "time": clock_time,
        "episode": airing["nextAiringEpisode"]["episode"],
    }
    assert entry.status == "RELEASING"

    # 01:30 JST on the 15th still belongs to the 14th, written 25:30.
    late = int(datetime(2026, 10, 15, 1, 30, tzinfo=JST).timestamp())
    entry = A.to_entry(media(9, nextAiringEpisode={"airingAt": late, "episode": 3}))
    assert entry.extra["nextAiringEpisode"] == {"date": "2026-10-14", "time": "25:30", "episode": 3}
    assert "nextAiringEpisode" not in A.to_entry(media(10)).extra


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("anilist_*.json")))
def test_every_recorded_row_converts(name):
    page = fixture(name)
    assert page["media"]
    for m in page["media"]:
        entry = A.to_entry(m)
        assert entry is not None and entry.id == f"anilist:{m['id']}"
        assert entry.kind == ("anime" if m["type"] == "ANIME" else "novel" if m["format"] == "NOVEL" else "manga")
        assert entry.title and len(entry.tags) <= 8
        assert entry.start_precision in (None, "day", "month", "year")
        assert (entry.start_date is None) == (entry.start_precision is None)
        assert not (entry.kind == "anime" and entry.creators) and not (entry.kind != "anime" and entry.studios)
        assert all(to_id.startswith("anilist:") for to_id, _ in A.relation_edges(m))


def test_streaming_links_only_and_creators_only_for_books():
    entry = A.to_entry(
        media(
            11,
            idMal=99,
            externalLinks=[
                {"site": "Official Site", "type": "INFO"},
                {"site": "Netflix", "type": "STREAMING"},
                {"site": "Netflix", "type": "STREAMING"},
                {"site": "Twitter", "type": "SOCIAL"},
            ],
            staff={"edges": [{"role": "Original Story", "node": {"name": {"native": None, "full": "Someone"}}}]},
        )
    )
    assert entry.extra == {"idMal": 99, "streaming": ["Netflix"]}
    assert entry.creators == []  # anime keep studios, not creators
    book = A.to_entry(media(12, "MANGA", staff={"edges": [{"role": "Story", "node": {"name": {"native": None, "full": "Some One"}}}]}))
    assert book.creators == ["Some One"]  # falls back to the full name


# --------------------------------------------------------------------------- end to end


async def test_rows_and_relations_are_stored_page_by_page(conn, clock):
    only(conn, "ANIME:pop")
    page = fixture("anilist_anime_2023_pop_p1.json")
    fake = FakeAniList()
    fake.route("ANIME", sort="POPULARITY_DESC", pages=[page["media"]])
    result = await run(conn, fake, clock)

    assert count(conn) == 50 and result.fetched == 50
    row = db.row_to_dict(conn.execute("SELECT * FROM entries WHERE id = 'anilist:161645'").fetchone())
    assert row["title"] == "薬屋のひとりごと" and row["kind"] == "anime" and row["start_precision"] == "day"
    assert row["tags"][0] == "Medicine" and row["studios"] == ["OLM", "TOHO animation STUDIO"]
    edges = conn.execute("SELECT to_id, relation_type FROM relations WHERE from_id = 'anilist:161645'").fetchall()
    assert ("anilist:99026", "SOURCE") in [(e[0], e[1]) for e in edges]
    assert count(conn, "relations") == sum(len(m["relations"]["edges"]) for m in page["media"])


async def test_novels_and_manga_land_with_their_kinds_and_relations(conn, clock):
    only(conn, "MANGA:pop")
    page = fixture("anilist_novel_honzuki.json")
    fake = FakeAniList()
    fake.route("MANGA", sort="POPULARITY_DESC", pages=[page["media"]])
    await run(conn, fake, clock)
    kinds = dict(conn.execute("SELECT id, kind FROM entries").fetchall())
    assert kinds["anilist:87383"] == "novel" and kinds["anilist:96775"] == "manga"
    adaptation = conn.execute(
        "SELECT to_id FROM relations WHERE from_id = 'anilist:87383' AND relation_type = 'ADAPTATION'"
    ).fetchall()
    assert "anilist:96775" in [r[0] for r in adaptation]


async def test_rerun_updates_rows_and_replaces_relations(conn, clock):
    only(conn, "ANIME:2024")
    first = FakeAniList()
    first.route("ANIME", like="2024%", pages=[[media(1, popularity=5, relations={"edges": [{"relationType": "SEQUEL", "node": {"id": 2, "type": "ANIME"}}]})]])
    await run(conn, first, clock)
    db.reset_source(conn, "anilist")
    only(conn, "ANIME:2024")
    second = FakeAniList()
    second.route("ANIME", like="2024%", pages=[[media(1, popularity=9, relations={"edges": [{"relationType": "SOURCE", "node": {"id": 3, "type": "MANGA"}}]})]])
    await run(conn, second, clock)

    assert count(conn) == 1
    assert conn.execute("SELECT popularity FROM entries").fetchone()[0] == 9
    assert [tuple(r) for r in conn.execute("SELECT to_id, relation_type FROM relations")] == [("anilist:3", "SOURCE")]


async def test_one_bad_record_does_not_sink_the_page(conn, clock):
    only(conn, "ANIME:2024")
    broken = media(2)
    del broken["id"]
    untitled = media(3, title={"native": None, "romaji": None, "english": None})
    fake = FakeAniList()
    fake.route("ANIME", like="2024%", pages=[[media(1), broken, untitled, media(4)]])
    result = await run(conn, fake, clock)
    assert [r[0] for r in conn.execute("SELECT id FROM entries ORDER BY id")] == ["anilist:1", "anilist:4"]
    assert result.slices_done == 1 and result.slices_error == 0
    assert len(result.errors) == 1 and "convert" in result.errors[0]


async def test_full_plan_with_nothing_found_finishes_every_slice(conn, clock):
    fake = FakeAniList()
    result = await run(conn, fake, clock)
    planned = A.plan_slices(2026)
    assert len(fake.requests) == len(planned) == result.slices_done
    assert result.fetched == 0 and result.slices_error == 0 and result.slices_skipped == 0
    assert count(conn, "harvest_state") == len(planned)
    # 20 requests a minute: one every 3 s.
    assert clock.sleeps == [3.0] * (len(planned) - 1)
