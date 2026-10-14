"""Tests for the なろう catalog harvester (backend/catalog/harvest/narou.py).

No live requests: httpx.MockTransport plus a fake clock.

Fixtures (recorded from https://api.syosetu.com/novelapi/api/ on 2026-10-14):
- narou_genre201_hyoka.json: `genre=201&type=re&order=hyoka&lim=8&st=1&gzip=5&of=...`
  decoded (the live body is a gzip file: Content-Type application/x-gzip and no
  Content-Encoding, so the tests re-gzip it to behave the same way).
- narou_empty_page.json: a window past the end of a small genre (`st=2000` on a
  genre with 541 serials): only the count.
- narou_error_st2001.txt: the body of `st=2001` (HTTP 404, plain text).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pytest

from backend.catalog import db, runtime
from backend.catalog.harvest import narou

FIXTURES = Path(__file__).parent / "fixtures"
quiet = lambda message: None  # noqa: E731


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


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def gz(payload: Any) -> httpx.Response:
    """What the API sends with gzip=5: a gzip file, no Content-Encoding."""
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return httpx.Response(200, content=body, headers={"Content-Type": "application/x-gzip"})


def record(genre: int, rank: int, **over: Any) -> dict[str, Any]:
    base = {
        "title": f"作品{genre}-{rank}",
        "ncode": f"N{genre:04d}{rank:05d}",
        "writer": f"作者{rank}",
        "biggenre": 2,
        "genre": genre,
        "keyword": "異世界 チート",
        "general_firstup": "2020-01-02 03:04:05",
        "general_lastup": "2026-10-01 12:00:00",
        "end": 1,
        "general_all_no": 10 + rank,
        "global_point": 100000 - rank,
        "fav_novel_cnt": 500 - (rank % 500),
    }
    base.update(over)
    return base


class FakeNarou:
    """A tiny novelapi: per genre a ranked list, `st`/`lim` windows, the st<=2000 rule."""

    def __init__(self, clock: FakeClock, counts: Optional[dict[int, int]] = None) -> None:
        self.clock = clock
        self.counts = counts or {}
        self.requests: list[dict[str, str]] = []
        self.times: list[float] = []
        self.headers: list[httpx.Headers] = []
        self.intercept: Optional[Callable[[dict[str, str]], Optional[httpx.Response]]] = None

    def ranked(self, genre: int) -> list[dict[str, Any]]:
        return [record(genre, rank) for rank in range(1, self.counts.get(genre, 0) + 1)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        self.times.append(self.clock.monotonic())
        self.headers.append(request.headers)
        if self.intercept is not None:
            override = self.intercept(params)
            if override is not None:
                return override
        genre, st, lim = int(params["genre"]), int(params["st"]), int(params["lim"])
        if st > 2000:
            return httpx.Response(404, text="ERROR:2000作品以上は検索できません。")
        items = self.ranked(genre)
        return gz([{"allcount": len(items)}, *items[st - 1 : st - 1 + lim]])

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def for_genre(self, genre: int) -> list[dict[str, str]]:
        return [p for p in self.requests if p["genre"] == str(genre)]


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "catalog.db")
    yield connection
    connection.close()


def entry_rows(conn, source: str = "narou") -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM entries WHERE source = ? ORDER BY id", (source,)).fetchall()
    return [db.row_to_dict(r) for r in rows]


async def run(fake: FakeNarou, conn, **kwargs: Any) -> runtime.HarvestResult:
    async with fake.client() as client:
        return await narou.harvest(conn, client, clock=fake.clock, log=quiet, **kwargs)


# --- conversion -----------------------------------------------------------


def test_genre_table_matches_the_official_codes():
    assert list(narou.GENRES) == [
        101, 102, 201, 202, 301, 302, 303, 304, 305, 306, 307,
        401, 402, 403, 404, 9901, 9902, 9903, 9904, 9999, 9801,
    ]  # fmt: skip
    assert narou.GENRES[201] == "ハイファンタジー〔ファンタジー〕"
    assert narou.GENRES[9801] == "ノンジャンル〔ノンジャンル〕"


def test_to_entry_converts_a_recorded_serial():
    raw = recorded("narou_genre201_hyoka.json")[2]  # 無職転生: end=0
    entry = narou.to_entry(raw)
    assert entry is not None
    assert (entry.source, entry.external_id, entry.kind) == ("narou", "n9669bk", "web-novel")
    assert entry.id == "narou:n9669bk"
    assert entry.title == "無職転生　- 異世界行ったら本気だす -"
    assert entry.creators == ["理不尽な孫の手"]
    assert entry.genres == ["ハイファンタジー〔ファンタジー〕"]
    assert entry.tags == ["R15", "残酷な描写あり", "異世界転生"]
    assert entry.status == "完結"  # end=0
    assert entry.episodes == 286
    assert entry.popularity == 998040
    assert (entry.start_date, entry.start_precision) == ("2012-11-22", "day")
    assert entry.url == "https://ncode.syosetu.com/n9669bk/"
    assert entry.extra == {
        "genre": 201,
        "bigGenre": 2,
        "favCount": 286283,
        "lastUpdate": "2015-04-03",
        "flags": [],
    }


def test_end_flag_means_serializing_when_one():
    serial = narou.to_entry(recorded("narou_genre201_hyoka.json")[1])  # とんでもスキル...: end=1
    assert serial is not None
    assert serial.external_id == "n2710db"
    assert serial.status == "連載中"
    assert narou.to_entry(record(201, 1, end=0)).status == "完結"  # type: ignore[union-attr]
    assert narou.to_entry(record(201, 1, end=1)).status == "連載中"  # type: ignore[union-attr]
    unknown = record(201, 1)
    del unknown["end"]
    assert narou.to_entry(unknown).status is None  # type: ignore[union-attr]


def test_flags_come_from_keywords_and_tags_are_split_on_any_whitespace():
    entry = narou.to_entry(record(201, 1, keyword="R15　書籍化 異世界  コミカライズ決定 アニメ化"))
    assert entry is not None
    assert entry.tags == ["R15", "書籍化", "異世界", "コミカライズ決定", "アニメ化"]
    assert entry.extra["flags"] == ["書籍化", "コミカライズ", "アニメ化"]
    assert narou.to_entry(record(201, 1, keyword="")).extra["flags"] == []  # type: ignore[union-attr]


def test_to_entry_skips_unusable_records_and_falls_back_to_slice_genre():
    assert narou.to_entry({"title": "ncode無し"}) is None
    assert narou.to_entry(record(201, 1, title="  ")) is None
    raw = record(201, 1, general_firstup="0000-00-00 00:00:00")
    raw["genre"] = None
    entry = narou.to_entry(raw, default_genre=202)
    assert entry is not None
    assert entry.genres == ["ローファンタジー〔ファンタジー〕"]
    assert entry.start_date is None and entry.start_precision is None


def test_following_start_pins_the_last_window_to_st_2000():
    assert narou.following_start(1, 500) == 501
    assert narou.following_start(1, 200) == 201
    assert narou.following_start(1501, 500) == 2000
    assert narou.following_start(2000, 500) is None


def test_parse_page_reads_gzip_and_plain_bodies_and_rejects_errors():
    payload = recorded("narou_genre201_hyoka.json")
    allcount, records = narou.parse_page(gz(payload))
    assert (allcount, len(records)) == (155716, 8)
    plain = httpx.Response(200, content=json.dumps(payload).encode("utf-8"))
    assert narou.parse_page(plain)[0] == 155716
    assert narou.parse_page(gz(recorded("narou_empty_page.json"))) == (541, [])

    error = httpx.Response(404, content=(FIXTURES / "narou_error_st2001.txt").read_bytes())
    with pytest.raises(narou.NarouError, match="ERROR:2000"):
        narou.parse_page(error)
    with pytest.raises(narou.NarouError):
        narou.parse_page(httpx.Response(200, content=b"\x1f\x8b not really gzip"))
    with pytest.raises(narou.NarouError):
        narou.parse_page(gz({"allcount": 1}))


# --- harvest --------------------------------------------------------------


async def test_harvest_slices_by_genre_and_follows_the_request_contract(conn):
    clock = FakeClock()
    fake = FakeNarou(clock, {101: 3, 201: 2, 9801: 1})
    result = await run(fake, conn)

    assert result.source == "narou"
    assert result.fetched == 6
    assert (result.slices_done, result.slices_skipped, result.slices_error) == (21, 0, 0)
    assert result.errors == []
    # one request per genre, in the documented order
    assert [int(p["genre"]) for p in fake.requests] == list(narou.GENRES)
    assert fake.requests[0] == {
        "genre": "101",
        "type": "re",
        "order": "hyoka",
        "lim": "500",
        "st": "1",
        "out": "json",
        "gzip": "5",
        "of": "t-n-w-bg-g-k-gf-gl-e-ga-gp-f",
    }
    assert all(h["user-agent"] == runtime.USER_AGENT for h in fake.headers)
    # 1 req/s
    gaps = [b - a for a, b in zip(fake.times, fake.times[1:])]
    assert gaps and min(gaps) >= 1.0
    # every slice is checkpointed
    states = conn.execute("SELECT slice_key, status FROM harvest_state WHERE source = 'narou'").fetchall()
    assert {r["slice_key"] for r in states} == {f"genre:{code}" for code in narou.GENRES}
    assert {r["status"] for r in states} == {"done"}
    assert {r["genres"] for r in conn.execute("SELECT genres FROM entries")} == {
        json.dumps(["異世界〔恋愛〕"], ensure_ascii=False),
        json.dumps(["ハイファンタジー〔ファンタジー〕"], ensure_ascii=False),
        json.dumps(["ノンジャンル〔ノンジャンル〕"], ensure_ascii=False),
    }


async def test_pages_follow_st_up_to_2000_and_the_overlap_is_deduplicated(conn):
    fake = FakeNarou(FakeClock(), {201: 2600})
    result = await run(fake, conn)

    windows = [(int(p["st"]), int(p["lim"])) for p in fake.for_genre(201)]
    assert windows == [(1, 500), (501, 500), (1001, 500), (1501, 500), (2000, 500)]
    assert max(int(p["st"]) for p in fake.requests) == 2000  # st=2001 is an API error
    rows = entry_rows(conn)
    assert len(rows) == 2499  # ranks 1..2499; rank 2000 came twice
    assert result.fetched == 2499
    state = db.slice_status(conn, "narou", "genre:201")
    assert (state["status"], state["fetched"], state["cursor"]) == ("done", 2499, None)


async def test_a_small_genre_stops_after_the_last_row(conn):
    fake = FakeNarou(FakeClock(), {9904: 541, 9903: 500})
    result = await run(fake, conn)

    assert [int(p["st"]) for p in fake.for_genre(9904)] == [1, 501]  # second window is short
    assert [int(p["st"]) for p in fake.for_genre(9903)] == [1]  # exactly 500: nothing left to ask
    assert result.fetched == 1041
    assert db.is_slice_done(conn, "narou", "genre:9904")
    assert db.is_slice_done(conn, "narou", "genre:9903")


async def test_recorded_page_is_stored_with_converted_values(conn):
    clock = FakeClock()
    fake = FakeNarou(clock)
    page = recorded("narou_genre201_hyoka.json")
    empty = recorded("narou_empty_page.json")
    fake.intercept = lambda p: gz(page if p["genre"] == "201" else empty)
    result = await run(fake, conn)

    assert result.fetched == 8
    rows = {r["external_id"]: r for r in entry_rows(conn)}
    assert len(rows) == 8
    slime = rows["n6316bn"]
    assert slime["title"] == "転生したらスライムだった件"
    assert slime["creators"] == ["伏瀬"]
    assert slime["status"] == "完結"
    assert slime["popularity"] == 984057
    assert slime["start_date"] == "2013-02-20"
    assert slime["extra"]["lastUpdate"] == "2020-07-04"
    assert slime["extra"]["favCount"] == 341483
    assert rows["n2710db"]["status"] == "連載中"
    # all-caps ncode in the response, lower-case id in the catalog
    assert all(key == key.lower() for key in rows)


async def test_rerun_skips_done_slices_and_does_not_duplicate_rows(conn):
    first = FakeNarou(FakeClock(), {101: 3})
    await run(first, conn)
    second = FakeNarou(FakeClock(), {101: 3})
    result = await run(second, conn)

    assert second.requests == []
    assert (result.slices_skipped, result.slices_done, result.fetched) == (21, 0, 0)
    assert len(entry_rows(conn)) == 3

    db.reset_source(conn, "narou")  # --fresh: forget the checkpoints, keep and update the rows
    third = FakeNarou(FakeClock(), {101: 3})
    result = await run(third, conn)
    assert len(third.requests) == 21
    assert result.fetched == 3
    assert len(entry_rows(conn)) == 3


async def test_a_failed_request_marks_the_slice_error_and_resumes_at_its_cursor(conn):
    clock = FakeClock()
    fake = FakeNarou(clock, {201: 1800, 202: 2})
    fail = {"on": True}

    def intercept(params: dict[str, str]) -> Optional[httpx.Response]:
        if fail["on"] and params["genre"] == "201" and params["st"] == "1001":
            return httpx.Response(503, text="maintenance")
        return None

    fake.intercept = intercept
    first = await run(fake, conn)

    attempts = [p for p in fake.for_genre(201) if p["st"] == "1001"]
    assert len(attempts) == 4  # first try + 3 retries
    assert (first.slices_error, first.slices_done) == (1, 20)
    assert "genre:201 st=1001" in first.errors[0]
    state = db.slice_status(conn, "narou", "genre:201")
    assert (state["status"], state["cursor"], state["fetched"]) == ("error", "1001", 1000)
    assert db.is_slice_done(conn, "narou", "genre:202")  # the rest of the run went on
    assert len(entry_rows(conn)) == 1000 + 2

    fail["on"] = False
    fake.requests.clear()
    second = await run(fake, conn)

    assert [int(p["st"]) for p in fake.requests] == [1001, 1501]  # picks up where it stopped
    assert (second.slices_skipped, second.slices_done, second.slices_error) == (20, 1, 0)
    assert second.fetched == 800
    assert len([r for r in entry_rows(conn) if r["extra"]["genre"] == 201]) == 1800
    assert db.is_slice_done(conn, "narou", "genre:201")


async def test_api_error_body_is_an_error_slice_and_is_not_retried(conn):
    fake = FakeNarou(FakeClock(), {102: 4})
    error_body = (FIXTURES / "narou_error_st2001.txt").read_bytes()
    fake.intercept = lambda p: httpx.Response(404, content=error_body) if p["genre"] == "101" else None
    result = await run(fake, conn)

    assert len(fake.for_genre(101)) == 1  # a 404 is not transient
    assert (result.slices_error, result.slices_done) == (1, 20)
    assert "ERROR:2000" in result.errors[0]
    assert db.slice_status(conn, "narou", "genre:101")["status"] == "error"
    assert result.fetched == 4


async def test_limit_stops_mid_genre_and_the_next_run_continues_from_the_cursor(conn):
    fake = FakeNarou(FakeClock(), {101: 1200, 102: 5})
    first = await run(fake, conn, limit=200)

    assert len(fake.requests) == 1
    assert (fake.requests[0]["genre"], fake.requests[0]["st"], fake.requests[0]["lim"]) == ("101", "1", "200")
    assert first.fetched == 200
    assert (first.slices_done, first.slices_error) == (0, 0)
    state = db.slice_status(conn, "narou", "genre:101")
    assert (state["status"], state["cursor"], state["fetched"]) == ("pending", "201", 200)

    fake.requests.clear()
    second = await run(fake, conn)
    assert [(p["genre"], p["st"]) for p in fake.for_genre(101)] == [("101", "201"), ("101", "701")]
    assert second.fetched == 1000 + 5
    assert len(entry_rows(conn)) == 1205
    assert db.is_slice_done(conn, "narou", "genre:101")


async def test_limit_spans_genres_and_shrinks_the_last_window(conn):
    fake = FakeNarou(FakeClock(), {101: 150, 102: 150, 201: 150})
    result = await run(fake, conn, limit=200)

    assert [(p["genre"], p["lim"]) for p in fake.requests] == [("101", "200"), ("102", "50")]
    assert result.fetched == 200
    assert result.slices_done == 1  # 101 finished, 102 is cut short
    assert db.is_slice_done(conn, "narou", "genre:101")
    assert db.slice_status(conn, "narou", "genre:102")["cursor"] == "51"
    assert db.slice_status(conn, "narou", "genre:201") is None
