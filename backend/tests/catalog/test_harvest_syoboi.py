"""Tests for the しょぼいカレンダー catalog harvester (backend/catalog/harvest/syoboi.py).

No live requests: httpx.MockTransport plus a fake clock, and a throwaway TTLCache
standing in for backend/cache.db (where the collector keeps the time of its last
request).

Fixture (recorded 2026-10-14 from
`https://cal.syoboi.jp/db.php?Command=TitleLookup&TID=*`, 8085 titles / 24 MB):
- syoboi_titlelookup_sample.xml: 196 of the titles, in the site's order, covering
  every Cat (1 / 2 / 3 / 4 / 5 / 6 / 7 / 8 / 10), a placeholder row (`*アニメ特番*`),
  live-action films, titles with TitleEN / ShortTitle / Keywords, year-only dates
  and titles without a start date. Comment / SubTitles are cut short; the
  structure is the live one.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx
import pytest

from backend.adapters.syoboi import CRAWL_DELAY_SECONDS, HostQueue
from backend.cache import TTLCache
from backend.catalog import db, runtime
from backend.catalog.harvest import syoboi

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "syoboi_titlelookup_sample.xml").read_bytes()
SAMPLE_ANIME = 143  # entries kept out of the 196 titles of the sample
quiet = lambda message: None  # noqa: E731

START = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc).timestamp()


class FakeClock:
    """Time only moves when someone sleeps, so waits are observable and instant."""

    def __init__(self, start: float = START) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, tz=timezone.utc)

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(seconds, 0.0)


class CollectorClock:
    """The collector's clock (backend.adapters.syoboi.Clock): float now()."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def now(self) -> float:
        return self.clock.t

    async def sleep(self, seconds: float) -> None:
        await self.clock.sleep(seconds)
        await asyncio.sleep(0)


class FakeSyoboi:
    """cal.syoboi.jp/db.php: logs every request with the fake time; answers from `responses`."""

    def __init__(self, clock: FakeClock, cache: Optional[TTLCache] = None) -> None:
        self.clock = clock
        self.cache = cache
        self.sent: list[tuple[float, httpx.Request]] = []
        self.stamp_seen: list[object] = []
        self.responses: list[Callable[[], httpx.Response]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append((self.clock.t, request))
        if self.cache is not None:
            self.stamp_seen.append(self.cache.get(syoboi.LAST_REQUEST_KEY))
        if self.responses:
            return self.responses.pop(0)()
        return httpx.Response(200, content=SAMPLE)

    @property
    def times(self) -> list[float]:
        return [t for t, _ in self.sent]


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def cache(tmp_path) -> TTLCache:
    shared = TTLCache(tmp_path / "cache.db")
    yield shared
    shared.close()


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture()
def server(clock, cache) -> FakeSyoboi:
    return FakeSyoboi(clock, cache)


@pytest.fixture()
async def client(server):
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as http:
        yield http


def title_xml(*items: str, code: str = "200") -> bytes:
    return (
        f"<TitleLookupResponse><Result><Code>{code}</Code><Message></Message></Result>"
        f"<TitleItems>{''.join(items)}</TitleItems></TitleLookupResponse>"
    ).encode()


def item(tid: int = 1, title: str = "テスト", cat: int = 10, **fields: str) -> str:
    values = {
        "TID": str(tid),
        "Title": title,
        "ShortTitle": "",
        "TitleYomi": "",
        "TitleEN": "",
        "Cat": str(cat),
        "FirstYear": "",
        "FirstMonth": "",
        "FirstEndYear": "",
        "FirstEndMonth": "",
        "Keywords": "",
    }
    values.update(fields)
    body = "".join(f"<{k}>{v}</{k}>" for k, v in values.items())
    return f'<TitleItem id="{tid}">{body}</TitleItem>'


def entry_of(entries: list[db.Entry], tid: str) -> db.Entry:
    return next(e for e in entries if e.external_id == tid)


# -- parsing -------------------------------------------------------------------------------------


def test_sample_keeps_only_anime_categories():
    entries = list(syoboi.parse_titles(SAMPLE))

    assert len(entries) == SAMPLE_ANIME
    by_cat = {}
    for e in entries:
        by_cat.setdefault((e.extra["cat"], e.format), 0)
        by_cat[(e.extra["cat"], e.format)] += 1
    # Cat 1 アニメ and Cat 10 アニメ(終了/再放送) are TV, 7 is OVA, 8 is 映画.
    assert by_cat == {(10, "TV"): 102, (1, "TV"): 17, (7, "OVA"): 12, (8, "映画"): 12}
    assert {e.kind for e in entries} == {"anime"} and {e.source for e in entries} == {"syoboi"}

    ids = {e.external_id for e in entries}
    assert "66" not in ids  # Cat 2 ラジオ
    assert "18" not in ids  # Cat 3 テレビ
    assert "111" not in ids  # Cat 4 特撮
    assert "62" not in ids  # Cat 5 アニメ関連
    assert "21" not in ids  # Cat 6 メモ (アンテナ)
    assert "93" not in ids  # Cat 1 placeholder `*アニメ特番*`
    assert "1263" not in ids  # Cat 8 `NANA2(実写版)`
    assert not [e for e in entries if e.title.startswith("*") or "実写" in e.title]


def test_entry_fields_of_a_plain_title():
    entry = entry_of(list(syoboi.parse_titles(SAMPLE)), "1")

    assert entry.id == "syoboi:1"
    assert entry.title == "魔法遣いに大切なこと"
    assert entry.title_reading == "まほうつかいにたいせつなこと"
    assert entry.title_english is None
    assert entry.synonyms == []
    assert (entry.start_date, entry.start_precision) == ("2003-01-01", "month")
    assert (entry.end_date, entry.end_precision) == ("2003-03-01", "month")
    assert entry.url == "https://cal.syoboi.jp/tid/1"
    assert entry.extra == {"tid": "1", "cat": 10, "keywords": []}


def test_short_title_english_and_keywords():
    entries = list(syoboi.parse_titles(SAMPLE))

    gundam = entry_of(entries, "19")  # 機動戦士ガンダムSEED, ShortTitle ガンダムSEED
    assert gundam.synonyms == ["ガンダムSEED"]
    assert gundam.extra["keywords"] == ["ガンダム"]

    chobits = entry_of(entries, "73")
    assert chobits.title_english == "Chobits"

    idol = entry_of(entries, "3588")
    assert idol.extra["keywords"] == ["アイマス", "wikipedia:アイドルマスター_シンデレラガールズ"]
    assert idol.title_english == "THE IDOLM@STER CINDERELLA GIRLS"


def test_dates_keep_their_real_precision():
    entries = list(syoboi.parse_titles(SAMPLE))

    movie = entry_of(entries, "472")  # 劇場版 新世紀エヴァンゲリオン: only the year is known
    assert (movie.start_date, movie.start_precision) == ("1997-01-01", "year")
    assert (movie.end_date, movie.end_precision) == (None, None)

    ova = entry_of(entries, "415")  # year -> year
    assert (ova.start_date, ova.start_precision, ova.end_date, ova.end_precision) == (
        "1993-01-01", "year", "1995-01-01", "year",
    )

    airing = entry_of(entries, "91")  # クレヨンしんちゃん: still running, no end
    assert (airing.start_date, airing.start_precision) == ("1992-04-01", "month")
    assert (airing.end_date, airing.end_precision) == (None, None)

    undated = entry_of(entries, "696")  # 劇場版 ワンピース: no date at all
    assert (undated.start_date, undated.start_precision) == (None, None)


def test_synonym_equal_to_title_is_dropped_and_bad_months_degrade_to_year():
    body = title_xml(
        item(1, "同じ題名", ShortTitle="同じ題名", FirstYear="2020", FirstMonth="13"),
        item(2, "別の題名", ShortTitle="略称", FirstYear="2020", FirstMonth="0", FirstEndYear="2021", FirstEndMonth="3"),
        item(3, "年なし", FirstMonth="4"),
    )
    entries = list(syoboi.parse_titles(body))

    assert entry_of(entries, "1").synonyms == []
    assert (entry_of(entries, "1").start_date, entry_of(entries, "1").start_precision) == ("2020-01-01", "year")
    two = entry_of(entries, "2")
    assert two.synonyms == ["略称"]
    assert (two.start_precision, two.end_date, two.end_precision) == ("year", "2021-03-01", "month")
    assert entry_of(entries, "3").start_date is None


def test_non_anime_cat_placeholder_and_live_action_rows_are_skipped():
    body = title_xml(
        item(1, "アニメ", cat=1),
        item(2, "ラジオ", cat=2),
        item(3, "*メモ*", cat=1),
        item(4, "原作の実写映画", cat=8),
        item(5, "映画", cat=8),
        item(6, "OVA", cat=7),
        item(7, "終了", cat=10),
        item(8, "", cat=10),
        item(9, "特撮", cat=4),
    )
    assert [e.external_id for e in syoboi.parse_titles(body)] == ["1", "5", "6", "7"]


def test_error_result_and_broken_xml_raise():
    with pytest.raises(syoboi.SyoboiError, match="result code 500"):
        list(syoboi.parse_titles(title_xml(code="500")))
    with pytest.raises(syoboi.SyoboiError, match="malformed"):
        list(syoboi.parse_titles(b"<TitleLookupResponse><Result><Code>200</Code></Result><TitleItems><TitleItem>"))
    with pytest.raises(syoboi.SyoboiError, match="no <Result>"):
        list(syoboi.parse_titles(b"<TitleLookupResponse></TitleLookupResponse>"))


# -- harvest: request and storage -------------------------------------------------------------------


async def test_one_request_stores_every_anime_title(conn, client, clock, cache, server):
    result = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert (result.source, result.fetched, result.slices_done, result.slices_skipped) == ("syoboi", SAMPLE_ANIME, 1, 0)
    assert result.slices_error == 0 and result.errors == []
    assert len(server.sent) == 1
    request = server.sent[0][1]
    assert request.method == "GET"
    assert (request.url.host, request.url.path) == ("cal.syoboi.jp", "/db.php")
    assert dict(request.url.params) == {"Command": "TitleLookup", "TID": "*"}
    assert request.headers["user-agent"] == runtime.USER_AGENT

    assert conn.execute("SELECT COUNT(*) FROM entries WHERE source = 'syoboi'").fetchone()[0] == SAMPLE_ANIME
    row = conn.execute("SELECT * FROM entries WHERE id = 'syoboi:3601'").fetchone()
    data = db.row_to_dict(row)
    assert (data["kind"], data["format"], data["title"]) == ("anime", "TV", "新妹魔王の契約者")
    assert data["title_english"] == "The Testament of Sister New Devil"
    assert data["extra"] == {"tid": "3601", "cat": 10, "keywords": []}
    state = db.slice_status(conn, "syoboi", "all")
    assert (state["status"], state["fetched"]) == ("done", SAMPLE_ANIME)


async def test_done_slice_is_skipped_and_fresh_refetches_without_duplicates(conn, client, clock, cache, server):
    await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    again = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)
    assert (again.fetched, again.slices_done, again.slices_skipped) == (0, 0, 1)
    assert len(server.sent) == 1  # nothing was requested

    db.reset_source(conn, "syoboi")  # what `build --fresh` does
    fresh = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)
    assert (fresh.fetched, fresh.slices_done) == (SAMPLE_ANIME, 1)
    assert len(server.sent) == 2
    assert conn.execute("SELECT COUNT(*) FROM entries WHERE source = 'syoboi'").fetchone()[0] == SAMPLE_ANIME
    assert server.times[1] - server.times[0] >= CRAWL_DELAY_SECONDS  # the rerun waited, too


async def test_limit_stops_the_run_but_leaves_the_slice_open(conn, client, clock, cache, server):
    result = await syoboi.harvest(conn, client, clock=clock, limit=5, log=quiet, cache=cache)

    assert (result.fetched, result.slices_done) == (5, 0)
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 5
    state = db.slice_status(conn, "syoboi", "all")
    assert (state["status"], state["fetched"], state["message"]) == ("pending", 5, "limit")
    assert not db.is_slice_done(conn, "syoboi", "all")

    full = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)
    assert (full.fetched, full.slices_done, full.slices_skipped) == (SAMPLE_ANIME, 1, 0)
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == SAMPLE_ANIME


# -- harvest: Crawl-delay: 60, shared with the collector ----------------------------------------------


async def test_waits_until_60_seconds_after_the_collectors_last_request(conn, client, clock, cache, server):
    cache.set(syoboi.LAST_REQUEST_KEY, clock.t - 10, 3600)  # the collector asked 10 s ago

    await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert clock.sleeps == [pytest.approx(50.0)]
    assert server.times == [pytest.approx(START + 50.0)]


async def test_no_wait_when_the_last_request_is_old_or_unknown(conn, client, clock, cache, server, tmp_path):
    await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)  # nothing in the cache yet
    assert clock.sleeps == []

    other = db.connect(tmp_path / "other.db")
    cache.set(syoboi.LAST_REQUEST_KEY, clock.t - 61, 3600)
    await syoboi.harvest(other, client, clock=clock, log=quiet, cache=cache)
    assert clock.sleeps == []
    other.close()


async def test_stamps_the_request_time_before_sending_and_again_when_done(conn, client, clock, cache, server):
    await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    # The handler saw the "before" stamp (the collector must wait for this request, too).
    assert server.stamp_seen == [pytest.approx(START)]
    # Time passes during the download; the "after" stamp is the time it ended.
    assert cache.get(syoboi.LAST_REQUEST_KEY) == pytest.approx(clock.t)


async def test_the_collector_waits_60_seconds_after_the_harvester(conn, client, clock, cache):
    await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)
    clock.sleeps.clear()
    clock.t += 15  # 15 s later the collector wants to send something

    queue = HostQueue(clock=CollectorClock(clock), cache=cache)
    sent_at: list[float] = []

    async def request() -> str:
        sent_at.append(clock.t)
        return "ok"

    assert await queue.run(request) == "ok"
    await queue.aclose()
    assert sent_at == [pytest.approx(START + 60.0)]
    assert sum(clock.sleeps) == pytest.approx(45.0)


def test_the_harvester_follows_the_collectors_key_and_delay(cache, clock):
    # One key, two writers: if the collector's key or delay changes, the harvester follows.
    from backend.adapters import syoboi as collector

    assert syoboi.LAST_REQUEST_KEY == collector._LAST_REQUEST_KEY
    assert syoboi.CrawlDelay(cache, clock).interval == collector.CRAWL_DELAY_SECONDS == 60.0


async def test_retries_wait_out_the_crawl_delay_each_time(conn, client, clock, cache, server):
    server.responses = [lambda: httpx.Response(503), lambda: httpx.Response(500), lambda: httpx.Response(200, content=SAMPLE)]

    result = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert (result.fetched, result.slices_done, result.slices_error) == (SAMPLE_ANIME, 1, 0)
    assert len(server.sent) == 3
    gaps = [b - a for a, b in zip(server.times, server.times[1:])]
    assert all(gap >= CRAWL_DELAY_SECONDS for gap in gaps)  # never closer than 60 s


async def test_a_dropped_connection_is_retried_after_the_delay(conn, clock, cache):
    calls = {"n": 0}
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        times.append(clock.t)
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=SAMPLE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await syoboi.harvest(conn, http, clock=clock, log=quiet, cache=cache)

    assert result.slices_done == 1 and calls["n"] == 2
    assert times[1] - times[0] >= CRAWL_DELAY_SECONDS


async def test_all_attempts_failing_marks_the_slice_error_and_does_not_raise(conn, client, clock, cache, server):
    server.responses = [lambda: httpx.Response(503)] * 3

    result = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert (result.fetched, result.slices_done, result.slices_error) == (0, 0, 1)
    assert len(server.sent) == syoboi.ATTEMPTS
    assert result.errors and "HTTP 503" in result.errors[0]
    state = db.slice_status(conn, "syoboi", "all")
    assert state["status"] == "error" and "HTTP 503" in state["message"]
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0

    # An error slice is not "done": the next run tries again.
    retry = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)
    assert retry.slices_done == 1 and retry.slices_skipped == 0


async def test_client_errors_are_not_retried(conn, client, clock, cache, server):
    server.responses = [lambda: httpx.Response(403)]

    result = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert len(server.sent) == 1
    assert result.slices_error == 1 and "HTTP 403" in result.errors[0]


async def test_rate_limit_response_is_retried(conn, client, clock, cache, server):
    server.responses = [
        lambda: httpx.Response(429, headers={"Retry-After": "5"}),
        lambda: httpx.Response(200, content=SAMPLE),
    ]

    result = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert result.slices_done == 1 and len(server.sent) == 2
    assert server.times[1] - server.times[0] >= CRAWL_DELAY_SECONDS


async def test_a_bad_result_code_is_an_error_slice(conn, client, clock, cache, server):
    server.responses = [lambda: httpx.Response(200, content=title_xml(code="503"))]

    result = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert result.slices_error == 1 and "result code 503" in result.errors[0]
    assert db.slice_status(conn, "syoboi", "all")["status"] == "error"
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


async def test_truncated_xml_is_an_error_slice_and_keeps_old_rows(conn, client, clock, cache, server):
    await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)
    db.reset_source(conn, "syoboi")
    server.responses = [lambda: httpx.Response(200, content=SAMPLE[: len(SAMPLE) // 2])]

    result = await syoboi.harvest(conn, client, clock=clock, log=quiet, cache=cache)

    assert result.slices_error == 1 and "malformed XML" in result.errors[0]
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == SAMPLE_ANIME  # nothing half-written


async def test_opens_and_closes_the_collector_cache_by_default(conn, client, clock, tmp_path, monkeypatch):
    opened: list[TTLCache] = []

    def make_cache() -> TTLCache:
        shared = TTLCache(tmp_path / "default-cache.db")
        opened.append(shared)
        return shared

    monkeypatch.setattr(syoboi, "TTLCache", make_cache)

    result = await syoboi.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_done == 1 and len(opened) == 1
    import sqlite3

    with pytest.raises(sqlite3.ProgrammingError):  # closed again
        opened[0].get("anything")
    check = TTLCache(tmp_path / "default-cache.db")
    assert check.get(syoboi.LAST_REQUEST_KEY) == pytest.approx(clock.t)
    check.close()
