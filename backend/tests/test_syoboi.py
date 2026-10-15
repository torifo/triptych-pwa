"""Tests for the syoboi calendar adapter and router.

No network: httpx.MockTransport serves recordings of the real API
(backend/tests/fixtures/syoboi_*, recorded 2026-10-13) and a fake clock replaces
sleeping, so the 60 second crawl delay is checked without waiting.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import errors
from backend.adapters import syoboi as syoboi_adapter
from backend.adapters.syoboi import (
    CRAWL_DELAY_SECONDS,
    DEFAULT_ABEMA_CHANNEL_IDS,
    DEFAULT_STREAMING_CHANNELS,
    FAILURE_COOLDOWN_SECONDS,
    HostQueue,
    SyoboiAdapter,
    TitleCandidate,
    build_title_dto,
    classify_channel,
    parse_channels,
    parse_programs,
    parse_title_search,
    pick_title,
    search_term,
    season_of,
    validate_tid,
)
from backend.api import syoboi as syoboi_api
from backend.cache import TTLCache
from backend.dto import SourceUnavailable, SyoboiTitleDTO
from backend.timeutil import JST, broadcast_day

FIXTURES = Path(__file__).parent / "fixtures"
USER_AGENT = "triptych/0.1 (personal tracker)"

# Recorded 2026-10-13; "now" for the tests is that day at noon JST.
NOW = int(datetime(2026, 10, 13, 12, 0, tzinfo=JST).timestamp())
KUSURIYA_3 = "薬屋のひとりごと 第3期"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClock:
    """Wall clock whose sleep() just moves time forward."""

    def __init__(self, start: float = NOW) -> None:
        self.t = float(start)
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        await asyncio.sleep(0)


@dataclass
class Sent:
    at: float
    kind: str  # TitleSearch / ProgLookup / ChLookup
    params: dict
    user_agent: str


class FakeSyoboi:
    """Serves the recorded responses and logs every request with the fake time."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.sent: list[Sent] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert request.url.host == "cal.syoboi.jp"
        kind = params.get("Req") or params.get("Command") or "?"
        self.sent.append(Sent(self.clock.now(), kind, params, request.headers.get("user-agent", "")))
        if self.status != 200:
            return httpx.Response(self.status, text="error")
        if kind == "TitleSearch":
            if "薬屋" in params["Search"]:
                return httpx.Response(200, text=read_fixture("syoboi_titlesearch_kusuriya.json"))
            return httpx.Response(200, text=read_fixture("syoboi_titlesearch_notfound.json"))
        if kind == "ProgLookup":
            if params["TID"] == "8025":
                return httpx.Response(200, text=read_fixture("syoboi_proglookup_8025.xml"))
            return httpx.Response(
                200,
                text="<ProgLookupResponse><ProgItems></ProgItems>"
                "<Result><Code>200</Code><Message></Message></Result></ProgLookupResponse>",
            )
        if kind == "ChLookup":
            return httpx.Response(200, text=read_fixture("syoboi_chlookup.xml"))
        raise AssertionError(f"unexpected request: {request.url}")

    def kinds(self) -> list[str]:
        return [s.kind for s in self.sent]

    def gaps(self) -> list[float]:
        return [b.at - a.at for a, b in zip(self.sent, self.sent[1:])]


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def server(clock: FakeClock) -> FakeSyoboi:
    return FakeSyoboi(clock)


@pytest.fixture()
async def adapter(tmp_path, clock, server):
    adapter = SyoboiAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(server.handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        clock=clock,
    )
    yield adapter
    await adapter.aclose()


async def settle(adapter: SyoboiAdapter) -> None:
    await adapter.wait_idle()


# --- TID validation ----------------------------------------------------------


def test_validate_tid_accepts_numeric_string():
    assert validate_tid("6199") == "6199"


@pytest.mark.parametrize("bad_tid", ["", "abc", "12a", "-1", "6199 ", "TID6199"])
def test_validate_tid_rejects_non_numeric(bad_tid):
    with pytest.raises(ValueError):
        validate_tid(bad_tid)


# --- queued -> ok ----------------------------------------------------------------


async def test_cache_miss_returns_queued_without_sending_anything(adapter, server):
    dto = await adapter.title_info(title=KUSURIYA_3)

    assert dto.status == "queued"
    assert dto.programs == [] and dto.streamStarts == []
    assert server.sent == []
    await settle(adapter)


async def test_worker_is_started_lazily_on_first_enqueue(adapter):
    assert adapter._host_queue.worker_running is False
    await adapter.title_info(title=KUSURIYA_3)
    await asyncio.sleep(0)  # let the job reach its first request
    assert adapter._host_queue.worker_running is True
    await settle(adapter)


async def test_after_the_queue_ran_the_result_is_ok_with_programs_and_stream_start(adapter, server):
    await adapter.title_info(title=KUSURIYA_3)
    await settle(adapter)

    dto = await adapter.title_info(title=KUSURIYA_3)

    assert dto.status == "ok"
    assert dto.tid == "8025"
    assert dto.title == "薬屋のひとりごと(第3期)"

    first = dto.programs[0]
    assert (first.channel, first.date, first.time, first.episodeCount) == ("チャンネルNECO", "2026-10-14", "23:30", 50)
    assert [p.date + " " + p.time for p in dto.programs] == sorted(p.date + " " + p.time for p in dto.programs)
    ntv = next(p for p in dto.programs if p.channel == "日本テレビ")
    assert (ntv.date, ntv.time, ntv.isStreaming, ntv.episodeCount) == ("2026-10-16", "23:00", False, 51)
    assert len(dto.programs) <= 50

    # Abemaアニメ airs 2026-10-17 00:00 JST -> broadcast day 10-16, "24:00".
    assert len(dto.streamStarts) == 1
    stream = dto.streamStarts[0]
    assert stream.kind == "stream-start"
    assert stream.label == "ABEMA 配信"
    assert (stream.date, stream.precision, stream.time) == ("2026-10-16", "day", "24:00")
    assert stream.source == "syoboi"
    assert stream.externalId == "8025:239"


async def test_ok_answers_come_from_the_cache_without_requests(adapter, server):
    await adapter.title_info(title=KUSURIYA_3)
    await settle(adapter)
    sent = len(server.sent)

    first = await adapter.title_info(title=KUSURIYA_3)
    again = await adapter.title_info(title="薬屋のひとりごと　第3期")  # same title, other spacing
    by_tid = await adapter.title_info(tid="8025")

    assert first.status == again.status == by_tid.status == "ok"
    assert len(server.sent) == sent


async def test_request_sequence_and_parameters(adapter, server):
    await adapter.title_info(title=KUSURIYA_3)
    await settle(adapter)

    assert server.kinds() == ["TitleSearch", "ChLookup", "ProgLookup"]
    assert server.sent[0].params["Search"] == "薬屋のひとりごと"  # season suffix removed
    assert server.sent[1].params["ChID"] == "*"
    assert server.sent[2].params["TID"] == "8025"
    # 配信中かを見るため、今日より前の 14 日分から取る（1リクエストのまま）
    assert server.sent[2].params["StTime"] == "20260929_000000-"
    assert all(s.user_agent == USER_AGENT for s in server.sent)


# --- Crawl-delay 60 -------------------------------------------------------------


def test_default_interval_is_the_crawl_delay(tmp_path):
    assert CRAWL_DELAY_SECONDS == 60
    assert SyoboiAdapter(cache=TTLCache(db_path=tmp_path / "cache.db"))._host_queue.interval == 60


async def test_never_more_than_one_request_per_60_seconds(adapter, server):
    await adapter.title_info(title=KUSURIYA_3)
    await adapter.title_info(title="存在しない作品名zzqxv")
    await adapter.title_info(tid="4321")
    await settle(adapter)

    assert len(server.sent) == 5  # 2 TitleSearch + ChLookup + 2 ProgLookup
    assert all(gap >= 60 for gap in server.gaps()), server.gaps()
    assert server.sent[-1].at - server.sent[0].at >= 60 * 4


async def test_requests_wait_for_each_other_even_when_enqueued_together(adapter, server, clock):
    for title in ("薬屋のひとりごと", "薬屋のひとりごと 第2期", "薬屋のひとりごと 第3期"):
        await adapter.title_info(title=title)
    await settle(adapter)

    assert all(gap >= 60 for gap in server.gaps())
    assert sum(clock.sleeps) >= 60 * (len(server.sent) - 1)


async def test_a_restarted_process_keeps_waiting(tmp_path, clock, server):
    cache = TTLCache(db_path=tmp_path / "cache.db")

    def make() -> SyoboiAdapter:
        return SyoboiAdapter(
            client=httpx.AsyncClient(transport=httpx.MockTransport(server.handler)),
            cache=cache,
            clock=clock,
        )

    first = make()
    await first.title_info(tid="4321")
    await first.wait_idle()
    await first.aclose()

    clock.t += 10  # the collector was restarted 10 seconds later
    second = make()
    await second.title_info(tid="8025")
    await second.wait_idle()
    await second.aclose()

    assert server.gaps()[-1] >= 60


async def test_host_queue_spaces_requests_by_the_interval(clock):
    queue = HostQueue(interval=60, clock=clock)
    started: list[float] = []

    async def request() -> str:
        started.append(clock.now())
        return "done"

    results = await asyncio.gather(*(queue.run(request) for _ in range(4)))
    await queue.aclose()

    assert results == ["done"] * 4
    assert [round(b - a) for a, b in zip(started, started[1:])] == [60, 60, 60]


async def test_host_queue_counts_failed_requests_and_survives_them(clock):
    queue = HostQueue(interval=60, clock=clock)
    started: list[float] = []

    async def failing() -> str:
        started.append(clock.now())
        raise SourceUnavailable("syoboi", "boom")

    async def fine() -> str:
        started.append(clock.now())
        return "ok"

    with pytest.raises(SourceUnavailable):
        await queue.run(failing)
    assert await queue.run(fine) == "ok"
    await queue.aclose()

    assert started[1] - started[0] >= 60


# --- not found -----------------------------------------------------------------


async def test_unknown_title_becomes_not_found_after_a_single_search(adapter, server):
    first = await adapter.title_info(title="存在しない作品名zzqxv")
    await settle(adapter)
    second = await adapter.title_info(title="存在しない作品名zzqxv")

    assert first.status == "queued"
    assert second.status == "not_found"
    assert second.programs == [] and second.streamStarts == []
    assert server.kinds() == ["TitleSearch"]


async def test_not_found_is_searched_again_on_refresh(adapter, server):
    await adapter.title_info(title="存在しない作品名zzqxv")
    await settle(adapter)

    dto = await adapter.title_info(title="存在しない作品名zzqxv", refresh=True)
    await settle(adapter)

    assert dto.status == "queued"
    assert server.kinds() == ["TitleSearch", "TitleSearch"]


async def test_other_season_is_not_matched(adapter):
    await adapter.title_info(title="薬屋のひとりごと 第4期")
    await settle(adapter)

    assert (await adapter.title_info(title="薬屋のひとりごと 第4期")).status == "not_found"


# --- refresh / dedupe / tid ----------------------------------------------------------


async def test_refresh_drops_the_cache_and_queues_again_within_the_delay(adapter, server):
    await adapter.title_info(title=KUSURIYA_3)
    await settle(adapter)
    assert (await adapter.title_info(title=KUSURIYA_3)).status == "ok"

    dto = await adapter.title_info(title=KUSURIYA_3, refresh=True)
    assert dto.status == "queued"
    await settle(adapter)

    assert (await adapter.title_info(title=KUSURIYA_3)).status == "ok"
    # Only the programs are fetched again: the TID and the channel table are kept.
    assert server.kinds() == ["TitleSearch", "ChLookup", "ProgLookup", "ProgLookup"]
    assert all(gap >= 60 for gap in server.gaps())


async def test_concurrent_calls_for_the_same_title_share_one_job(adapter, server):
    for _ in range(3):
        assert (await adapter.title_info(title=KUSURIYA_3)).status == "queued"
    await settle(adapter)

    assert server.kinds().count("TitleSearch") == 1


async def test_lookup_by_tid_then_title_is_known_afterwards(adapter, server):
    assert (await adapter.title_info(tid="8025")).status == "queued"
    await settle(adapter)
    dto = await adapter.title_info(tid="8025")

    assert dto.status == "ok" and dto.tid == "8025"
    assert dto.title is None  # nothing in the cache names this TID yet
    assert len(dto.programs) > 0
    assert server.kinds() == ["ChLookup", "ProgLookup"]

    await adapter.title_info(title=KUSURIYA_3, refresh=True)
    await settle(adapter)
    assert (await adapter.title_info(tid="8025")).title == "薬屋のひとりごと(第3期)"


async def test_tid_with_title_hint_keeps_the_title(adapter):
    await adapter.title_info(tid="8025", title="薬屋のひとりごと(第3期)")
    await settle(adapter)

    assert (await adapter.title_info(tid="8025")).title == "薬屋のひとりごと(第3期)"


async def test_tid_without_programs_is_ok_and_empty(adapter):
    await adapter.title_info(tid="4321")
    await settle(adapter)

    dto = await adapter.title_info(tid="4321")
    assert dto.status == "ok" and dto.programs == [] and dto.streamStarts == []


async def test_bad_arguments_raise_value_error(adapter):
    with pytest.raises(ValueError):
        await adapter.title_info()
    with pytest.raises(ValueError):
        await adapter.title_info(title="   ")
    with pytest.raises(ValueError):
        await adapter.title_info(title="！？")
    with pytest.raises(ValueError):
        await adapter.title_info(tid="12a")


# --- failures --------------------------------------------------------------------------


async def test_failed_job_surfaces_on_the_next_call_then_retries_after_the_cooldown(adapter, server, clock):
    server.status = 500
    assert (await adapter.title_info(title=KUSURIYA_3)).status == "queued"
    await settle(adapter)

    with pytest.raises(SourceUnavailable) as exc_info:
        await adapter.title_info(title=KUSURIYA_3)
    assert exc_info.value.source == "syoboi"
    assert len(server.sent) == 1  # no retry inside the cooldown

    server.status = 200
    clock.t += FAILURE_COOLDOWN_SECONDS + 1
    assert (await adapter.title_info(title=KUSURIYA_3)).status == "queued"
    await settle(adapter)
    assert (await adapter.title_info(title=KUSURIYA_3)).status == "ok"


async def test_refresh_retries_a_failed_lookup_at_once(adapter, server):
    server.status = 500
    await adapter.title_info(tid="8025")
    await settle(adapter)
    with pytest.raises(SourceUnavailable):
        await adapter.title_info(tid="8025")

    server.status = 200
    assert (await adapter.title_info(tid="8025", refresh=True)).status == "queued"
    await settle(adapter)
    assert (await adapter.title_info(tid="8025")).status == "ok"


async def test_channel_table_failure_falls_back_to_the_built_in_ids(tmp_path, clock, server):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("Command") == "ChLookup":
            server.sent.append(Sent(clock.now(), "ChLookup", {}, ""))
            return httpx.Response(500, text="error")
        return server.handler(request)

    adapter = SyoboiAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=TTLCache(db_path=tmp_path / "cache.db"),
        clock=clock,
    )
    await adapter.title_info(tid="8025")
    await adapter.wait_idle()
    dto = await adapter.title_info(tid="8025")
    await adapter.aclose()

    assert dto.status == "ok"
    assert [s.label for s in dto.streamStarts] == ["ABEMA 配信"]  # decided by the built-in ID table


# --- parsing: fixtures --------------------------------------------------------------------


def test_parse_title_search_fixture():
    candidates = parse_title_search(read_fixture("syoboi_titlesearch_kusuriya.json"))

    assert [c.tid for c in candidates] == ["6793", "7296", "8025"]
    assert candidates[2].title == "薬屋のひとりごと(第3期)"
    assert candidates[2].short_title == "薬屋のひとりごと(3)"
    assert [c.has_programs for c in candidates] == [False, False, True]


@pytest.mark.parametrize("text", ['{"Titles":null}', '{"Titles":[]}', '{"Titles":{}}', "{}"])
def test_parse_title_search_no_hit(text):
    assert parse_title_search(text) == []


def test_parse_title_search_rejects_non_json():
    with pytest.raises(SourceUnavailable):
        parse_title_search("<html>")


def test_parse_channels_fixture():
    table = parse_channels(read_fixture("syoboi_chlookup.xml"))

    assert len(table) == 288
    assert table["234"] == ("dアニメストア", "7")
    assert table["4"] == ("日本テレビ", "1")
    assert table["239"] == ("Abemaアニメ", "23")


def test_parse_programs_fixture():
    programs = parse_programs(read_fixture("syoboi_proglookup_8025.xml"))

    assert len(programs) == 90
    ntv = next(p for p in programs if p.chid == "4")
    assert broadcast_day(ntv.start) == ("2026-10-16", "23:00")
    assert ntv.count == 51


def test_parse_programs_skips_deleted_and_tolerates_empty_count():
    xml = (
        "<ProgLookupResponse><ProgItems>"
        "<ProgItem><ChID>4</ChID><StTime>2026-10-16 23:00:00</StTime><Count></Count><Deleted>0</Deleted></ProgItem>"
        "<ProgItem><ChID>4</ChID><StTime>2026-10-23 23:00:00</StTime><Count>52</Count><Deleted>1</Deleted></ProgItem>"
        "</ProgItems><Result><Code>200</Code><Message></Message></Result></ProgLookupResponse>"
    )
    programs = parse_programs(xml)

    assert len(programs) == 1 and programs[0].count is None


def test_parse_programs_no_programs_code_is_not_an_error():
    xml = "<ProgLookupResponse><Result><Code>404</Code><Message>none</Message></Result></ProgLookupResponse>"
    assert parse_programs(xml) == []


@pytest.mark.parametrize(
    "parser, text",
    [
        (parse_programs, "<not><valid"),
        (parse_channels, "<not><valid"),
        (parse_programs, "<ProgLookupResponse><Result><Code>500</Code><Message>x</Message></Result></ProgLookupResponse>"),
        (parse_channels, "<ChLookupResponse><Result><Code>200</Code></Result><ChItems/></ChLookupResponse>"),
    ],
)
def test_malformed_or_failed_xml_raises_source_unavailable(parser, text):
    with pytest.raises(SourceUnavailable):
        parser(text)


# --- streaming judgement ------------------------------------------------------------------


STREAMING_IDS = ["107", "132", "136", "165", "178", "210", "234", "248", "256", "257", "268", "269", "274", "281"]


def test_streaming_is_decided_by_the_channel_group():
    table = parse_channels(read_fixture("syoboi_chlookup.xml"))

    for chid in STREAMING_IDS:
        assert classify_channel(chid, table).is_streaming, chid
    for chid in ["1", "4", "20", "21", "26", "71", "80", "135"]:  # NHK / 民放 / AT-X / CS / PlayStation Store
        assert not classify_channel(chid, table).is_streaming, chid


def test_abema_channels_are_one_service_and_others_keep_their_name():
    table = parse_channels(read_fixture("syoboi_chlookup.xml"))

    assert classify_channel("239", table).service == "ABEMA"
    assert classify_channel("239", table).name == "Abemaアニメ"
    assert classify_channel("281", table).service == "ABEMA"
    assert classify_channel("256", table).service == "Netflix"


def test_built_in_ids_agree_with_the_recorded_channel_table():
    table = parse_channels(read_fixture("syoboi_chlookup.xml"))

    for chid, name in DEFAULT_STREAMING_CHANNELS.items():
        assert classify_channel(chid, None) == classify_channel(chid, table)
        assert table[chid][0] == name
    for chid in DEFAULT_ABEMA_CHANNEL_IDS:
        assert classify_channel(chid, None).service == classify_channel(chid, table).service == "ABEMA"


def test_unknown_channel_is_not_streaming():
    assert not classify_channel("99999", None).is_streaming
    assert not classify_channel("99999", parse_channels(read_fixture("syoboi_chlookup.xml"))).is_streaming


# --- build_title_dto -----------------------------------------------------------------------


def _build(today: str, channels="fixture"):
    table = parse_channels(read_fixture("syoboi_chlookup.xml")) if channels == "fixture" else channels
    return build_title_dto("8025", "薬屋のひとりごと(第3期)", parse_programs(read_fixture("syoboi_proglookup_8025.xml")), table, today)


def test_programs_are_capped_at_50_and_sorted():
    dto = _build("2026-10-13")

    assert len(dto.programs) == 50
    keys = [(p.date, p.time) for p in dto.programs]
    assert keys == sorted(keys)


def test_only_programs_from_today_on_are_listed():
    dto = _build("2026-10-17")

    assert min(p.date for p in dto.programs) == "2026-10-17"
    # The Abema slot of 10-16 24:00 is gone: the next one (10-23 24:00) is the stream start.
    assert [(s.date, s.time) for s in dto.streamStarts] == [("2026-10-23", "24:00")]
    # ...and since Abema already streamed on 10-16, it is streaming now (the 10-23 slot is the next one).
    assert dto.startedStreams == [dto.streamStarts[0].externalId]


def test_nothing_is_streaming_yet_without_earlier_slots():
    dto = _build("2026-10-13")  # recorded from 10-13 on: no earlier programs

    assert [s.date for s in dto.streamStarts] == ["2026-10-16"]
    assert dto.startedStreams == []


def test_started_streams_look_back_14_days_per_service():
    pr = syoboi_adapter.Program
    t = lambda month, day, hour: int(datetime(2026, month, day, hour, 0, tzinfo=JST).timestamp())  # noqa: E731
    programs = [
        pr("244", t(10, 9, 12)),  # ABEMA (other channel) 4 days ago
        pr("239", t(10, 16, 12)),  # ABEMA next
        pr("256", t(9, 28, 12)),  # Netflix 15 days ago: outside the window
        pr("256", t(10, 20, 12)),  # Netflix next
        pr("234", t(9, 29, 12)),  # d 14 days ago: inside the window
        pr("234", t(10, 13, 12)),  # d today
        pr("4", t(10, 6, 23)),  # terrestrial: never a stream
        pr("269", t(10, 14, 12)),  # TVer starts later: not streaming yet
    ]

    dto = build_title_dto("77", "x", programs, None, "2026-10-13")

    # Earlier slots are only used for the judgement; programs and stream starts stay from today on.
    assert min(p.date for p in dto.programs) == "2026-10-13"
    assert [(s.label, s.date, s.externalId) for s in dto.streamStarts] == [
        ("dアニメストア 配信", "2026-10-13", "77:234"),
        ("TVer 配信", "2026-10-14", "77:269"),
        ("ABEMA 配信", "2026-10-16", "77:239"),
        ("Netflix 配信", "2026-10-20", "77:256"),
    ]
    assert dto.startedStreams == ["77:234", "77:239"]


def test_cached_answers_from_before_started_streams_are_unknown():
    dto = SyoboiTitleDTO.model_validate({"status": "ok", "tid": "1", "streamStarts": []})

    assert dto.startedStreams is None


def test_late_night_program_belongs_to_the_previous_day():
    assert broadcast_day(int(datetime(2026, 10, 17, 0, 0, tzinfo=JST).timestamp())) == ("2026-10-16", "24:00")
    assert broadcast_day(int(datetime(2026, 10, 17, 4, 59, tzinfo=JST).timestamp())) == ("2026-10-16", "28:59")
    assert broadcast_day(int(datetime(2026, 10, 17, 5, 0, tzinfo=JST).timestamp())) == ("2026-10-17", "05:00")


def test_broadcast_day_cutoff_for_today():
    # 02:00 JST on 10-17 is still the broadcast day 10-16: its 25:30 slot is not dropped.
    start = int(datetime(2026, 10, 17, 1, 30, tzinfo=JST).timestamp())
    program = syoboi_adapter.Program(chid="239", start=start, count=1)

    assert len(build_title_dto("1", None, [program], None, "2026-10-16").programs) == 1
    assert len(build_title_dto("1", None, [program], None, "2026-10-17").programs) == 0


def test_first_program_per_streaming_service():
    pr = syoboi_adapter.Program
    t = lambda day, hour: int(datetime(2026, 11, day, hour, 0, tzinfo=JST).timestamp())  # noqa: E731
    programs = [
        pr("256", t(10, 12)),  # Netflix, later
        pr("234", t(5, 12)),  # d
        pr("256", t(3, 12)),  # Netflix, first
        pr("4", t(1, 23)),  # terrestrial
        pr("239", t(8, 0)),  # ABEMA
        pr("244", t(7, 0)),  # ABEMA other channel, earlier
    ]
    dto = build_title_dto("77", "x", programs, None, "2026-11-01")

    assert [(s.label, s.date, s.externalId) for s in dto.streamStarts] == [
        ("Netflix 配信", "2026-11-03", "77:256"),
        ("dアニメストア 配信", "2026-11-05", "77:234"),
        ("ABEMA 配信", "2026-11-06", "77:244"),
    ]
    assert [p.isStreaming for p in dto.programs[:2]] == [False, True]


# --- title matching ---------------------------------------------------------------------------


@pytest.fixture()
def kusuriya():
    return parse_title_search(read_fixture("syoboi_titlesearch_kusuriya.json"))


@pytest.mark.parametrize(
    "query, tid",
    [
        ("薬屋のひとりごと 第3期", "8025"),
        ("薬屋のひとりごと(第3期)", "8025"),
        ("薬屋のひとりごと第3期", "8025"),
        ("薬屋のひとりごと 3rd Season", "8025"),
        ("薬屋のひとりごと", "6793"),
        ("薬屋のひとりごと 第2期", "7296"),
    ],
)
def test_pick_title_follows_the_season(kusuriya, query, tid):
    picked = pick_title(kusuriya, query)
    assert picked is not None and picked.tid == tid


@pytest.mark.parametrize("query", ["薬屋のひとりごと 第4期", "全然違う作品名", "あ"])
def test_pick_title_returns_none_without_a_match(kusuriya, query):
    assert pick_title(kusuriya, query) is None


def test_pick_title_prefers_the_one_with_upcoming_programs_on_a_tie():
    candidates = [TitleCandidate("1", "作品"), TitleCandidate("2", "作品", has_programs=True)]
    assert pick_title(candidates, "作品").tid == "2"


@pytest.mark.parametrize(
    "title, expected",
    [
        ("薬屋のひとりごと 第3期", "薬屋のひとりごと"),
        ("薬屋のひとりごと(第3期)", "薬屋のひとりごと"),
        ("Re:ゼロから始める異世界生活 3rd Season", "Re:ゼロから始める異世界生活"),
        ("無職転生 Season 2", "無職転生"),
        ("薬屋のひとりごと", "薬屋のひとりごと"),
        ("第3期", "第3期"),
    ],
)
def test_search_term_drops_the_season_suffix(title, expected):
    assert search_term(title) == expected


def test_season_of():
    assert season_of("薬屋のひとりごと(第2期)") == 2
    assert season_of("薬屋のひとりごと 第３期") == 3
    assert season_of("Overlord 4th Season") == 4
    assert season_of("薬屋のひとりごと") == 1


# --- router --------------------------------------------------------------------------------------


class StubAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: Exception | None = None

    async def title_info(self, tid=None, title=None, refresh=False):
        self.calls.append({"tid": tid, "title": title, "refresh": refresh})
        if self.error is not None:
            raise self.error
        if tid is not None:
            validate_tid(tid)
        elif not title:
            raise ValueError("tid or title is required")
        return SyoboiTitleDTO(status="queued", tid=tid)


@pytest.fixture()
def stub():
    return StubAdapter()


@pytest.fixture()
def client(stub):
    app = FastAPI()
    app.include_router(syoboi_api.router)
    app.dependency_overrides[syoboi_api.get_adapter] = lambda: stub
    errors.clear_adapter_error("syoboi")
    yield TestClient(app)
    errors.clear_adapter_error("syoboi")


def test_route_by_title(client, stub):
    response = client.get("/api/syoboi/title", params={"title": "薬屋のひとりごと 第3期"})

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert stub.calls == [{"tid": None, "title": "薬屋のひとりごと 第3期", "refresh": False}]


def test_route_by_tid_with_refresh(client, stub):
    response = client.get("/api/syoboi/title", params={"tid": "8025", "refresh": "1"})

    assert response.status_code == 200
    assert response.json()["tid"] == "8025"
    assert stub.calls == [{"tid": "8025", "title": None, "refresh": True}]


def test_route_legacy_path_goes_to_the_same_lookup(client, stub):
    response = client.get("/api/syoboi/title/8025")

    assert response.status_code == 200
    assert stub.calls == [{"tid": "8025", "title": None, "refresh": False}]


@pytest.mark.parametrize("params", [{}, {"tid": ""}, {"tid": "abc"}, {"title": ""}])
def test_route_rejects_bad_input_with_400(client, params):
    assert client.get("/api/syoboi/title", params=params).status_code == 400


def test_route_source_unavailable_is_502_and_recorded(client, stub):
    stub.error = SourceUnavailable("syoboi", "request failed: boom")

    response = client.get("/api/syoboi/title", params={"title": "x"})

    assert response.status_code == 502
    assert response.json()["detail"] == "request failed: boom"
    assert errors.last_adapter_errors["syoboi"].message == "request failed: boom"
