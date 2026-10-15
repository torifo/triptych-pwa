"""Tests for the NDL catalog harvester (backend/catalog/harvest/ndl.py).

No live requests: httpx.MockTransport plus a fake clock. A small fake of the
OpenSearch endpoint enforces what the live API does (verified 2026-10-14): only
the first 500 records of a query are reachable (a window past it answers an empty
page with totalResults 0), `from` / `until` are YYYY-MM, `isbn` is a prefix match
and the records come back ordered by title.

Recorded fixtures (https://ndlsearch.ndl.go.jp/api/opensearch, 2026-10-14, the
<rdfs:seeAlso> link lists removed):
- ndl_opensearch_913.6_2026-10.xml: `ndc=913.6&mediatype=books&from=2026-10&until=2026-10&cnt=30`
  (totalResults 286: 30 records, six without an ISBN)
- ndl_opensearch_726.1_2026-10.xml: the same for `ndc=726.1` (totalResults 234;
  two アンソロジー books)
- ndl_title_honzuki.xml / ndl_title_tensura.xml: `title=<series>&mediatype=books&from=2025-10`,
  for series extraction: catalogued volumes with `dcndl:volume` ("第3部[10]", "14[上]"),
  ジュニア文庫 lines, 特装版 books and 近刊 records (no NDC, dated to the day, volume
  written in the title).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx
import pytest

from backend.adapters.ndl import NdlRecord
from backend.catalog import db, runtime
from backend.catalog.harvest import ndl

FIXTURES = Path(__file__).parent / "fixtures"
quiet = lambda message: None  # noqa: E731

NOVEL, MANGA = ndl.CLASSES


def recorded(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeClock:
    """Time only moves when someone sleeps, so pacing is observable and instant."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)  # 12:00 JST

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(seconds, 0.0)


# -- a tiny OpenSearch ---------------------------------------------------------------------------


def isbn13(body: str) -> str:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


def make_isbn(prefix: str, n: int) -> str:
    """A valid ISBN-13 starting with `prefix` (n picks the rest)."""
    spread = f"{n % 10}{n // 10:0>{12 - len(prefix) - 1}}"
    return isbn13(prefix + spread)


@dataclass
class Book:
    isbn: Optional[str]
    title: str
    ndc: str = "913.6"
    month: str = "2026-10"  # YYYY-MM, what `from` / `until` compare
    issued: Optional[str] = None  # raw dcterms:issued, default "2026.10"
    volume: Optional[str] = None
    series: Optional[str] = None
    publisher: str = "テスト出版"
    author: str = "著者, 太郎,著者太郎 著"
    categories: tuple[str, ...] = ("図書", "紙")
    has_ndc: bool = True
    reading: str = "テスト"

    @property
    def raw_issued(self) -> str:
        if self.issued:
            return self.issued
        year, month = self.month.split("-")
        return f"{year}.{int(month)}"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def item_xml(b: Book) -> str:
    parts = [
        f"<title>{esc(b.title)}</title>",
        f"<link>https://ndlsearch.ndl.go.jp/books/R100000002-I{b.isbn or 'x'}</link>",
        f"<author>{esc(b.author)}</author>",
        *[f"<category>{c}</category>" for c in b.categories],
        f"<dc:title>{esc(b.title)}</dc:title>",
        f"<dcndl:titleTranscription>{b.reading}</dcndl:titleTranscription>",
        "<dc:creator>著者, 太郎, 1970-</dc:creator>",
        f"<dc:publisher>{b.publisher}</dc:publisher>",
        f"<dcterms:issued>{b.raw_issued}</dcterms:issued>",
    ]
    if b.volume:
        parts.append(f"<dcndl:volume>{esc(b.volume)}</dcndl:volume>")
    if b.series:
        parts.append(f"<dcndl:seriesTitle>{esc(b.series)}</dcndl:seriesTitle>")
    if b.isbn:
        parts.append(f'<dc:identifier xsi:type="dcndl:ISBN">{b.isbn}</dc:identifier>')
    if b.has_ndc:
        parts.append(f'<dc:subject xsi:type="dcndl:NDC10">{b.ndc}</dc:subject>')
    return "<item>" + "".join(parts) + "</item>"


def page_xml(total: int, books: list[Book], idx: int = 1) -> bytes:
    return (
        '<rss xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:openSearch="http://a9.com/-/spec/opensearchrss/1.0/" '
        'xmlns:dcndl="http://ndl.go.jp/dcndl/terms/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.0"><channel>'
        f"<title>test</title><openSearch:totalResults>{total}</openSearch:totalResults>"
        f"<openSearch:startIndex>{idx}</openSearch:startIndex>"
        f"{''.join(item_xml(b) for b in books)}</channel></rss>"
    ).encode()


@dataclass
class Sent:
    at: float
    params: dict[str, str]
    user_agent: str


@dataclass
class FakeNdl:
    clock: FakeClock
    books: list[Book] = field(default_factory=list)
    sent: list[Sent] = field(default_factory=list)
    # called with the request params; returns a response / raises to replace the answer
    intercept: Optional[Callable[[dict[str, str]], Optional[httpx.Response]]] = None
    out_of_reach: int = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert request.url.host == "ndlsearch.ndl.go.jp" and request.url.path == "/api/opensearch"
        self.sent.append(Sent(self.clock.t, params, request.headers.get("user-agent", "")))
        if self.intercept is not None:
            replaced = self.intercept(params)
            if replaced is not None:
                return replaced

        cnt, idx = int(params["cnt"]), int(params["idx"])
        prefix = params.get("isbn", "").replace("-", "")
        found = [
            b
            for b in self.books
            if b.ndc.startswith(params["ndc"])
            and params["from"] <= b.month
            and ("until" not in params or b.month <= params["until"])
            and (b.isbn or "").startswith(prefix)
        ]
        found.sort(key=lambda b: (b.title, b.isbn or ""))
        if idx + cnt - 1 > ndl.REACH:  # what the live API does: nothing at all
            self.out_of_reach += 1
            return httpx.Response(200, content=page_xml(0, []))
        return httpx.Response(200, content=page_xml(len(found), found[idx - 1 : idx - 1 + cnt], idx))

    def slice_requests(self, ndc: str, start: str) -> list[dict[str, str]]:
        return [s.params for s in self.sent if s.params["ndc"] == ndc and s.params["from"] == start]


def too_many_requests() -> httpx.Response:
    return httpx.Response(
        429,
        content='<?xml version="1.0"?><error><code>429</code><message>Too Many Requests</message></error>'.encode(),
    )


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def server(clock) -> FakeNdl:
    return FakeNdl(clock)


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture()
async def client(server):
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as http:
        yield http


def rows(conn: sqlite3.Connection, where: str = "1") -> list[dict]:
    return [
        db.row_to_dict(r)
        for r in conn.execute(f"SELECT * FROM entries WHERE source = 'ndl' AND {where} ORDER BY id")
    ]


# -- slices ------------------------------------------------------------------------------------------


def test_slices_run_newest_first_from_the_tail_back_to_a_year_ago():
    slices = ndl.build_slices(date(2026, 10, 14))

    assert len(slices) == 2 + 19 * 2
    keys = [s.key for s in slices]
    assert keys[:2] == ["913.6:2027-05..", "726.1:2027-05.."]  # everything after the six months ahead
    assert keys[2:6] == ["913.6:2027-04", "726.1:2027-04", "913.6:2027-03", "726.1:2027-03"]
    assert keys[-2:] == ["913.6:2025-10", "726.1:2025-10"]  # one year back (the month of 2025-10)
    assert "913.6:2026-10" in keys and len(set(keys)) == len(keys)

    month = slices[4].params()  # 913.6:2027-03
    assert month == {"ndc": "913.6", "mediatype": "books", "from": "2027-03", "until": "2027-03", "cnt": 500, "idx": 1}
    tail = slices[0].params()
    assert "until" not in tail and tail["from"] == "2027-05"


def test_slices_cross_the_year_boundary():
    keys = [s.key for s in ndl.build_slices(date(2026, 1, 31)) if s.key.startswith("726.1")]
    assert keys[0] == "726.1:2026-08.."
    assert keys[1] == "726.1:2026-07" and keys[-1] == "726.1:2025-01"


def test_isbn_prefixes_split_ten_ways_down_to_a_whole_isbn():
    assert ndl.children("") == [f"9784{d}" for d in "0123456789"]
    assert ndl.children("97840") == [f"97840{d}" for d in "0123456789"]
    assert ndl.children("978406123456") == [f"978406123456{d}" for d in "0123456789"]
    assert ndl.children("9784061234567") == []


# -- recorded pages --------------------------------------------------------------------------------------


def test_recorded_novel_page_is_parsed_and_filtered():
    total, items = ndl.parse_page(recorded("ndl_opensearch_913.6_2026-10.xml"))
    assert (total, len(items)) == (286, 30)

    entries = ndl.entries_of(items, NOVEL)

    assert len(entries) == 24  # six of the 30 have no ISBN
    first = entries[0]
    assert first.id == "ndl:9784286281940"
    assert (first.source, first.kind, first.format) == ("ndl", "book", "小説")
    assert first.title == "アイアンレオの川"
    assert first.title_reading == "アイアンレオ ノ カワ"
    assert (first.start_date, first.start_precision) == ("2026-10-01", "month")
    assert first.creators == ["高原左京"]
    assert first.url and first.url.startswith("https://ndlsearch.ndl.go.jp/books/R100000002-I")
    assert first.extra == {
        "isbn": "9784286281940",
        "volume": None,
        "series": "アイアンレオの川",
        "line": "文芸社セレクション",
        "publisher": "文芸社",
        "ndc": "913.6",
        "kindHint": "novel",
    }
    volume = next(e for e in entries if e.external_id == "9784803023817")
    assert volume.title == "明日もいい日でありますように。 : 異世界で新しい家族ができました 5"
    assert volume.extra["series"] == "明日もいい日でありますように。 : 異世界で新しい家族ができました"
    assert volume.extra["volume"] == "5"


def test_recorded_manga_page_drops_anthologies_and_keeps_the_class():
    total, items = ndl.parse_page(recorded("ndl_opensearch_726.1_2026-10.xml"))
    assert (total, len(items)) == (234, 30)

    entries = ndl.entries_of(items, MANGA)

    titles = [e.title for e in entries]
    assert len(entries) == 28
    assert not [t for t in titles if "アンソロジー" in t]
    assert {e.format for e in entries} == {"漫画"}
    assert {e.extra["kindHint"] for e in entries} == {"manga"}
    assert {e.extra["ndc"] for e in entries} == {"726.1"}
    aoba = next(e for e in entries if e.external_id == "9784785982942")
    assert (aoba.title, aoba.extra["series"], aoba.extra["volume"], aoba.extra["line"]) == (
        "アオバ自転車店といこうよ! 17", "アオバ自転車店といこうよ!", "17", "YKコミックス",
    )


def test_honzuki_volumes_share_one_series_and_keep_their_line():
    _, items = ndl.parse_page(recorded("ndl_title_honzuki.xml"))
    entries = {e.external_id: e for e in ndl.entries_of(items, NOVEL)}

    junior = entries["9784868541233"]  # TOジュニア文庫, 第3部[10]
    assert junior.title == "本好きの下剋上 第3部[10]"
    assert junior.extra["series"] == "本好きの下剋上"
    assert junior.extra["volume"] == "第3部[10]"
    assert junior.extra["line"] == "TOジュニア文庫"

    manga = entries["9784867949450"]  # the comic: the subtitle is part of the work's name
    assert manga.extra["series"] == "本好きの下剋上 : 司書になるためには手段を選んでいられません"
    assert manga.extra["volume"] == "第3部[10]"
    assert manga.extra["line"] == ""

    upcoming = entries["9784868542186"]  # 近刊: no NDC, dated to the day, the volume is in the title
    assert (upcoming.start_date, upcoming.start_precision) == ("2026-12-10", "day")
    assert upcoming.extra["series"] == "本好きの下剋上~司書になるためには手段を選んでいられません"
    assert upcoming.extra["volume"] == "4-13"
    assert upcoming.extra["ndc"] == "913.6"  # no NDC of its own: the query's

    # Spin-offs are their own series (and the linker, which ignores punctuation, joins
    # "...下剋上 : 司書..." and "...下剋上~司書..." again).
    assert entries["9784867949269"].extra["series"] == "本好きの下剋上 : ハンネローレの貴族院五年生"
    assert entries["9784868541653"].extra["series"] == "本好きの下剋上 ~ハンネローレの貴族院五年生"
    assert {e.extra["series"] for e in entries.values()} == {
        "本好きの下剋上",
        "本好きの下剋上 : 司書になるためには手段を選んでいられません",
        "本好きの下剋上~司書になるためには手段を選んでいられません",
        "本好きの下剋上~司書になるためには手段を選んでいられません~フェルディナンドの館にて",
        "本好きの下剋上 : ハンネローレの貴族院五年生",
        "本好きの下剋上 ~ハンネローレの貴族院五年生",
        "本好きの下剋上~ハンネローレの貴族院五年生~恋してみたいお姫様",
    }


def test_tensura_series_names_lines_and_exclusions():
    _, items = ndl.parse_page(recorded("ndl_title_tensura.xml"))
    entries = {e.external_id: e for e in ndl.entries_of(items, NOVEL)}

    assert len(items) == 23 and len(entries) == 19  # four 特装版 (edition) / 付き records are dropped
    assert "9784065454350" not in entries  # ミニクリアファイル付き ... 特装版 (近刊)
    assert "9784065415146" not in entries  # 特装版

    gc = entries["9784867168738"]
    assert (gc.title, gc.extra["series"], gc.extra["volume"], gc.extra["line"]) == (
        "転生したらスライムだった件 23", "転生したらスライムだった件", "23", "GC NOVELS",
    )
    split = entries["9784867168653"]  # 14[上]
    assert (split.extra["series"], split.extra["volume"], split.extra["line"]) == (
        "転生したらスライムだった件", "14[上]", "かなで文庫",
    )
    upcoming = entries["9784825000674"]  # 近刊: "物語⑰　（上）" in the title
    assert upcoming.title == "転生したらスライムだった件 とある時空の物語17 (上)"
    assert upcoming.extra["series"] == "転生したらスライムだった件 とある時空の物語"
    assert upcoming.extra["volume"] == "17 (上)"
    assert (upcoming.start_date, upcoming.start_precision) == ("2026-11-22", "day")
    assert upcoming.extra["line"] == "かなで文庫"
    spin = entries["9784065413371"]
    assert spin.extra["series"] == "転生したらスライムだった件異聞 : 魔国暮らしのトリニティ"
    # The comics and the novels of one title share a series name, told apart by the class.
    assert entries["9784065423844"].extra["series"] == "転生したらスライムだった件"


@pytest.mark.parametrize(
    ("title", "volume", "in_title", "series"),
    [
        ("悪役令嬢はしゃべりません", "5", False, "悪役令嬢はしゃべりません"),
        ("本好きの下剋上 : 司書になるためには手段を選んでいられません", "第3部[10]", False,
         "本好きの下剋上 : 司書になるためには手段を選んでいられません"),
        ("本好きの下剋上 第3部[10]", "第3部[10]", False, "本好きの下剋上"),  # the volume copied into the title
        ("悪霊島 : 人見十吉秘境小説集成 2", None, False, "悪霊島 : 人見十吉秘境小説集成"),
        ("本好きの下剋上~司書になるためには手段を選んでいられません~第五部 女神の化身12", None, True,
         "本好きの下剋上~司書になるためには手段を選んでいられません"),
        ("本好きの下剋上~司書になるためには手段を選んでいられません~第四部「貴族院の図書館を救いたい!13」", "4-13", True,
         "本好きの下剋上~司書になるためには手段を選んでいられません"),
        ("本好きの下剋上 ~ハンネローレの貴族院五年生~ 「恋してみたいお姫様 2」", "2", True,
         "本好きの下剋上 ~ハンネローレの貴族院五年生"),
        ("転生したらスライムだった件 とある時空の物語17 (上)", None, True, "転生したらスライムだった件 とある時空の物語"),
        ("Re:ゼロから始める異世界生活 第3巻", None, False, "Re:ゼロから始める異世界生活"),
        ("86 : エイティシックス Ep.5", None, False, "86 : エイティシックス"),
        ("よふかしのうた vol.3", None, False, "よふかしのうた"),
        ("ブルーロック (30)", None, False, "ブルーロック"),
        ("ワールドトリガー27", None, False, "ワールドトリガー"),
        ("ONE PIECE 100", None, False, "ONE PIECE"),
        ("K2", None, False, "K2"),  # too short to lose its 2
        ("1984", None, False, "1984"),
        ("2001年宇宙の旅", None, False, "2001年宇宙の旅"),
        ("朝日のあたる病院", None, False, "朝日のあたる病院"),
    ],
)
def test_series_name(title, volume, in_title, series):
    assert ndl.series_name(title, volume, in_title=in_title) == series


def test_display_title_adds_the_volume_unless_the_title_has_it():
    assert ndl.display_title("悪役令嬢はしゃべりません", "5") == "悪役令嬢はしゃべりません 5"
    assert ndl.display_title("悪役令嬢はしゃべりません", None) == "悪役令嬢はしゃべりません"
    assert ndl.display_title("物語17 (上)", "17 (上)") == "物語17 (上)"
    assert ndl.display_title("物語 17", "17") == "物語 17"
    assert ndl.display_title("貴族院の図書館を救いたい!13」", "4-13") == "貴族院の図書館を救いたい!13」"
    assert ndl.display_title("相棒", "season24上") == "相棒 season24上"


def test_creators_drop_roles_and_headings():
    def record(author, creators=()):
        return NdlRecord(title="t", author=author, creators=tuple(creators))

    assert ndl.creators_of(record("織守, きょうや, 1980-,織守きょうや 著")) == ["織守きょうや"]
    assert ndl.creators_of(record("香山, 滋, 1909-1975,日下, 三蔵, 1968-,香山滋 著,日下三蔵 編")) == ["香山滋", "日下三蔵"]
    assert ndl.creators_of(record("香月, 美夜,鈴華,香月美夜 原作,椎名優 イラスト原案")) == ["香月美夜", "椎名優"]
    assert ndl.creators_of(record("浜, 千鳥,浜千鳥 [著]")) == ["浜千鳥"]
    assert ndl.creators_of(record("太宰, 治,Mark, Gibeau,OsamuDazai 著 ; MarkGibeau 訳")) == ["OsamuDazai", "MarkGibeau"]
    # No piece with a role: fall back to dc:creator.
    assert ndl.creators_of(record("嘉悦, 秀明, 1961-,H. Kaetsu", ["嘉悦秀明"])) == ["嘉悦秀明"]
    assert ndl.creators_of(record(None, ["a", "a", "b"])) == ["a", "b"]
    assert ndl.creators_of(record(None)) == []


def test_error_bodies_and_broken_pages_raise():
    with pytest.raises(ndl.NdlError, match="429"):
        ndl.parse_page(too_many_requests().content)
    with pytest.raises(ndl.NdlError, match="invalid XML"):
        ndl.parse_page(b"<rss><channel>")
    with pytest.raises(ndl.NdlError, match="no <channel>"):
        ndl.parse_page(b"<rss></rss>")
    with pytest.raises(ndl.NdlError, match="totalResults"):
        ndl.parse_page(b"<rss><channel><title>x</title></channel></rss>")


# -- filtering -----------------------------------------------------------------------------------------------


def xml_items(*books: Book) -> list[ndl.NdlItem]:
    return ndl.parse_page(page_xml(len(books), list(books)))[1]


def test_records_that_are_not_japanese_paper_volumes_are_dropped():
    good = Book(make_isbn("9784100", 1), "良い本")
    books = [
        good,
        Book(make_isbn("9781234", 2), "foreign ISBN"),
        Book(None, "no ISBN"),
        Book(make_isbn("9784100", 3), "電子書籍", categories=("図書", "電子")),
        Book(make_isbn("9784100", 4), "範囲の日付", issued="2018.1-2020.1"),
        Book(make_isbn("9784100", 5), "日付なし", issued="不明"),
        Book(make_isbn("9784100", 6), "アンソロジー集"),
        Book(make_isbn("9784100", 7), "ファンブック"),
        Book(make_isbn("9784100", 8), "ふぁんぶっく"),
        Book(make_isbn("9784100", 9), "画集 1"),
        Book(make_isbn("9784100", 10), "ドラマCD付き 2"),
        Book(make_isbn("9784100", 11), "付き合うことになりました"),  # 付き合う is a word, not an extra
        Book(make_isbn("9784100", 12), "特装版", volume="3"),
    ]
    kept = [e.title for e in ndl.entries_of(xml_items(*books), NOVEL)]
    assert kept == ["良い本", "付き合うことになりました"]


def test_the_same_isbn_twice_becomes_one_row_with_the_finer_date():
    isbn = make_isbn("9784100", 1)
    published = Book(isbn, "二重の本", issued="2026.10")
    upcoming = Book(isbn, "二重の本", issued="2026-10-23", has_ndc=False, volume="2")

    [entry] = ndl.entries_of(xml_items(published, upcoming), NOVEL)

    assert (entry.start_date, entry.start_precision) == ("2026-10-23", "day")
    assert entry.extra["ndc"] == "913.6"  # the catalogued record's
    assert entry.extra["volume"] == "2"  # a missing field is filled from the other


# -- harvest: storage and requests ------------------------------------------------------------------------------


async def test_small_slices_are_one_request_each_at_one_per_second(conn, client, clock, server):
    server.books = [
        Book(make_isbn("9784100", 1), "小説 A", month="2026-10"),
        Book(make_isbn("9784100", 2), "小説 B", month="2026-10", volume="2", series="テスト文庫 ; て-1-2"),
        Book(make_isbn("9784100", 3), "小説 C", month="2026-09"),
        Book(make_isbn("9784200", 4), "漫画 A", ndc="726.1", month="2026-10"),
        Book(make_isbn("9784300", 5), "遠い未来", month="2027-08"),  # the tail slice
    ]

    result = await ndl.harvest(conn, client, clock=clock, log=quiet)

    assert (result.source, result.fetched, result.slices_error) == ("ndl", 5, 0)
    assert result.slices_done == 40 and result.slices_skipped == 0
    assert len(server.sent) == 40  # nobody is over 500: one request per slice, no ISBN cut
    assert all("isbn" not in s.params for s in server.sent)
    assert {s.user_agent for s in server.sent} == {runtime.USER_AGENT}
    gaps = [b.at - a.at for a, b in zip(server.sent, server.sent[1:])]
    assert all(gap >= 1.0 for gap in gaps)  # 1 req/s

    sample = server.sent[-1].params  # the oldest slice
    assert sample == {
        "ndc": "726.1", "mediatype": "books", "from": "2025-10", "until": "2025-10", "cnt": "500", "idx": "1",
    }
    tail = [s.params for s in server.sent if "until" not in s.params]
    assert [(p["ndc"], p["from"]) for p in tail] == [("913.6", "2027-05"), ("726.1", "2027-05")]
    assert [s.params["from"] for s in server.sent[:4]] == ["2027-05", "2027-05", "2027-04", "2027-04"]  # newest first

    stored = rows(conn)
    assert [r["id"] for r in stored] == [f"ndl:{b.isbn}" for b in sorted(server.books, key=lambda b: b.isbn)]
    b_row = next(r for r in stored if r["title"].startswith("小説 B"))
    assert (b_row["kind"], b_row["format"], b_row["title"]) == ("book", "小説", "小説 B 2")
    assert (b_row["start_date"], b_row["start_precision"]) == ("2026-10-01", "month")
    assert b_row["creators"] == ["著者太郎"]
    assert b_row["url"].startswith("https://ndlsearch.ndl.go.jp/books/")
    assert b_row["extra"]["line"] == "テスト文庫" and b_row["extra"]["series"] == "小説 B"
    assert next(r for r in stored if r["title"] == "漫画 A")["format"] == "漫画"
    state = db.slice_status(conn, "ndl", "913.6:2026-10")
    assert (state["status"], state["fetched"]) == ("done", 2)


async def test_a_slice_over_500_is_cut_by_isbn_prefix(conn, client, clock, server):
    # 1200 novels in 2026-09, spread evenly over 97840 .. 97849.
    server.books = [Book(make_isbn(f"9784{n % 10}", n), f"本 {n:04d}", month="2026-09") for n in range(1200)]

    result = await ndl.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_error == 0 and result.fetched == 1200
    assert server.out_of_reach == 0  # no query ever reached past the 500th record
    asked = server.slice_requests("913.6", "2026-09")
    assert "isbn" not in asked[0]  # the whole month first: 1200 > 500
    assert [p["isbn"] for p in asked[1:]] == [f"9784{d}" for d in "0123456789"]
    assert len(asked) == 11
    assert len(rows(conn)) == 1200
    assert db.slice_status(conn, "ndl", "913.6:2026-09")["fetched"] == 1200


async def test_a_prefix_still_over_500_is_cut_again(conn, client, clock, server):
    big = [Book(make_isbn("978406", n), f"講談 {n:04d}", month="2026-08") for n in range(700)]
    small = [Book(make_isbn("978404", n), f"角川 {n:04d}", month="2026-08") for n in range(60)]
    other = [Book(make_isbn("978480", n), f"他 {n:04d}", month="2026-08") for n in range(500)]
    server.books = big + small + other  # 1260 -> 97840: 760 (over) -> 978406: 700 (over)

    result = await ndl.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_error == 0 and result.fetched == 1260
    assert server.out_of_reach == 0
    isbns = [p.get("isbn") for p in server.slice_requests("913.6", "2026-08")]
    assert isbns == [
        None,  # the month: 1260 records
        "97840",  # 760 (over): cut into its ten children, depth first
        *[f"97840{d}" for d in range(7)],  # the last of them, 978406, holds 700 (over): cut again
        *[f"978406{d}" for d in range(10)],
        "978407", "978408", "978409",
        *[f"9784{d}" for d in range(1, 10)],  # 97848 holds exactly 500: no further cut
    ]
    assert len(rows(conn)) == 1260


async def test_non_japanese_isbn_groups_are_never_asked_for(conn, client, clock, server):
    server.books = [Book(make_isbn(f"9784{n % 10}", n), f"本 {n:04d}", month="2026-09") for n in range(600)]
    await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert all(p.get("isbn", "9784").startswith("9784") for p in (s.params for s in server.sent))


async def test_rerun_skips_done_slices_and_fresh_refetches_without_duplicates(conn, client, clock, server):
    server.books = [Book(make_isbn("9784100", n), f"小説 {n}", month="2026-10") for n in range(5)]
    await ndl.harvest(conn, client, clock=clock, log=quiet)
    first_requests = len(server.sent)

    again = await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert (again.fetched, again.slices_done, again.slices_skipped) == (0, 0, 40)
    assert len(server.sent) == first_requests  # nothing asked

    db.reset_source(conn, "ndl")
    fresh = await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert (fresh.fetched, fresh.slices_done, fresh.slices_skipped) == (5, 40, 0)
    assert len(rows(conn)) == 5


async def test_limit_stops_the_run_and_leaves_the_slice_open(conn, client, clock, server):
    server.books = [
        *[Book(make_isbn("9784100", n), f"小説 {n}", month="2026-10") for n in range(3)],
        *[Book(make_isbn("9784200", n), f"漫画 {n}", ndc="726.1", month="2026-10") for n in range(3)],
        Book(make_isbn("9784100", 90), "古い小説", month="2026-09"),
    ]

    result = await ndl.harvest(conn, client, clock=clock, limit=4, log=quiet)

    assert result.fetched == 4
    assert len(rows(conn)) == 4
    assert not any(s.params["from"] <= "2026-09" for s in server.sent)  # older slices were never asked
    assert db.is_slice_done(conn, "ndl", "913.6:2026-10")
    state = db.slice_status(conn, "ndl", "726.1:2026-10")
    assert (state["status"], state["fetched"], state["message"]) == ("pending", 1, "limit")

    full = await ndl.harvest(conn, client, clock=clock, log=quiet)  # later run: everything
    assert full.slices_error == 0 and db.is_slice_done(conn, "ndl", "726.1:2026-10")
    assert len(rows(conn)) == 7
    assert full.slices_skipped == 15  # two tails and every month from 2027-04 on, bar 726.1:2026-10


async def test_old_slices_resume_after_an_interrupted_run(conn, client, clock, server):
    server.books = [
        Book(make_isbn("9784100", 1), "十月", month="2026-10"),
        Book(make_isbn("9784100", 2), "九月", month="2026-09"),
        Book(make_isbn("9784100", 3), "八月", month="2026-08"),
    ]
    boom = {"on": True}

    def fail_september(params):
        if boom["on"] and params["from"] == "2026-09" and params["ndc"] == "913.6":
            return httpx.Response(400, content=b"bad request")

    server.intercept = fail_september

    first = await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert (first.slices_error, first.slices_done) == (1, 39)
    assert len(first.errors) == 1 and "HTTP 400" in first.errors[0]
    state = db.slice_status(conn, "ndl", "913.6:2026-09")
    assert state["status"] == "error" and "HTTP 400" in state["message"]
    assert len(rows(conn)) == 2  # 十月 and 八月 are there; the walk went on past the failure

    boom["on"] = False
    server.sent.clear()
    second = await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert (second.slices_done, second.slices_skipped, second.slices_error) == (1, 39, 0)
    assert [s.params["from"] for s in server.sent] == ["2026-09"]  # only the failed slice came again
    assert len(rows(conn)) == 3


# -- harvest: failures --------------------------------------------------------------------------------------------


async def test_429_without_retry_after_waits_20_seconds_and_tries_again(conn, client, clock, server):
    server.books = [Book(make_isbn("9784100", 1), "小説", month="2026-10")]
    state = {"left": 2}

    def throttle(params):
        if state["left"] and params["from"] == "2027-05":
            state["left"] -= 1
            return too_many_requests()

    server.intercept = throttle

    result = await ndl.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_error == 0 and result.fetched == 1
    assert clock.sleeps.count(ndl.RATE_LIMIT_WAIT) == 2  # not the 60 s default
    tail_requests = [s for s in server.sent if s.params["from"] == "2027-05" and s.params["ndc"] == "913.6"]
    assert len(tail_requests) == 3


async def test_a_429_that_names_retry_after_is_obeyed(conn, client, clock, server):
    state = {"left": 1}

    def throttle(params):
        if state["left"]:
            state["left"] -= 1
            return httpx.Response(429, headers={"Retry-After": "7"}, content=too_many_requests().content)

    server.intercept = throttle
    await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert 7.0 in clock.sleeps and ndl.RATE_LIMIT_WAIT not in clock.sleeps


async def test_a_slice_that_keeps_failing_is_marked_error_and_the_run_goes_on(conn, client, clock, server):
    server.books = [
        Book(make_isbn("9784100", 1), "十月", month="2026-10"),
        Book(make_isbn("9784100", 2), "九月", month="2026-09"),
    ]
    server.intercept = lambda p: too_many_requests() if (p["ndc"], p["from"]) == ("913.6", "2026-10") else None

    result = await ndl.harvest(conn, client, clock=clock, log=quiet)

    assert (result.slices_error, result.slices_done) == (1, 39)
    assert "HTTPStatusError" in result.errors[0] and "913.6:2026-10" in result.errors[0]
    assert db.slice_status(conn, "ndl", "913.6:2026-10")["status"] == "error"
    failed = [s for s in server.sent if s.params["from"] == "2026-10" and s.params["ndc"] == "913.6"]
    assert len(failed) == ndl.RETRIES + 1
    assert [r["title"] for r in rows(conn)] == ["九月"]


async def test_timeouts_are_retried(conn, client, clock, server):
    state = {"left": 1}

    def slow(params):
        if state["left"]:
            state["left"] -= 1
            raise httpx.ReadTimeout("slow")

    server.intercept = slow
    result = await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert result.slices_error == 0 and result.slices_done == 40
    assert 2.0 in clock.sleeps  # request_with_retry's first backoff


async def test_an_error_page_with_status_200_is_an_error_slice(conn, client, clock, server):
    server.intercept = lambda p: (
        httpx.Response(200, content=b"<error><code>500</code><message>oops</message></error>")
        if p["from"] == "2026-10" and p["ndc"] == "726.1"
        else None
    )
    result = await ndl.harvest(conn, client, clock=clock, log=quiet)
    assert result.slices_error == 1 and "500 oops" in result.errors[0]


async def test_a_failed_prefix_fails_the_slice_but_keeps_the_other_prefixes(conn, client, clock, server):
    server.books = [Book(make_isbn(f"9784{n % 10}", n), f"本 {n:04d}", month="2026-09") for n in range(600)]
    server.intercept = lambda p: httpx.Response(404) if p.get("isbn") == "97843" else None

    result = await ndl.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_error == 1 and "isbn=97843" in result.errors[0]
    state = db.slice_status(conn, "ndl", "913.6:2026-09")
    assert state["status"] == "error"
    stored = {r["external_id"] for r in rows(conn)}
    missing = {b.isbn for b in server.books} - stored
    assert len(missing) == 10 and all(i.startswith("97843") for i in missing)  # only what 97843 alone held
    assert len(stored) == 590  # the month's first 500 and the other nine prefixes made it


# -- harvest: duplicates and dates -------------------------------------------------------------------------------------


async def test_one_isbn_in_two_prefix_queries_is_stored_once(conn, client, clock, server):
    # The month query's first 500 and the prefix queries overlap.
    server.books = [Book(make_isbn(f"9784{n % 10}", n), f"本 {n:04d}", month="2026-09") for n in range(800)]

    result = await ndl.harvest(conn, client, clock=clock, log=quiet)

    assert result.fetched == 800 and len(rows(conn)) == 800


async def test_a_finer_date_stored_earlier_is_not_overwritten_by_a_coarser_one(conn, client, clock, server):
    isbn = make_isbn("9784100", 1)
    db.upsert_entries(
        conn,
        [db.Entry(source="ndl", external_id=isbn, kind="book", title="古い題", start_date="2026-10-15", start_precision="day")],
    )
    server.books = [Book(isbn, "新しい題", month="2026-10")]  # NDL now only knows the month

    await ndl.harvest(conn, client, clock=clock, log=quiet)

    [row] = rows(conn)
    assert row["title"] == "新しい題"  # everything else is updated
    assert (row["start_date"], row["start_precision"]) == ("2026-10-15", "day")


# -- recent (build --recent) -------------------------------------------------------------------------------------


def test_recent_slices_are_the_tails_and_the_months_from_last_month_on():
    slices = ndl.build_slices(date(2026, 10, 14), recent=True)
    keys = [s.key for s in slices]

    assert ndl.RECENT_MONTHS_BACK == 1
    assert len(keys) == 2 + 8 * 2  # two tails, and 2026-09 .. 2027-04 for both classes
    assert keys[:2] == ["913.6:2027-05..", "726.1:2027-05.."]
    assert keys[2:4] == ["913.6:2027-04", "726.1:2027-04"]
    assert keys[-2:] == ["913.6:2026-09", "726.1:2026-09"]
    assert "913.6:2026-10" in keys and "913.6:2026-08" not in keys
    # the slices are the full build's own, so a recent run settles the same records
    full = {s.key: s for s in ndl.build_slices(date(2026, 10, 14))}
    assert all(full[s.key] == s for s in slices)
    assert len(ndl.build_slices(date(2026, 10, 14))) == 40  # without `recent` nothing changed


def test_recent_slices_cross_the_year_boundary():
    keys = [s.key for s in ndl.build_slices(date(2026, 1, 31), recent=True) if s.key.startswith("726.1")]
    assert keys[0] == "726.1:2026-08.."
    assert keys[-1] == "726.1:2025-12"


async def test_recent_refetches_done_months_ahead_and_leaves_the_older_ones(conn, client, clock, server):
    server.books = [
        Book(make_isbn("9784100", 1), "十月", month="2026-10"),
        Book(make_isbn("9784100", 2), "九月", month="2026-09"),
        Book(make_isbn("9784100", 3), "八月", month="2026-08"),
        Book(make_isbn("9784100", 4), "来年", month="2027-02"),
    ]
    await ndl.harvest(conn, client, clock=clock, log=quiet)  # the full build: 40 slices done
    old_state = dict(db.slice_status(conn, "ndl", "913.6:2026-08"))
    old_fetched = conn.execute("SELECT fetched_at FROM entries WHERE id = ?", (f"ndl:{make_isbn('9784100', 3)}",)).fetchone()[0]

    server.books.append(Book(make_isbn("9784100", 5), "新刊", month="2026-10"))  # catalogued since
    server.books[0] = Book(make_isbn("9784100", 1), "十月 改題", month="2026-10")
    server.sent.clear()
    result = await ndl.harvest(conn, client, clock=clock, log=quiet, recent=True)

    assert (result.slices_done, result.slices_skipped, result.slices_error) == (18, 0, 0)
    assert len(server.sent) == 18  # nothing over 500: one request per slice
    assert min(s.params["from"] for s in server.sent) == "2026-09"  # 2026-08 and older: not asked
    assert [s.params["from"] for s in server.sent][:4] == ["2027-05", "2027-05", "2027-04", "2027-04"]
    gaps = [b.at - a.at for a, b in zip(server.sent, server.sent[1:])]
    assert all(gap >= 1.0 for gap in gaps)  # still 1 req/s
    assert result.fetched == 4  # 十月 (changed), 新刊, 九月, 来年

    titles = {r["title"] for r in rows(conn)}
    assert titles == {"十月 改題", "新刊", "九月", "八月", "来年"}  # updated and added; nothing deleted
    assert db.is_slice_done(conn, "ndl", "913.6:2026-10")
    assert dict(db.slice_status(conn, "ndl", "913.6:2026-08")) == old_state  # untouched
    assert conn.execute("SELECT fetched_at FROM entries WHERE id = ?", (f"ndl:{make_isbn('9784100', 3)}",)).fetchone()[0] == old_fetched


async def test_recent_cuts_a_big_current_month_by_isbn_prefix(conn, client, clock, server):
    server.books = [Book(make_isbn(f"9784{n % 10}", n), f"本 {n:04d}", month="2026-10") for n in range(900)]

    result = await ndl.harvest(conn, client, clock=clock, log=quiet, recent=True)  # no earlier build at all

    assert result.slices_error == 0 and server.out_of_reach == 0
    asked = server.slice_requests("913.6", "2026-10")
    assert "isbn" not in asked[0] and [p["isbn"] for p in asked[1:]] == [f"9784{d}" for d in "0123456789"]
    assert len(rows(conn)) == 900


async def test_recent_honours_the_limit(conn, client, clock, server):
    server.books = [Book(make_isbn("9784100", n), f"小説 {n}", month="2026-10") for n in range(6)]
    result = await ndl.harvest(conn, client, clock=clock, limit=4, log=quiet, recent=True)

    assert result.fetched == 4 and len(rows(conn)) == 4
    assert not any(s.params["from"] <= "2026-08" for s in server.sent)
