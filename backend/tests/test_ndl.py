"""Tests for backend.adapters.ndl and the `/api/ndl` route.

All network access is stubbed via httpx.MockTransport. Fixtures are real
responses recorded on 2026-10-13 (UA `triptych/0.1 (personal tracker)`):

- ndl_books_honzuki_from2024.xml: `title=本好きの下剋上&mediatype=books&from=2024&cnt=500`
- ndl_books_slime_from2024.xml: `title=転生したらスライムだった件&mediatype=books&from=2024&cnt=500`
- openbd_batch_new_volumes.json: one `/v1/get?isbn=...` for `BATCH_ISBNS`
- ndl_search_honzuki.xml: an older trimmed response (no mediatype, CDs included)

Records that the real data lacks (a translation, an anthology, a bundled
special edition, a duplicate ISBN) are injected with `_inject()`.
"""

from __future__ import annotations

import json
import random
import re
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import errors
from backend.adapters import ndl, openbd
from backend.api import ndl as ndl_api
from backend.cache import TTLCache
from backend.dto import NdlSeriesDTO, SourceUnavailable
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"
HONZUKI_XML = (FIXTURES / "ndl_books_honzuki_from2024.xml").read_bytes()
SLIME_XML = (FIXTURES / "ndl_books_slime_from2024.xml").read_bytes()
LEGACY_XML = (FIXTURES / "ndl_search_honzuki.xml").read_bytes()

HONZUKI = "本好きの下剋上"
SLIME = "転生したらスライムだった件"
TODAY = date(2026, 10, 13)

BATCH_ISBNS = [
    "9784867949443",
    "9784867949467",
    "9784868540700",
    "9784867949450",
    "9784867949269",
    "9784867949474",
    "9784868540366",
    "9784065444528",
    "9784065444443",
    "9784867169995",
    "9784825000360",
    "9784867169629",
    "9784065437544",
]
OPENBD_BY_ISBN = dict(
    zip(
        BATCH_ISBNS,
        json.loads((FIXTURES / "openbd_batch_new_volumes.json").read_text(encoding="utf-8")),
    )
)

# 本好き: the TOジュニア文庫 volume missing from openBD (audit case).
JUNIOR_9 = "9784868540366"
JUNIOR_UPCOMING = "9784868541233"  # 近刊「本好きの下剋上　第三部　領主の養女10」
MANGA_UPCOMING_4_13 = "9784868542186"  # 近刊 第四部「貴族院の図書館を救いたい！13」
MANGA_UPCOMING_HANNELORE = "9784868541653"  # 近刊「恋してみたいお姫様 2」
MANGA_3_10 = "9784867949450"  # 第3部[10], issued 2026.8
HANNELORE_3 = "9784867949269"  # novel spin-off, issued 2026.5
FANBOOK = "9784867943304"  # 本好きの下剋上ふぁんぶっく 9


@pytest.fixture(autouse=True)
def fast_rate_limiters(monkeypatch):
    monkeypatch.setattr(ndl, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))
    monkeypatch.setattr(openbd, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))


# --- helpers -----------------------------------------------------------------------


def _isbn(prefix12: str) -> str:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(prefix12))
    return prefix12 + str((10 - total % 10) % 10)


def _item(
    title: str,
    isbn: Optional[str],
    issued: str,
    *,
    ndc: Optional[str] = None,
    volume: Optional[str] = None,
    series: Optional[str] = None,
    edition: Optional[str] = None,
    creators: tuple[str, ...] = (),
    categories: tuple[str, ...] = ("図書", "紙"),
    publisher: str = "TOブックス",
) -> str:
    parts = [f"<title>{title}</title>"]
    parts += [f"<category>{c}</category>" for c in categories]
    parts.append(f"<dc:title>{title}</dc:title>")
    parts += [f"<dc:creator>{c}</dc:creator>" for c in creators]
    if volume:
        parts.append(f"<dcndl:volume>{volume}</dcndl:volume>")
    if series:
        parts.append(f"<dcndl:seriesTitle>{series}</dcndl:seriesTitle>")
    if edition:
        parts.append(f"<dcndl:edition>{edition}</dcndl:edition>")
    parts.append(f"<dc:publisher>{publisher}</dc:publisher>")
    parts.append(f"<dcterms:issued>{issued}</dcterms:issued>")
    if isbn:
        parts.append(f'<dc:identifier xsi:type="dcndl:ISBN">{isbn}</dc:identifier>')
    if ndc:
        parts.append(f'<dc:subject xsi:type="dcndl:NDC10">{ndc}</dc:subject>')
    return "<item>" + "".join(parts) + "</item>"


def _inject(xml: bytes, *items: str) -> bytes:
    text = xml.decode("utf-8")
    return text.replace("</channel>", "".join(items) + "</channel>", 1).encode("utf-8")


def _reverse_items(xml: bytes) -> bytes:
    text = xml.decode("utf-8")
    items = re.findall(r"<item>.*?</item>", text, flags=re.S)
    head = text[: text.index("<item>")]
    tail = text[text.rindex("</item>") + len("</item>") :]
    return (head + "".join(reversed(items)) + tail).encode("utf-8")


class Calls:
    def __init__(self) -> None:
        self.ndl: list[httpx.Request] = []
        self.openbd: list[list[str]] = []


def _client(
    xml: bytes,
    calls: Optional[Calls] = None,
    *,
    openbd_overrides: Optional[dict[str, dict]] = None,
    openbd_status: int = 200,
) -> httpx.AsyncClient:
    calls = calls or Calls()
    books = dict(OPENBD_BY_ISBN)
    books.update(openbd_overrides or {})

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(ndl.NDL_API_URL):
            calls.ndl.append(request)
            return httpx.Response(200, content=xml)
        if url.startswith(openbd.OPENBD_API_URL):
            isbns = request.url.params["isbn"].split(",")
            calls.openbd.append(isbns)
            if openbd_status != 200:
                return httpx.Response(openbd_status)
            return httpx.Response(200, json=[books.get(i) for i in isbns])
        raise AssertionError(f"unexpected request: {url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _series(xml: bytes, title: str, kind: str, line=None, **kwargs) -> NdlSeriesDTO:
    calls = kwargs.pop("calls", None)
    overrides = kwargs.pop("openbd_overrides", None)
    status = kwargs.pop("openbd_status", 200)
    async with _client(xml, calls, openbd_overrides=overrides, openbd_status=status) as client:
        return await ndl.series(
            title, kind, line=line, client=client, cache=kwargs.pop("cache", None), today=TODAY, **kwargs
        )


def _isbns(dto: NdlSeriesDTO) -> list[str]:
    return [r.externalId for r in dto.releases]


def _by_isbn(dto: NdlSeriesDTO, isbn: str):
    return next(r for r in dto.releases if r.externalId == isbn)


def _assert_newest_first(dto: NdlSeriesDTO) -> None:
    dates = [r.date for r in dto.releases]
    assert dates == sorted(dates, reverse=True)


# --- dates ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026.8.25", ("2026-08-25", "day")),
        ("2026-08-25", ("2026-08-25", "day")),
        ("2026-12-10", ("2026-12-10", "day")),
        ("2026.8", ("2026-08-01", "month")),
        ("2024年12月", ("2024-12-01", "month")),
        ("2026", ("2026-01-01", "year")),
        ("[2026]", ("2026-01-01", "year")),
        ("2018.1-2020.1", None),
        ("2019.-", None),
        ("2015.10-2016.7", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_issued_keeps_precision(raw, expected):
    assert ndl.parse_issued(raw) == expected


# --- parsing ----------------------------------------------------------------------------


def test_parse_records_reads_isbn_ndc_series_and_date():
    records = ndl.parse_records(HONZUKI_XML)
    assert len(records) == 35

    junior = next(r for r in records if r.isbn == JUNIOR_9)
    assert junior.series_title == "TOジュニア文庫 ; か-1-24"
    assert junior.volume == "第3部[9]"
    assert junior.ndc == "913.6"
    assert (junior.date, junior.precision) == ("2026-07-01", "month")
    assert "児童図書" in junior.genres

    upcoming = next(r for r in records if r.isbn == MANGA_UPCOMING_4_13)
    assert upcoming.ndc is None
    assert (upcoming.date, upcoming.precision) == ("2026-12-10", "day")
    assert upcoming.publisher == "TOブックス"  # ＴＯブックス, NFKC


def test_parse_records_leaves_non_books_without_isbn():
    records = ndl.parse_records(LEGACY_XML)
    cds = [r for r in records if "録音資料" in r.categories]
    assert cds and all(r.isbn is None for r in cds)


def test_parse_records_raises_source_unavailable_on_malformed_xml():
    with pytest.raises(SourceUnavailable):
        ndl.parse_records(b"not xml at all <<<")


# --- request -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_series_makes_one_books_request_from_two_years_back():
    calls = Calls()
    await _series(HONZUKI_XML, HONZUKI, "novel", calls=calls)

    assert len(calls.ndl) == 1
    params = calls.ndl[0].url.params
    assert params["title"] == HONZUKI
    assert params["mediatype"] == "books"
    assert params["from"] == "2024"
    assert params["cnt"] == "500"
    assert calls.ndl[0].headers["user-agent"] == "triptych/0.1 (personal tracker)"


# --- 本好きの下剋上 -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_honzuki_manga_main_line_puts_upcoming_and_latest_first():
    dto = await _series(HONZUKI_XML, HONZUKI, "manga")

    assert dto.line == ""
    assert [(l.key, l.label) for l in dto.lines] == [("", "本編")]
    _assert_newest_first(dto)
    top = [(r.externalId, r.label, r.date, r.precision) for r in dto.releases[:3]]
    assert top[0] == (MANGA_UPCOMING_4_13, "第四部「貴族院の図書館を救いたい!13」", "2026-12-10", "day")
    assert top[1][0] == MANGA_UPCOMING_HANNELORE
    assert top[1][2:] == ("2026-10-15", "day")
    assert {r.externalId for r in dto.releases[2:4]} == {MANGA_3_10, "9784868540700"}
    assert all(r.kind == "volume" and r.source == "ndl" for r in dto.releases)


@pytest.mark.asyncio
async def test_honzuki_novel_defaults_to_main_line_not_junior_bunko():
    dto = await _series(HONZUKI_XML, HONZUKI, "novel")

    assert dto.line == ""
    lines = {l.key: (l.label, l.count) for l in dto.lines}
    assert lines == {"": ("本編", 5), "TOジュニア文庫": ("TOジュニア文庫", 9)}
    _assert_newest_first(dto)
    first = dto.releases[0]
    assert (first.externalId, first.label, first.date, first.precision) == (
        HANNELORE_3,
        "ハンネローレの貴族院五年生 3",
        "2026-05-01",
        "month",
    )
    assert "第5部[12]" in [r.label for r in dto.releases]
    assert JUNIOR_9 not in _isbns(dto)
    assert JUNIOR_UPCOMING not in _isbns(dto)


@pytest.mark.asyncio
async def test_honzuki_junior_bunko_is_its_own_line_with_its_upcoming_volume():
    dto = await _series(HONZUKI_XML, HONZUKI, "novel", line="TOジュニア文庫")

    assert dto.line == "TOジュニア文庫"
    assert len(dto.releases) == 9
    first = dto.releases[0]
    assert (first.externalId, first.label, first.date, first.precision) == (
        JUNIOR_UPCOMING,
        "第三部 領主の養女10",
        "2026-10-01",
        "day",
    )
    assert dto.releases[1].label == "第3部[9]"


@pytest.mark.asyncio
async def test_volume_missing_from_openbd_keeps_its_ndl_date():
    calls = Calls()
    dto = await _series(HONZUKI_XML, HONZUKI, "novel", line="TOジュニア文庫", calls=calls)

    assert OPENBD_BY_ISBN[JUNIOR_9] is None  # really absent from openBD
    assert any(JUNIOR_9 in batch for batch in calls.openbd)
    release = _by_isbn(dto, JUNIOR_9)
    assert (release.date, release.precision) == ("2026-07-01", "month")


@pytest.mark.asyncio
async def test_issued_year_month_is_month_precision():
    dto = await _series(HONZUKI_XML, HONZUKI, "manga")
    release = _by_isbn(dto, MANGA_3_10)  # dcterms:issued 2026.8, openBD null
    assert (release.date, release.precision, release.label) == ("2026-08-01", "month", "第3部[10]")


@pytest.mark.asyncio
async def test_kind_manga_returns_only_comics():
    records = {r.isbn: r for r in ndl.parse_records(HONZUKI_XML)}
    dto = await _series(HONZUKI_XML, HONZUKI, "manga")

    assert len(dto.releases) == 17
    for release in dto.releases:
        ndc = records[release.externalId].ndc
        assert ndc is None or ndc.startswith("726")
    # 近刊 records have no NDC: their comic artists only appear on comics.
    assert {MANGA_UPCOMING_4_13, MANGA_UPCOMING_HANNELORE} <= set(_isbns(dto))
    assert HANNELORE_3 not in _isbns(dto)


@pytest.mark.asyncio
async def test_fanbook_and_artbooks_are_excluded():
    for kind in ("novel", "manga"):
        dto = await _series(HONZUKI_XML, HONZUKI, kind)
        for line in dto.lines:
            picked = await _series(HONZUKI_XML, HONZUKI, kind, line=line.key)
            assert FANBOOK not in _isbns(picked)
            assert all("画集" not in r.label for r in picked.releases)


@pytest.mark.asyncio
async def test_translation_anthology_and_bundled_special_edition_are_excluded():
    translation = _isbn("978957000001")
    anthology = _isbn("978486854900")
    special = _isbn("978486854901")
    ebook = _isbn("978486854902")
    xml = _inject(
        HONZUKI_XML,
        _item(f"{HONZUKI} : 司書になるためには手段を選んでいられません", translation, "2026.9",
              ndc="913.6", volume="第5部[11]"),
        _item(f"{HONZUKI} 公式コミックアンソロジー", anthology, "2026.9", ndc="726.1", volume="10"),
        _item(f"{HONZUKI}　第三部　領主の養女10　クリアファイル付き特装版", special, "2026-10-01",
              series="TOジュニア文庫"),
        _item(f"{HONZUKI} : 司書になるためには手段を選んでいられません", ebook, "2026.9",
              ndc="913.6", volume="第5部[13]", categories=("電子書籍",)),
    )
    title_key = ndl._key(HONZUKI)
    reasons = {r.isbn: ndl.exclusion_reason(r, title_key) for r in ndl.parse_records(xml)}
    assert reasons[translation] == "foreign-isbn"
    assert reasons[anthology] == "excluded-word"
    assert reasons[special] == "excluded-word"
    assert reasons[ebook] == "electronic"

    seen: set[str] = set()
    for kind in ("novel", "manga"):
        dto = await _series(xml, HONZUKI, kind)
        for line in dto.lines:
            seen.update(_isbns(await _series(xml, HONZUKI, kind, line=line.key)))
    assert seen.isdisjoint({translation, anthology, special, ebook})


@pytest.mark.parametrize(
    "title,reason",
    [
        ("本好きの下剋上ドラマCD 9", "excluded-word"),
        ("本好きの下剋上 公式コミックアンソロジー", "excluded-word"),
        ("本好きの下剋上 カレンダー2026", "excluded-word"),
        ("本好きの下剋上 第3部 限定版", "excluded-word"),
        ("本好きの下剋上 アクリルスタンド同梱版", "excluded-word"),
        ("本好きの下剋上 設定資料集", "excluded-word"),
        ("本好きの下剋上 公式ガイドブック", "excluded-word"),
        ("本好きの下剋上と付き合う方法", None),  # 付き合う is not 付き
        ("このライトノベルがすごい! 2024", "title"),
    ],
)
def test_exclusion_words(title, reason):
    record = ndl.NdlRecord(title=title, isbn="9784867940228", date="2026-01-01", precision="year")
    assert ndl.exclusion_reason(record, ndl._key(HONZUKI)) == reason


@pytest.mark.asyncio
async def test_same_isbn_listed_twice_becomes_one_with_the_finer_date():
    duplicate = _item(
        "本好きの下剋上　第三部　領主の養女9", JUNIOR_9, "2026-07-10", series="TOジュニア文庫",
        volume="9",
    )
    dto = await _series(_inject(HONZUKI_XML, duplicate), HONZUKI, "novel", line="TOジュニア文庫")

    matches = [r for r in dto.releases if r.externalId == JUNIOR_9]
    assert len(matches) == 1
    assert (matches[0].date, matches[0].precision) == ("2026-07-10", "day")
    assert matches[0].label == "第3部[9]"  # the catalogued record's volume
    assert len(dto.releases) == 9


@pytest.mark.asyncio
async def test_openbd_day_pubdate_sharpens_a_month_date_in_one_batch():
    calls = Calls()
    record = json.loads(json.dumps(OPENBD_BY_ISBN["9784867949474"]))  # any non-null record
    record["summary"].update(isbn=MANGA_3_10, pubdate="20260825")
    dto = await _series(
        HONZUKI_XML, HONZUKI, "manga", calls=calls, openbd_overrides={MANGA_3_10: record}
    )

    assert len(calls.openbd) == 1
    assert set(calls.openbd[0]) == {MANGA_3_10, "9784868540700", "9784867949467", "9784867949443"}
    release = _by_isbn(dto, MANGA_3_10)
    assert (release.date, release.precision) == ("2026-08-25", "day")
    assert dto.releases[2].externalId == MANGA_3_10  # re-sorted above 2026-08 (month)
    _assert_newest_first(dto)


@pytest.mark.asyncio
async def test_openbd_empty_pubdate_keeps_the_ndl_date():
    dto = await _series(HONZUKI_XML, HONZUKI, "novel")
    assert OPENBD_BY_ISBN[HANNELORE_3]["summary"]["pubdate"] == ""
    release = _by_isbn(dto, HANNELORE_3)
    assert (release.date, release.precision) == ("2026-05-01", "month")


@pytest.mark.asyncio
async def test_openbd_outage_keeps_ndl_dates_and_is_recorded(monkeypatch):
    monkeypatch.setattr(errors, "last_adapter_errors", {})
    dto = await _series(HONZUKI_XML, HONZUKI, "manga", openbd_status=503)

    assert (_by_isbn(dto, MANGA_3_10).date, _by_isbn(dto, MANGA_3_10).precision) == (
        "2026-08-01",
        "month",
    )
    assert "openbd" in errors.last_adapter_errors


@pytest.mark.asyncio
async def test_result_does_not_depend_on_ndl_return_order():
    for kind in ("novel", "manga"):
        forward = await _series(HONZUKI_XML, HONZUKI, kind)
        backward = await _series(_reverse_items(HONZUKI_XML), HONZUKI, kind)
        assert forward == backward


@pytest.mark.asyncio
async def test_sorts_by_date_before_keeping_thirty():
    volumes = list(range(1, 41))
    random.Random(7).shuffle(volumes)
    items = [
        _item(f"{HONZUKI} : 司書になるためには手段を選んでいられません",
              _isbn(f"97848685{n:04d}"), f"{2023 + n // 12}.{n % 12 + 1}",
              ndc="913.6", volume=f"[{n}]")
        for n in volumes
    ]
    xml = _inject(b"<rss xmlns:dc='http://purl.org/dc/elements/1.1/' "
                  b"xmlns:dcndl='http://ndl.go.jp/dcndl/terms/' "
                  b"xmlns:dcterms='http://purl.org/dc/terms/' "
                  b"xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'>"
                  b"<channel></channel></rss>", *items)
    dto = await _series(xml, HONZUKI, "novel")

    assert dto.lines[0].count == 40
    assert len(dto.releases) == 30
    assert [r.label for r in dto.releases] == [str(n) for n in range(40, 10, -1)]  # 目録の [40] も数字に揃える


@pytest.mark.asyncio
async def test_upcoming_records_without_series_name_join_their_label_line():
    title = "架空の魔法学園"
    xml = _inject(
        b"<rss xmlns:dc='http://purl.org/dc/elements/1.1/' "
        b"xmlns:dcndl='http://ndl.go.jp/dcndl/terms/' "
        b"xmlns:dcterms='http://purl.org/dc/terms/' "
        b"xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'><channel></channel></rss>",
        _item(title, _isbn("978404000030"), "2025.3", ndc="913.6", volume="30",
              series="電撃文庫 ; か-1-30", publisher="KADOKAWA"),
        _item(title, _isbn("978404000031"), "2025.9", ndc="913.6", volume="31",
              series="電撃文庫 ; か-1-31", publisher="KADOKAWA"),
        _item(f"{title}　32", _isbn("978404000032"), "2026-11-10", publisher="ＫＡＤＯＫＡＷＡ"),
        _item(f"{title} 外伝 3 (電撃文庫)", _isbn("978404000033"), "2026-12-10",
              publisher="ＫＡＤＯＫＡＷＡ"),
    )
    dto = await _series(xml, title, "novel")

    assert dto.line == "電撃文庫"
    assert [(l.key, l.count) for l in dto.lines] == [("電撃文庫", 4)]
    assert [r.label for r in dto.releases] == ["外伝 3", "32", "31", "30"]


# --- 転生したらスライムだった件 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_slime_novel_prefers_the_general_line_over_childrens_bunko():
    dto = await _series(SLIME_XML, SLIME, "novel")

    assert dto.line == "GC NOVELS"
    lines = {l.key: l.count for l in dto.lines}
    assert lines == {"GC NOVELS": 3, "かなで文庫": 18}
    assert [r.label for r in dto.releases] == ["番外編 とある休暇の過ごし方", "23", "22"]


@pytest.mark.asyncio
async def test_slime_childrens_bunko_line_includes_its_upcoming_record():
    dto = await _series(SLIME_XML, SLIME, "novel", line="かなで文庫")

    first = dto.releases[0]
    assert (first.externalId, first.label, first.date, first.precision) == (
        "9784825000674",
        "とある時空の物語17 (上)",
        "2026-11-22",
        "day",
    )


@pytest.mark.asyncio
async def test_slime_manga_is_comics_only_with_readable_spinoff_labels():
    dto = await _series(SLIME_XML, SLIME, "manga")

    assert dto.line == "シリウスKC"
    assert all(r.externalId.startswith("978406") for r in dto.releases)  # 講談社 comics
    labels = {r.externalId: r.label for r in dto.releases}
    assert labels["9784065444528"] == "異聞 魔国暮らしのトリニティ 14"
    assert labels["9784065444443"] == "クレイマンREVENGE 9"
    # Main volumes NDL lists without a series name join the シリウスKC line.
    assert labels["9784065437544"] == "32"
    assert labels["9784065423844"] == "31"
    # 特装版 (edition) and the ミニクリアファイル付き 近刊 are excluded.
    assert "9784065439203" not in labels
    assert "9784065454350" not in labels
    _assert_newest_first(dto)


# --- older payload ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_payload_drops_cds_ranges_and_ranking_books():
    calls = Calls()
    dto = await _series(LEGACY_XML, HONZUKI, "novel", calls=calls)

    assert [(r.externalId, r.date, r.precision) for r in dto.releases] == [
        ("9784864723473", "2015-03-01", "month"),
        ("9784864723428", "2015-02-01", "month"),
    ]
    assert [r.label for r in dto.releases] == ["第1部[2] (兵士の娘 2)", "第1部[1] (兵士の娘 1)"]
    assert calls.openbd == []  # nothing near today, no openBD request


# --- cache / errors ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_is_reused_and_refresh_bypasses_it(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    calls = Calls()
    async with _client(HONZUKI_XML, calls) as client:
        await ndl.series(HONZUKI, "novel", client=client, cache=cache, today=TODAY)
        await ndl.series(HONZUKI, "manga", client=client, cache=cache, today=TODAY)
        await ndl.search_books(HONZUKI, client=client, cache=cache, today=TODAY)
        assert len(calls.ndl) == 1
        await ndl.series(HONZUKI, "novel", refresh=True, client=client, cache=cache, today=TODAY)
        assert len(calls.ndl) == 2


@pytest.mark.asyncio
async def test_series_raises_source_unavailable_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable):
            await ndl.series(HONZUKI, "novel", client=client, cache=None, today=TODAY)


@pytest.mark.asyncio
async def test_broken_payload_is_not_cached(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    async with _client(b"<html>maintenance", Calls()) as client:
        with pytest.raises(SourceUnavailable):
            await ndl.series(HONZUKI, "novel", client=client, cache=cache, today=TODAY)
    async with _client(HONZUKI_XML, Calls()) as client:
        dto = await ndl.series(HONZUKI, "novel", client=client, cache=cache, today=TODAY)
    assert dto.releases


@pytest.mark.asyncio
async def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        await ndl.series(HONZUKI, "anime", cache=None, today=TODAY)  # type: ignore[arg-type]


# --- search_books ---------------------------------------------------------------------------------


async def _search(xml: bytes, q: str):
    async with _client(xml) as client:
        return await ndl.search_books(q, client=client, cache=None, today=TODAY)


@pytest.mark.asyncio
async def test_search_books_returns_one_result_per_series_line():
    results = await _search(HONZUKI_XML, HONZUKI)

    assert [(r.mediaType, r.subtitle) for r in results] == [
        ("manga", "TOブックス・第四部「貴族院の図書館を救いたい!13」・2026年12月"),
        ("light-novel", "TOブックス・TOジュニア文庫・第三部 領主の養女10・2026年10月"),
        ("light-novel", "TOブックス・ハンネローレの貴族院五年生 3・2026年5月"),
    ]
    assert [r.externalId for r in results] == [
        f"manga::{HONZUKI}",
        f"novel:TOジュニア文庫:{HONZUKI}",
        f"novel::{HONZUKI}",
    ]
    assert len({r.externalId for r in results}) == len(results)
    for result in results:
        assert result.source == "ndl"
        assert result.title == HONZUKI
        assert result.url.startswith("https://ndlsearch.ndl.go.jp/search?")
        assert result.direct is False


@pytest.mark.asyncio
async def test_search_books_names_the_series_not_a_spinoff():
    results = await _search(SLIME_XML, SLIME)
    by_subtitle = {r.subtitle.split("・")[1]: r for r in results}

    assert by_subtitle["シリウスKC"].title == SLIME
    assert by_subtitle["シリウスKC"].mediaType == "manga"
    assert by_subtitle["GC NOVELS"].title == SLIME
    assert by_subtitle["GC NOVELS"].mediaType == "light-novel"
    assert len(results) == len({(r.mediaType, r.subtitle.split("・")[1]) for r in results})


@pytest.mark.asyncio
async def test_search_books_blank_query_makes_no_request():
    assert await ndl.search_books("  ", client=None, cache=None, today=TODAY) == []


# --- /api/ndl/series route ---------------------------------------------------------------------------


def _route_client(monkeypatch, fake_series) -> TestClient:
    monkeypatch.setattr(ndl_api.ndl, "series", fake_series)
    app = FastAPI()
    app.include_router(ndl_api.router)
    return TestClient(app)


def test_route_passes_kind_line_and_refresh(monkeypatch):
    seen: list[tuple] = []

    async def fake_series(title, kind, line=None, refresh=False, **_):
        seen.append((title, kind, line, refresh))
        return NdlSeriesDTO(line=line or "")

    client = _route_client(monkeypatch, fake_series)
    assert client.get("/api/ndl/series", params={"title": HONZUKI, "kind": "manga"}).status_code == 200
    client.get("/api/ndl/series", params={"title": HONZUKI, "kind": "novel", "line": "", "refresh": "1"})
    client.get("/api/ndl/series", params={"title": HONZUKI, "line": "TOジュニア文庫"})

    assert seen == [
        (HONZUKI, "manga", None, False),
        (HONZUKI, "novel", "", True),
        (HONZUKI, "novel", "TOジュニア文庫", False),
    ]
    assert client.get("/api/ndl/series", params={"title": HONZUKI, "kind": "anime"}).status_code == 422


def test_route_records_adapter_error_before_502(monkeypatch):
    async def fake_series(*args, **kwargs):
        raise SourceUnavailable("ndl", "boom")

    monkeypatch.setattr(errors, "last_adapter_errors", {})
    client = _route_client(monkeypatch, fake_series)
    response = client.get("/api/ndl/series", params={"title": HONZUKI, "kind": "novel"})

    assert response.status_code == 502
    assert errors.last_adapter_errors["ndl"].message == "boom"


# --- 目録前の近刊: JPRO rows of the catalog (backend.catalog.upcoming) ---------------------------------

HONZUKI_NEW = _isbn("978486854999")  # not in the title search
GOGO = "本好きの下剋上~司書になるためには手段を選んでいられません~第五部 女神の化身13"


def _upcoming(isbn: str, title: str, kind: str, day: str, **fields) -> "ndl.UpcomingRecord":
    fields.setdefault("publisher", "TOブックス")
    record = ndl.NdlRecord(
        title=title, isbn=isbn, categories=("図書",), issued=day, date=day, precision="day", **fields
    )
    return ndl.UpcomingRecord(record=record, kind=kind)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_catalog_upcoming_volume_joins_the_line_it_belongs_to():
    upcoming = [_upcoming(HONZUKI_NEW, GOGO, "novel", "2027-03-10")]

    dto = await _series(HONZUKI_XML, HONZUKI, "novel", upcoming=upcoming)

    assert dto.line == ""  # still 本編 by default
    assert {l.key: l.count for l in dto.lines} == {"": 6, "TOジュニア文庫": 9}  # one more 本編 book
    first = dto.releases[0]
    assert (first.externalId, first.date, first.precision, first.source) == (HONZUKI_NEW, "2027-03-10", "day", "ndl")
    assert first.label.endswith("女神の化身13")
    pinned = await _series(HONZUKI_XML, HONZUKI, "novel", line="", upcoming=upcoming)  # a synced edition's ndlLine
    assert pinned.releases[0].externalId == HONZUKI_NEW
    junior = await _series(HONZUKI_XML, HONZUKI, "novel", line="TOジュニア文庫", upcoming=upcoming)
    assert HONZUKI_NEW not in _isbns(junior)
    manga = await _series(HONZUKI_XML, HONZUKI, "manga", upcoming=upcoming)
    assert HONZUKI_NEW not in _isbns(manga)


@pytest.mark.asyncio
async def test_a_labelled_catalog_volume_makes_or_joins_its_labels_line():
    upcoming = [_upcoming(HONZUKI_NEW, "本好きの下剋上 第三部 領主の養女11", "novel", "2027-01-08", series_title="TOジュニア文庫")]
    dto = await _series(HONZUKI_XML, HONZUKI, "novel", line="TOジュニア文庫", upcoming=upcoming)
    assert dto.releases[0].externalId == HONZUKI_NEW
    assert len(dto.releases) == 10


@pytest.mark.asyncio
async def test_the_catalog_kind_wins_over_the_guess_for_a_record_without_ndc():
    # A 近刊 record in the title search with no NDC and no comic word: the adapter guesses "novel".
    odd = _isbn("978486854998")
    xml = _inject(HONZUKI_XML, _item("本好きの下剋上 ~新しい物語~ 1", odd, "2026-12-01"))
    guessed = await _series(xml, HONZUKI, "novel", line="")
    assert odd in _isbns(guessed)

    upcoming = [_upcoming(odd, "本好きの下剋上 ~新しい物語~ 1", "manga", "2026-12-01")]  # JPRO: コミックス・劇画
    novel = await _series(xml, HONZUKI, "novel", line="", upcoming=upcoming)
    manga = await _series(xml, HONZUKI, "manga", line="", upcoming=upcoming)
    assert odd not in _isbns(novel)
    assert odd in _isbns(manga)


@pytest.mark.asyncio
async def test_a_catalogued_record_keeps_its_ndc_kind():
    upcoming = [_upcoming(MANGA_3_10, "本好きの下剋上 第3部[10]", "novel", "2026-08-25")]  # a wrong hint
    manga = await _series(HONZUKI_XML, HONZUKI, "manga", upcoming=upcoming)
    assert MANGA_3_10 in _isbns(manga)


@pytest.mark.asyncio
async def test_a_volume_in_both_the_title_search_and_the_catalog_is_one_release():
    upcoming = [_upcoming(MANGA_UPCOMING_4_13, "本好きの下剋上 別の題 13", "manga", "2026-12-25")]

    dto = await _series(HONZUKI_XML, HONZUKI, "manga", upcoming=upcoming)

    assert _isbns(dto).count(MANGA_UPCOMING_4_13) == 1
    release = _by_isbn(dto, MANGA_UPCOMING_4_13)
    assert (release.label, release.date) == ("第四部「貴族院の図書館を救いたい!13」", "2026-12-10")  # the search's own


@pytest.mark.asyncio
async def test_catalog_volumes_of_other_titles_or_dropped_kinds_are_ignored():
    upcoming = [
        _upcoming(_isbn("978486854997"), "転生したらスライムだった件 30", "novel", "2027-01-01"),
        _upcoming(_isbn("978486854996"), "本好きの下剋上 公式ファンブック 10", "novel", "2027-01-01"),
        _upcoming(_isbn("978123456789"), "本好きの下剋上 英語版 1", "novel", "2027-01-01"),
    ]
    dto = await _series(HONZUKI_XML, HONZUKI, "novel", line="", upcoming=upcoming)
    before = await _series(HONZUKI_XML, HONZUKI, "novel", line="", upcoming=None)
    assert _isbns(dto) == _isbns(before)


@pytest.mark.asyncio
async def test_series_reads_the_jpro_rows_of_the_catalog_by_default(tmp_path, monkeypatch):
    from backend.catalog import db as catalog_db

    path = tmp_path / "catalog.db"
    monkeypatch.setenv(catalog_db.DB_ENV_VAR, str(path))
    conn = catalog_db.connect(path)
    jpro_extra = {
        "isbn": HONZUKI_NEW, "volume": None, "series": "本好きの下剋上~司書になるためには手段を選んでいられません",
        "line": "", "publisher": "TOブックス", "ndc": "913.6", "kindHint": "novel", "via": "jpro",
        "jpro": {"title": GOGO, "volume": None, "seriesTitle": None, "labels": ["一般", "日本文学小説・物語"]},
    }
    catalogued = _isbn("978486854995")
    catalog_db.upsert_entries(conn, [
        catalog_db.Entry(source="ndl", external_id=HONZUKI_NEW, kind="book", title=GOGO, format="小説",
                         start_date="2027-03-10", start_precision="day", creators=["香月美夜"], extra=jpro_extra),
        catalog_db.Entry(source="ndl", external_id=catalogued, kind="book", title="本好きの下剋上 第5部[13]", format="小説",
                         start_date="2027-03-01", start_precision="month",
                         extra={"isbn": catalogued, "kindHint": "novel", "series": "本好きの下剋上", "line": ""}),
    ])
    conn.close()

    dto = await _series(HONZUKI_XML, HONZUKI, "novel", line="")

    assert dto.releases[0].externalId == HONZUKI_NEW
    assert catalogued not in _isbns(dto)  # catalogued rows come from the title search, not from here


@pytest.mark.asyncio
async def test_a_missing_catalog_changes_nothing():
    with_default = await _series(HONZUKI_XML, HONZUKI, "novel")  # conftest: TRIPTYCH_CATALOG_DB does not exist
    without = await _series(HONZUKI_XML, HONZUKI, "novel", upcoming=None)
    assert with_default == without


# --- 近刊 labels spelled differently from NDL's catalogue -------------------------------------------------

KC_PUBLISHER = "講談社"
KC_AUTHOR = ("講談, 太郎",)  # the same creator on every volume: the adapter's guess makes the 近刊 a comic


def _kc_xml(*extra: str) -> bytes:
    """Four catalogued volumes of a comic in NDL's 'ヤンマガKC' / 'モーニングKC' spelling."""
    catalogued = [
        _item(f"フーディーズ {n}", _isbn(f"97840650000{n}"), f"2026.{n}", ndc="726.1", volume=str(n),
              series="ヤンマガKC", publisher=KC_PUBLISHER, creators=KC_AUTHOR)
        for n in range(1, 5)
    ]
    return _inject(HONZUKI_XML.replace(b"<item>", b"<item>", 1), *catalogued, *extra)


@pytest.mark.parametrize(
    ("jpro_label", "line"),
    [
        ("ヤンマガKCスペシャル", "ヤンマガKC"),  # NDL catalogues the label shorter
        ("ヤンマガ KC", "ヤンマガKC"),  # spacing
        ("やんまがKC", "やんまがKC"),  # a different label stays apart
    ],
)
@pytest.mark.asyncio
async def test_a_jpro_label_variant_joins_the_catalogued_line(jpro_label, line):
    upcoming_isbn = _isbn("978406500005")
    xml = _kc_xml(_item("フーディーズ(5)", upcoming_isbn, "2026-11-19", series=jpro_label, publisher=KC_PUBLISHER,
                        creators=KC_AUTHOR))

    dto = await _series(xml, "フーディーズ", "manga", upcoming=None)

    assert {l.key for l in dto.lines} == ({"ヤンマガKC"} if line == "ヤンマガKC" else {"ヤンマガKC", line})
    pinned = await _series(xml, "フーディーズ", "manga", line="ヤンマガKC", upcoming=None)
    assert (upcoming_isbn in _isbns(pinned)) == (line == "ヤンマガKC")


@pytest.mark.asyncio
async def test_a_line_pinned_by_the_jpro_spelling_still_finds_its_volumes():
    upcoming_isbn = _isbn("978406500005")
    xml = _kc_xml(_item("フーディーズ(5)", upcoming_isbn, "2026-11-19", series="ヤンマガKCスペシャル", publisher=KC_PUBLISHER,
                        creators=KC_AUTHOR))

    dto = await _series(xml, "フーディーズ", "manga", line="ヤンマガKCスペシャル", upcoming=None)

    assert dto.line == "ヤンマガKCスペシャル"
    assert upcoming_isbn in _isbns(dto)  # an edition pinned before NDL catalogued the series


@pytest.mark.asyncio
async def test_a_label_variant_of_another_publisher_or_kind_stays_apart():
    other_publisher = _isbn("978406500006")
    novel = _isbn("978406500007")
    xml = _kc_xml(
        _item("フーディーズ(6)", other_publisher, "2026-12-19", series="ヤンマガKCスペシャル", publisher="別の出版社",
              creators=KC_AUTHOR),
        _item("フーディーズ 外伝", novel, "2026-12-01", series="ヤンマガKC文庫", publisher=KC_PUBLISHER),
    )
    dto = await _series(
        xml, "フーディーズ", "manga", line="ヤンマガKC",
        upcoming=[_upcoming(novel, "フーディーズ 外伝", "novel", "2026-12-01", series_title="ヤンマガKC文庫",
                            publisher=KC_PUBLISHER)],
    )
    assert other_publisher not in _isbns(dto)
    assert novel not in _isbns(dto)


@pytest.mark.asyncio
async def test_a_catalog_volume_with_the_jpro_label_joins_the_series_line():
    upcoming_isbn = _isbn("978406500005")
    upcoming = [_upcoming(upcoming_isbn, "フーディーズ(5)", "manga", "2026-11-19", series_title="ヤンマガKCスペシャル",
                          publisher=KC_PUBLISHER)]
    dto = await _series(_kc_xml(), "フーディーズ", "manga", line="ヤンマガKC", upcoming=upcoming)
    assert _isbns(dto)[0] == upcoming_isbn


@pytest.mark.parametrize(
    "remainder, volume, expected",
    [
        ("(5)", None, "5"),  # 近刊は題名に「金田一パパの事件簿(5)」と書く
        ("（12）", None, "12"),
        ("[3]", None, "3"),
        ("第3部[10]", None, "第3部[10]"),  # 括弧だけでなければそのまま
        ("", "(7)", "7"),
    ],
)
def test_label_unwraps_a_bare_bracketed_volume(remainder, volume, expected):
    record = ndl.NdlRecord(title="金田一パパの事件簿" + remainder, volume=volume)
    assert ndl._label(record, remainder, None) == expected
