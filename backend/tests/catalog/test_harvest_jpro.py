"""Tests for the JPRO (NDL OAI-PMH set=jpro) catalog harvester (backend/catalog/harvest/jpro.py).

No live requests: httpx.MockTransport plus a fake clock. A small fake of the OAI-PMH
endpoint answers what the live one does (verified 2026-10-15): `from` / `until` select
records by their update datestamp, a list longer than 200 records goes on with a
`resumptionToken` (and the token is the request's only other argument), every list ends
with a `<resumptionToken completeListSize=.. cursor=../>` element (empty on the last
page) and a day without records answers the `noRecordsMatch` error.

Recorded fixtures (https://ndlsearch.ndl.go.jp/api/oaipmh, 2026-10-15, UA
`triptych/0.1 (personal tracker)`; the `<dcterms:tableOfContents>` blocks removed):
- jpro_oai_2026-10-14_p1.xml / _p2.xml: `verb=ListRecords&metadataPrefix=dcndl&set=jpro&from=2026-10-14&until=2026-10-14`
  and its resumptionToken page, cut down to 21 records picked from the 10/14 pages and a few
  other days (manga and novels by genre words and by NDC, a mook, picture books, an exam
  book, an adult comic, a 特装版, an e-book without ISBN, a deleted record, old books)
- jpro_oai_no_records.xml: the answer for a day without records (`from=until=2026-09-25`)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx
import pytest

from backend.catalog import db, runtime
from backend.catalog.harvest import jpro
from backend.catalog.harvest import ndl as ndl_harvest
from backend.catalog.link import run_link

FIXTURES = Path(__file__).parent / "fixtures"
quiet = lambda message: None  # noqa: E731

TODAY = date(2026, 10, 15)


def recorded(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeClock:
    """Time only moves when someone sleeps, so pacing is observable and instant."""

    def __init__(self, today: date = TODAY) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []
        self.today = today

    def now(self) -> datetime:
        return datetime(self.today.year, self.today.month, self.today.day, 3, 0, tzinfo=timezone.utc)  # 12:00 JST

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(seconds, 0.0)


# -- synthetic records ---------------------------------------------------------------------------


def isbn13(body: str) -> str:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


def make_isbn(n: int, prefix: str = "978482") -> str:
    return isbn13(f"{prefix}{n:0>{12 - len(prefix)}}")


MANGA_LABELS = ("一般", "コミックス", "コミックス・劇画", "コミックス（書籍）")
NOVEL_LABELS = ("一般", "文庫", "日本文学小説・物語", "文庫")


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass
class Rec:
    isbn: Optional[str]
    title: str
    issued: Optional[str] = "2026-11-20"
    labels: tuple[str, ...] = MANGA_LABELS
    ndc: Optional[str] = None  # e.g. "ndc10/726.1"
    series: Optional[str] = None
    volume: Optional[str] = None
    authors: tuple[str, ...] = ("作者 太郎 著・文・その他",)
    publisher: str = "テスト社"
    stamp: str = "2026-10-14T15:00:00Z"
    deleted: bool = False

    @property
    def identifier(self) -> str:
        return f"R100000137-I{self.isbn or '06A0000000000000001X'}"

    def xml(self) -> str:
        header = (
            f"<header{' status=\"deleted\"' if self.deleted else ''}>"
            f"<identifier>oai:ndlsearch.ndl.go.jp:{self.identifier}</identifier>"
            f"<datestamp>{self.stamp}</datestamp><setSpec>jpro</setSpec></header>"
        )
        if self.deleted:
            return f"<record>{header}</record>"
        about = f"https://ndlsearch.ndl.go.jp/books/{self.identifier}"
        parts = [
            f'<dcterms:identifier rdf:datatype="http://ndl.go.jp/dcndl/terms/ISBN">{self.isbn}</dcterms:identifier>'
            if self.isbn
            else "",
            f"<dcterms:title>{esc(self.title)}</dcterms:title>",
            f"<dc:title><rdf:Description><rdf:value>{esc(self.title)}</rdf:value>"
            "<dcndl:transcription>テスト</dcndl:transcription></rdf:Description></dc:title>",
        ]
        if self.volume:
            parts.append(f"<dcndl:volume><rdf:Description><rdf:value>{esc(self.volume)}</rdf:value></rdf:Description></dcndl:volume>")
        if self.series:
            parts.append(f"<dcndl:seriesTitle><rdf:Description><rdf:value>{esc(self.series)}</rdf:value></rdf:Description></dcndl:seriesTitle>")
        for author in self.authors:
            name = author.rsplit(" ", 1)[0]
            parts.append(f"<dcterms:creator><foaf:Agent><foaf:name>{esc(name)}</foaf:name></foaf:Agent></dcterms:creator>")
            parts.append(f"<dc:creator>{esc(author)}</dc:creator>")
        parts.append(f"<dcterms:publisher><foaf:Agent><foaf:name>{self.publisher}</foaf:name></foaf:Agent></dcterms:publisher>")
        if self.issued:
            parts.append(f'<dcterms:issued rdf:datatype="http://purl.org/dc/terms/W3CDTF">{self.issued}</dcterms:issued>')
        parts.append("<dcterms:description>装丁 : B6</dcterms:description>")
        parts.extend(f"<dcterms:description>{esc(label)}</dcterms:description>" for label in self.labels)
        if self.ndc:
            parts.append(f'<dcterms:subject rdf:resource="http://id.ndl.go.jp/class/{self.ndc}"/>')
        if self.isbn:
            parts.append(f'<foaf:thumbnail rdf:resource="https://ndlsearch.ndl.go.jp/thumbnail/{self.isbn}.jpg"/>')
        return (
            f"<record>{header}<metadata><rdf:RDF>"
            f'<dcndl:BibAdminResource rdf:about="{about}"/>'
            f'<dcndl:BibResource rdf:about="{about}#material">{"".join(parts)}</dcndl:BibResource>'
            "</rdf:RDF></metadata></record>"
        )


ROOT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<OAI-PMH xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://www.openarchives.org/OAI/2.0/" '
    'xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcndl="http://ndl.go.jp/dcndl/terms/" '
    'xmlns:foaf="http://xmlns.com/foaf/0.1/">'
    "<responseDate>2026-10-15T12:00:00Z</responseDate><request>https://ndlsearch.ndl.go.jp/api/oaipmh</request>"
)


def page_xml(records: list[str], total: int, cursor: int, token: Optional[str]) -> bytes:
    resumption = (
        f'<resumptionToken completeListSize="{total}" cursor="{cursor}">{token}</resumptionToken>'
        if token
        else f'<resumptionToken completeListSize="{total}" cursor="{cursor}"/>'
    )
    return f"{ROOT}<ListRecords>{''.join(records)}{resumption}</ListRecords></OAI-PMH>".encode()


def oai_error(code: str, message: str = "error") -> bytes:
    return f'{ROOT}<error code="{code}">{message}</error></OAI-PMH>'.encode()


@dataclass
class Sent:
    at: float
    params: dict[str, str]
    user_agent: str


@dataclass
class FakeOai:
    clock: FakeClock
    days: dict[str, list[str]] = field(default_factory=dict)  # "YYYY-MM-DD" -> record XML
    sizes: dict[str, int] = field(default_factory=dict)  # completeListSize claimed for a day (bulk days)
    sent: list[Sent] = field(default_factory=list)
    intercept: Optional[Callable[[dict[str, str]], Optional[httpx.Response]]] = None

    def add(self, day: str, *records: Rec) -> None:
        self.days.setdefault(day, []).extend(r.xml() for r in records)

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert request.url.host == "ndlsearch.ndl.go.jp" and request.url.path == "/api/oaipmh"
        self.sent.append(Sent(self.clock.t, params, request.headers.get("user-agent", "")))
        if self.intercept is not None:
            replaced = self.intercept(params)
            if replaced is not None:
                return replaced
        if "resumptionToken" in params:
            assert set(params) == {"verb", "resumptionToken"}  # the token is exclusive
            _, day, start = params["resumptionToken"].split("/")
            cursor = int(start)
        else:
            assert params["verb"] == "ListRecords" and params["metadataPrefix"] == "dcndl" and params["set"] == "jpro"
            assert params["from"] == params["until"]  # one day per slice
            day, cursor = params["from"], 0
        records = self.days.get(day, [])
        total = self.sizes.get(day, len(records))
        if total == 0:
            return httpx.Response(200, content=oai_error("noRecordsMatch", "no record match the search criteria"))
        page = records[cursor : cursor + 200]
        after = cursor + 200
        token = f"tok/{day}/{after}" if after < total else None
        return httpx.Response(200, content=page_xml(page, total, cursor, token))

    def days_asked(self) -> list[str]:
        return [s.params["from"] for s in self.sent if "from" in s.params]


def too_many_requests() -> httpx.Response:
    return httpx.Response(
        429,
        content='<?xml version="1.0"?><error><code>429</code><message>Too Many Requests</message></error>'.encode(),
    )


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def server(clock) -> FakeOai:
    return FakeOai(clock)


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
    return [db.row_to_dict(r) for r in conn.execute(f"SELECT * FROM entries WHERE {where} ORDER BY id")]


def row(conn: sqlite3.Connection, isbn: str) -> Optional[dict]:
    found = conn.execute("SELECT * FROM entries WHERE id = ?", (f"ndl:{isbn}",)).fetchone()
    return db.row_to_dict(found) if found else None


def recorded_records() -> list[jpro.JproRecord]:
    first = jpro.parse_page(recorded("jpro_oai_2026-10-14_p1.xml"))
    second = jpro.parse_page(recorded("jpro_oai_2026-10-14_p2.xml"))
    return first.records + second.records


# -- parsing ---------------------------------------------------------------------------------------


def test_recorded_page_is_parsed_with_its_resumption_token():
    page = jpro.parse_page(recorded("jpro_oai_2026-10-14_p1.xml"))

    assert (page.total, page.cursor, len(page.records)) == (21, 0, 12)
    assert page.token and page.token.startswith("dcndl/2026-10-14T00:00:00Z/2026-10-14T23:59:59Z/jpro/200/")

    yamato = page.records[0]
    assert yamato.identifier == "R100000137-I9784065448236"
    assert yamato.datestamp == "2026-10-14T15:49:26Z"
    assert yamato.isbn == "9784065448236"
    assert yamato.title == "MADE IN YAMATO(1)"  # NFKC: full-width spaces and brackets folded
    assert yamato.reading == "メイドイン ヤマト1"
    assert yamato.series_title == "ヤンマガKCスペシャル"
    assert yamato.issued == "2026-09-17"
    assert yamato.publisher == "講談社"
    assert yamato.authors == ("卯月 まさゆき 著・文・その他",)
    assert yamato.labels == ("雑誌扱いコミックスおよびムック", "コミックス", "コミックス・劇画")  # 装丁 : B6 is not a genre
    assert yamato.ndcs == ("726.1",)
    assert yamato.url == "https://ndlsearch.ndl.go.jp/books/R100000137-I9784065448236"
    assert yamato.thumbnail == "https://ndlsearch.ndl.go.jp/thumbnail/9784065448236.jpg"

    by_id = {r.identifier: r for r in page.records}
    assert by_id["R100000137-I9784825102354"].volume == "14"
    assert by_id["R100000137-I9784825102347"].title == "虫かぶり姫8"  # the tentative-title mark (仮) is dropped
    ebook = by_id["R100000137-I06A0000000000970974D"]
    assert ebook.isbn is None and "電子書籍" in ebook.labels
    deleted = by_id["R100000137-I9784334110239"]
    assert deleted.deleted and deleted.isbn is None


def test_key_value_descriptions_are_not_genre_words():
    rec = Rec(make_isbn(1), "本 1", labels=("著者略歴 :", "著者略歴 : 1980年生まれ。", "JP-eコード : 06A0", "一般", "文庫", "日本文学小説・物語"))
    [record] = jpro.parse_page(page_xml([rec.xml()], 1, 0, None)).records
    assert record.labels == ("一般", "文庫", "日本文学小説・物語")


def test_last_page_has_an_empty_token():
    page = jpro.parse_page(recorded("jpro_oai_2026-10-14_p2.xml"))
    assert (page.total, page.cursor, page.token, len(page.records)) == (21, 12, None, 9)


def test_no_records_match_is_an_empty_page():
    page = jpro.parse_page(recorded("jpro_oai_no_records.xml"))
    assert (page.total, page.token, page.records) == (0, None, [])


def test_error_bodies_and_broken_pages_raise():
    with pytest.raises(jpro.JproError, match="badResumptionToken"):
        jpro.parse_page(oai_error("badResumptionToken", "expired"))
    with pytest.raises(jpro.JproError, match="429"):
        jpro.parse_page(too_many_requests().content)
    with pytest.raises(jpro.JproError, match="invalid XML"):
        jpro.parse_page(b"<OAI-PMH>")
    with pytest.raises(jpro.JproError, match="ListRecords"):
        jpro.parse_page(f"{ROOT}</OAI-PMH>".encode())


# -- classification ----------------------------------------------------------------------------------

# What every recorded record becomes on 2026-10-15 (releases from 2026-09-01 on are kept).
RECORDED_OUTCOMES = {
    "9784065448236": "manga",  # MADE IN YAMATO(1): コミックス・劇画, NDC 726.1, 2026-09-17
    "9784065449813": "manga",  # 金田一パパの事件簿(5): モーニング KC (雑誌扱い)
    "9784434386619": "manga",  # うさひな (volume 2 in its own field)
    "9784825102354": "novel",  # 悲劇の元凶となる…14: 新書 / 日本文学小説・物語
    "9784825102347": "novel",  # 虫かぶり姫8(仮)
    "9784824305466": "genre",  # ケアマネジャー試験 過去問解説集: 資格・試験
    "9784791153497": "genre",  # 臨床精神薬理: 医学
    "9784434386664": "adult",  # 狂愛プリズン: Content warning (sex)
    "9784065381557": "form",  # だいすきちいかわ シェイプブック: 絵本
    "9784105901684": "genre",  # 海と山のオムレツ: 外国文学小説 (NDC 973)
    "9784046605382": "novel",  # 雨森潤奈は湿度が高い4: MF文庫J
    "9784825102743": "excluded-word",  # …4 小冊子付特装版
    "9784081153084": "form",  # 秋本治のナイス!なチョイス: ムック その他 (コミックス・劇画)
    "9784302126699": "manga",  # 海運王の身代わり花嫁…2: no genre word, NDC 726.1
    "9784094076066": "novel",  # 平家物語 (林真理子): NDC 913.6
    "9784251100009": "form",  # あめめ: 絵本 (日本文学小説・物語)
    "9784593106486": "novel",  # うちのクラスに天才子役[図書館版]: 児童 / 日本文学小説・物語
    "9784046845276": "old",  # メンタル強め美女白川さん7: NDC 726.1, 2025-03-05
    "9784824019325": "novel",  # 3S探索者の叛逆無双譚 1
}


def test_recorded_records_are_classified():
    since = jpro.release_floor(TODAY)
    outcomes = {}
    for record in recorded_records():
        if record.isbn is None:
            assert jpro.outcome(record, since)[1] in ("deleted", "electronic")
            continue
        entry, reason = jpro.outcome(record, since)
        outcomes[record.isbn] = entry.extra["kindHint"] if entry else reason
    assert outcomes == RECORDED_OUTCOMES


@pytest.mark.parametrize(
    ("labels", "ndcs", "expected"),
    [
        # JPRO genre words (the C code's 対象 / 形態 / 内容) and NDL's NDC link
        (("一般", "コミックス", "コミックス・劇画"), (), "manga"),
        (("雑誌扱いコミックスおよびムック", "コミックス", "コミックス・劇画"), (), "manga"),  # weekly-magazine comics
        (("一般", "文庫", "日本文学小説・物語"), (), "novel"),
        (("児童 中学生以下対象", "単行本", "日本文学小説・物語"), (), "novel"),  # children's novels stay
        (("一般", "単行本", "諸芸・娯楽"), ("726.1",), "manga"),  # NDL's NDC decides when it is there
        (("一般", "文庫", "日本文学評論・随筆・その他"), ("913.6",), "novel"),
        (("一般", "文庫", "コミックス・劇画"), ("913.6",), "novel"),  # NDC before the genre word
        (("一般", "単行本", "日本文学小説・物語"), ("913.68",), "novel"),
        (("一般", "コミックス", "コミックス・劇画"), ("726.507",), "manga"),  # another NDC: the genre word
        (("ムック その他", "コミックス・劇画"), (), "form"),
        (("ムック", "日本文学小説・物語"), (), "form"),
        (("児童 中学生以下対象", "絵本", "日本文学小説・物語"), (), "form"),
        (("一般", "写真集", "コミックス・劇画"), (), "form"),
        (("一般", "図鑑", "コミックス・劇画"), ("726.1",), "form"),
        (("電子書籍", "文芸", "日本文学小説・物語"), (), "electronic"),
        (("Content warning (sex) 【成人向け（性）】", "コミックス・劇画"), (), "adult"),
        (("Any adult audience 【成人指定（理由明記なし）】", "日本文学小説・物語"), (), "adult"),
        (("一般", "単行本", "諸芸・娯楽", "コミックス（書籍）"), (), "manga"),  # JPRO's comic genre (マーマレードコミックス)
        (("雑誌扱いコミックスおよびムック", "コミックス", "コミックス（雑誌扱）"), (), "manga"),
        (("一般", "コミックス", "コミックス（廉価版）"), (), "manga"),
        (("児童 中学生以下対象", "単行本", "日本歴史", "コミックス（書籍）"), (), "genre"),  # 学習まんが
        (("一般", "単行本", "日本文学小説・物語", "芸能・タレント"), (), "genre"),  # 写真集 filed as a story
        (("一般", "単行本", "日本文学小説・物語", "趣味・実用"), (), "genre"),  # 麻雀の戦術書
        (("実用", "単行本", "コミックス・劇画"), (), "genre"),
        (("一般", "単行本", "諸芸・娯楽", "趣味・実用"), ("726.1",), "manga"),  # NDL's NDC still decides
        (("一般", "コミックス", "日本歴史"), (), "genre"),  # 学習まんが
        (("一般", "全集・双書", "外国文学小説"), ("973",), "genre"),
        (("一般", "単行本", "日本文学評論・随筆・その他"), (), "genre"),
        (("専門", "単行本", "資格・試験"), (), "genre"),
        ((), (), "genre"),
    ],
)
def test_classification_table(labels, ndcs, expected):
    record = jpro.JproRecord(
        identifier=f"R100000137-I{make_isbn(1)}", datestamp="2026-10-14T15:00:00Z", isbn=make_isbn(1),
        title="テストの本 3", issued="2026-11-20", labels=labels, ndcs=ndcs,
    )
    book_class, reason = jpro.classify(record)
    assert (book_class.hint if book_class else reason) == expected


@pytest.mark.parametrize(
    "title",
    ["紺野栞写真集", "映画『汝、星のごとく』公式ビジュアルブック", "生徒会にも穴はある!公式キャラクターブック!",
     "ブルーロック エゴイストシールブック", "吸血姫 美夕維(コミック)3 特別版"],
)
def test_books_about_a_work_are_dropped_by_their_title(title):
    record = jpro.JproRecord(
        identifier="x", datestamp="2026-10-14T15:00:00Z", isbn=make_isbn(3), title=title,
        issued="2026-11-20", labels=MANGA_LABELS,
    )
    assert jpro.outcome(record, jpro.release_floor(TODAY)) == (None, "excluded-word")


def test_release_floor_is_the_first_day_of_last_month():
    assert jpro.release_floor(date(2026, 10, 15)) == date(2026, 9, 1)
    assert jpro.release_floor(date(2026, 1, 3)) == date(2025, 12, 1)


@pytest.mark.parametrize(
    ("issued", "kept"),
    [
        ("2026-09-01", True),
        ("2026-08-31", False),
        ("2026-09", True),  # month precision: September overlaps
        ("2026-08", False),
        ("2026", True),  # a year that includes September
        ("2025", False),
        ("2027-03-10", True),
        (None, False),
    ],
)
def test_only_releases_from_last_month_on_are_kept(issued, kept):
    record = jpro.JproRecord(
        identifier="x", datestamp="2026-10-14T15:00:00Z", isbn=make_isbn(2), title="日付の本 1",
        issued=issued, labels=MANGA_LABELS,
    )
    entry, reason = jpro.outcome(record, jpro.release_floor(TODAY))
    assert (entry is not None) == kept
    if not kept:
        assert reason in ("old", "date")


def test_entries_of_recorded_records():
    entries = {e.external_id: e for e in jpro.entries_of(recorded_records(), jpro.release_floor(TODAY))[0]}

    yamato = entries["9784065448236"]
    assert yamato.id == "ndl:9784065448236"  # the same row as the catalogued NDL record later
    assert (yamato.source, yamato.kind, yamato.format) == ("ndl", "book", "漫画")
    assert yamato.title == "MADE IN YAMATO(1)"
    assert yamato.title_reading == "メイドイン ヤマト1"
    assert (yamato.start_date, yamato.start_precision) == ("2026-09-17", "day")
    assert yamato.creators == ["卯月まさゆき"]  # roles dropped, the space between 姓 and 名 too (NDL's form)
    assert yamato.url == "https://ndlsearch.ndl.go.jp/books/R100000137-I9784065448236"
    assert yamato.cover is None  # NDL's thumbnails answer 403 to other sites (checked 2026-10-15)
    assert yamato.extra == {
        "isbn": "9784065448236",
        "volume": "(1)",
        "series": "MADE IN YAMATO",
        "line": "ヤンマガKCスペシャル",
        "publisher": "講談社",
        "ndc": "726.1",
        "kindHint": "manga",
        "via": "jpro",
        "jpro": {
            "title": "MADE IN YAMATO(1)",
            "volume": None,
            "seriesTitle": "ヤンマガKCスペシャル",
            "labels": ["雑誌扱いコミックスおよびムック", "コミックス", "コミックス・劇画"],
        },
    }

    queen = entries["9784825102354"]
    assert queen.title == "悲劇の元凶となる最強外道ラスボス女王は民の為に尽くします。14"
    assert (queen.format, queen.extra["kindHint"], queen.extra["ndc"]) == ("小説", "novel", "913.6")
    assert queen.extra["series"] == "悲劇の元凶となる最強外道ラスボス女王は民の為に尽くします。"
    assert (queen.extra["volume"], queen.extra["line"]) == ("14", "アイリスNEO")
    assert queen.creators == ["天壱", "鈴ノ助"]

    mushi = entries["9784825102347"]
    assert (mushi.title, mushi.extra["series"], mushi.extra["volume"]) == ("虫かぶり姫8", "虫かぶり姫", "8")

    usahina = entries["9784434386619"]
    assert usahina.title == "うさひな -5年付き合ってる大学生カップルの話- 2"
    assert usahina.extra["volume"] == "2" and usahina.extra["line"] == "Letter Comics"

    kindaichi = entries["9784065449813"]
    assert (kindaichi.extra["series"], kindaichi.extra["line"]) == ("金田一パパの事件簿", "モーニング KC")
    assert kindaichi.creators == ["天樹征丸", "さとうふみや"]

    amamori = entries["9784046605382"]
    assert (amamori.extra["series"], amamori.extra["volume"], amamori.extra["line"]) == ("雨森潤奈は湿度が高い", "4", "MF文庫J")

    marmalade = entries["9784302126699"]
    assert marmalade.extra["line"] == "マーマレードコミックス"  # "; CMMBL227" is the number in the label
    assert marmalade.extra["ndc"] == "726.1"

    heike = entries["9784094076066"]
    assert (heike.extra["series"], heike.extra["line"], heike.extra["volume"]) == ("平家物語", "", None)

    assert set(entries) == {isbn for isbn, kind in RECORDED_OUTCOMES.items() if kind in ("manga", "novel")}


def test_entries_of_counts_the_reasons():
    _, counts = jpro.entries_of(recorded_records(), jpro.release_floor(TODAY))
    assert counts == {
        "manga": 4, "novel": 6, "genre": 3, "form": 3, "adult": 1, "excluded-word": 1, "old": 1,
        "electronic": 1, "deleted": 1,
    }


# -- slices ------------------------------------------------------------------------------------------


def test_days_run_newest_first_over_the_window():
    days = jpro.window_days(TODAY)
    assert len(days) == jpro.WINDOW_DAYS == 30
    assert days[0] == TODAY and days[-1] == date(2026, 9, 16)
    assert days == sorted(days, reverse=True)
    assert jpro.slice_key(TODAY) == "day:2026-10-15"
    assert jpro.window_days(TODAY, start=date(2026, 10, 12)) == [date(2026, 10, d) for d in (15, 14, 13, 12)]


# -- harvest: storage and requests ----------------------------------------------------------------------


async def test_each_day_is_one_request_at_one_per_second_and_settled_days_are_done(conn, client, clock, server):
    server.add("2026-10-14", Rec(make_isbn(1), "漫画 A 1"), Rec(make_isbn(2), "小説 B 2", labels=NOVEL_LABELS, series="テスト文庫"))
    server.add("2026-10-01", Rec(make_isbn(3), "漫画 C 3", issued="2026-10-20"))
    server.add("2026-09-20", Rec(make_isbn(4), "ムック D", labels=("ムック その他", "コミックス・劇画")))

    result = await jpro.harvest(conn, client, clock=clock, log=quiet)

    assert (result.source, result.fetched, result.slices_error) == ("jpro", 3, 0)
    assert len(server.sent) == 30  # 30 days, one request each (none goes past 200 records)
    assert server.days_asked()[:3] == ["2026-10-15", "2026-10-14", "2026-10-13"]  # newest first
    assert server.days_asked()[-1] == "2026-09-16"
    assert {s.user_agent for s in server.sent} == {runtime.USER_AGENT}
    gaps = [b.at - a.at for a, b in zip(server.sent, server.sent[1:])]
    assert all(gap >= 1.0 for gap in gaps)  # 1 req/s
    assert server.sent[0].params == {
        "verb": "ListRecords", "metadataPrefix": "dcndl", "set": "jpro", "from": "2026-10-15", "until": "2026-10-15",
    }

    # A day is final two days later (CloudFront keeps an answer for about 23 hours); today and
    # yesterday stay open and are asked again by the next run.
    assert result.slices_done == 28
    for day in ("2026-10-15", "2026-10-14"):
        state = db.slice_status(conn, "jpro", f"day:{day}")
        assert state["status"] == "pending" and "open" in state["message"]
    assert db.is_slice_done(conn, "jpro", "day:2026-10-13")
    assert db.slice_status(conn, "jpro", "day:2026-10-01")["fetched"] == 1

    stored = rows(conn)
    assert [r["id"] for r in stored] == [f"ndl:{make_isbn(n)}" for n in (1, 2, 3)]
    novel = row(conn, make_isbn(2))
    assert (novel["source"], novel["kind"], novel["format"]) == ("ndl", "book", "小説")
    assert (novel["extra"]["series"], novel["extra"]["line"], novel["extra"]["via"]) == ("小説 B", "テスト文庫", "jpro")
    assert (novel["start_date"], novel["start_precision"]) == ("2026-11-20", "day")


async def test_resumption_tokens_are_followed_through_a_day(conn, client, clock, server):
    server.add("2026-10-10", *[Rec(make_isbn(n), f"漫画 {n:03d}") for n in range(450)])

    result = await jpro.harvest(conn, client, clock=clock, log=quiet)

    asked = [s.params for s in server.sent if s.params.get("from") == "2026-10-10" or "resumptionToken" in s.params]
    assert [p.get("resumptionToken") for p in asked] == [None, "tok/2026-10-10/200", "tok/2026-10-10/400"]
    assert result.fetched == 450 and len(rows(conn)) == 450
    assert db.slice_status(conn, "jpro", "day:2026-10-10")["fetched"] == 450


async def test_a_bulk_day_is_measured_first_and_skipped(conn, client, clock, server):
    # 2026-09-27: NDL re-stamped the whole set (3,556,855 records) in one day.
    server.add("2026-09-27", *[Rec(make_isbn(n), f"漫画 {n:03d}") for n in range(200)])
    server.sizes["2026-09-27"] = 3_556_855
    server.add("2026-09-26", Rec(make_isbn(900), "次の日"))
    logs: list[str] = []

    result = await jpro.harvest(conn, client, clock=clock, log=logs.append)

    bulk = [s.params for s in server.sent if s.params.get("from") == "2026-09-27" or "2026-09-27" in s.params.get("resumptionToken", "")]
    assert len(bulk) == 1  # the first page told the size; no token was followed
    state = db.slice_status(conn, "jpro", "day:2026-09-27")
    assert state["status"] == "done" and "3556855" in state["message"]
    assert any("3556855" in line and "skip" in line for line in logs)
    assert row(conn, make_isbn(0)) is None  # nothing of the bulk day is kept
    assert row(conn, make_isbn(900)) is not None  # the run went on
    assert result.slices_error == 0


async def test_rerun_skips_settled_days_and_fresh_asks_them_again(conn, client, clock, server):
    server.add("2026-10-01", Rec(make_isbn(1), "漫画 1"))
    await jpro.harvest(conn, client, clock=clock, log=quiet)
    server.sent.clear()

    again = await jpro.harvest(conn, client, clock=clock, log=quiet)
    assert server.days_asked() == ["2026-10-15", "2026-10-14"]  # only the open days
    assert (again.slices_skipped, again.slices_done) == (28, 0)

    db.reset_source(conn, "jpro")  # build --fresh
    server.sent.clear()
    fresh = await jpro.harvest(conn, client, clock=clock, log=quiet)
    assert len(server.sent) == 30 and fresh.slices_done == 28
    assert len(rows(conn)) == 1


async def test_a_later_run_settles_the_days_that_were_open(conn, client, clock, server):
    await jpro.harvest(conn, client, clock=clock, log=quiet)
    clock.today = TODAY + timedelta(days=2)
    server.sent.clear()

    await jpro.harvest(conn, client, clock=clock, log=quiet)

    assert server.days_asked() == ["2026-10-17", "2026-10-16", "2026-10-15", "2026-10-14"]
    assert db.is_slice_done(conn, "jpro", "day:2026-10-15") and db.is_slice_done(conn, "jpro", "day:2026-10-14")
    assert not db.is_slice_done(conn, "jpro", "day:2026-10-16")


async def test_limit_stops_the_run_and_leaves_the_day_pending(conn, client, clock, server):
    server.add("2026-10-14", *[Rec(make_isbn(n), f"漫画 {n}") for n in range(3)])
    server.add("2026-10-12", *[Rec(make_isbn(10 + n), f"小説 {n}", labels=NOVEL_LABELS) for n in range(3)])
    server.add("2026-10-01", Rec(make_isbn(90), "古い日"))

    result = await jpro.harvest(conn, client, clock=clock, limit=4, log=quiet)

    assert result.fetched == 4 and len(rows(conn)) == 4
    assert "2026-10-01" not in server.days_asked()  # older days were never asked
    state = db.slice_status(conn, "jpro", "day:2026-10-12")
    assert (state["status"], state["fetched"], state["message"]) == ("pending", 1, "limit")
    assert db.slice_status(conn, "jpro", jpro.RUN_KEY) is None  # a trial run is not a finished run

    full = await jpro.harvest(conn, client, clock=clock, log=quiet)
    assert full.slices_error == 0 and db.is_slice_done(conn, "jpro", "day:2026-10-12")
    assert len(rows(conn)) == 7


# -- recent (build --recent) --------------------------------------------------------------------------------


async def test_recent_starts_a_day_before_the_last_finished_run(conn, client, clock, server):
    server.add("2026-10-10", Rec(make_isbn(1), "漫画 1"))
    await jpro.harvest(conn, client, clock=clock, log=quiet)  # finished on 2026-10-15
    run = db.slice_status(conn, "jpro", jpro.RUN_KEY)
    assert (run["status"], run["cursor"]) == ("done", "2026-10-15")

    clock.today = date(2026, 10, 20)
    server.add("2026-10-18", Rec(make_isbn(2), "漫画 2"))
    server.add("2026-10-13", Rec(make_isbn(3), "漫画 3"))  # done before, asked again only by a window
    server.sent.clear()
    result = await jpro.harvest(conn, client, clock=clock, log=quiet, recent=True)

    assert server.days_asked() == [f"2026-10-{d}" for d in range(20, 13, -1)]  # 10/20 .. 10/14, done or not
    assert result.fetched == 1 and row(conn, make_isbn(2)) is not None
    assert row(conn, make_isbn(3)) is None  # 10/13 is older than the recent window
    assert db.slice_status(conn, "jpro", jpro.RUN_KEY)["cursor"] == "2026-10-20"
    assert db.is_slice_done(conn, "jpro", "day:2026-10-18")


async def test_recent_without_a_finished_run_works_like_a_plain_build(conn, client, clock, server):
    db.mark_slice(conn, "jpro", "day:2026-10-01", "done", fetched=0)
    result = await jpro.harvest(conn, client, clock=clock, log=quiet, recent=True)
    assert len(server.sent) == 29 and result.slices_skipped == 1
    assert db.slice_status(conn, "jpro", jpro.RUN_KEY)["cursor"] == "2026-10-15"


async def test_a_run_with_an_error_does_not_move_the_recent_start(conn, client, clock, server):
    await jpro.harvest(conn, client, clock=clock, log=quiet)
    clock.today = date(2026, 10, 20)
    server.intercept = lambda p: httpx.Response(400, content=b"bad") if p.get("from") == "2026-10-17" else None

    result = await jpro.harvest(conn, client, clock=clock, log=quiet, recent=True)

    assert result.slices_error == 1 and "HTTP 400" in result.errors[0]
    assert db.slice_status(conn, "jpro", jpro.RUN_KEY)["cursor"] == "2026-10-15"
    assert db.slice_status(conn, "jpro", "day:2026-10-17")["status"] == "error"


# -- failures --------------------------------------------------------------------------------------------------


async def test_429_without_retry_after_waits_20_seconds_and_tries_again(conn, client, clock, server):
    server.add("2026-10-15", Rec(make_isbn(1), "漫画"))
    state = {"left": 2}

    def throttle(params):
        if state["left"] and params.get("from") == "2026-10-15":
            state["left"] -= 1
            return too_many_requests()

    server.intercept = throttle
    result = await jpro.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_error == 0 and result.fetched == 1
    assert clock.sleeps.count(jpro.RATE_LIMIT_WAIT) == 2


async def test_503_with_retry_after_is_obeyed(conn, client, clock, server):
    state = {"left": 1}

    def maintenance(params):
        if state["left"]:
            state["left"] -= 1
            return httpx.Response(503, headers={"Retry-After": "9"}, content=b"maintenance")

    server.intercept = maintenance
    result = await jpro.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_error == 0
    assert 9.0 in clock.sleeps


async def test_an_oai_error_in_the_middle_of_a_day_fails_only_that_day(conn, client, clock, server):
    server.add("2026-10-10", *[Rec(make_isbn(n), f"漫画 {n:03d}") for n in range(300)])
    server.add("2026-10-09", Rec(make_isbn(900), "次の日"))
    server.intercept = lambda p: (
        httpx.Response(200, content=oai_error("badResumptionToken", "expired")) if "resumptionToken" in p else None
    )

    result = await jpro.harvest(conn, client, clock=clock, log=quiet)

    assert result.slices_error == 1 and "badResumptionToken" in result.errors[0]
    state = db.slice_status(conn, "jpro", "day:2026-10-10")
    assert state["status"] == "error" and state["fetched"] == 200  # the first page is kept
    assert row(conn, make_isbn(900)) is not None
    assert db.slice_status(conn, "jpro", jpro.RUN_KEY) is None


async def test_an_empty_day_is_settled(conn, client, clock, server):
    server.intercept = lambda p: (
        httpx.Response(200, content=recorded("jpro_oai_no_records.xml")) if p.get("from") == "2026-09-25" else None
    )
    result = await jpro.harvest(conn, client, clock=clock, log=quiet)
    assert result.slices_error == 0
    state = db.slice_status(conn, "jpro", "day:2026-09-25")
    assert (state["status"], state["fetched"]) == ("done", 0)


# -- merging with the catalogued rows ------------------------------------------------------------------------------


def catalogued(isbn: str, **fields) -> db.Entry:
    """A row the NDL OpenSearch harvester wrote for a catalogued record."""
    values = dict(
        source="ndl", external_id=isbn, kind="book", format="漫画", title="目録の題 5",
        start_date="2026-11-01", start_precision="month",
        extra={"isbn": isbn, "volume": "5", "series": "目録の題", "line": "目録コミックス", "publisher": "目録社",
               "ndc": "726.1", "kindHint": "manga"},
    )
    values.update(fields)
    return db.Entry(**values)


async def test_a_catalogued_row_keeps_its_names_and_only_takes_a_finer_date(conn, client, clock, server):
    coarse, fine, same = make_isbn(1), make_isbn(2), make_isbn(3)
    db.upsert_entries(conn, [
        catalogued(coarse),
        catalogued(fine, start_date="2026-11-05", start_precision="day"),
        catalogued(same, start_date="2026-11-05", start_precision="day", title="同じ精度"),
    ])
    server.add(
        "2026-10-10",
        Rec(coarse, "JPRO の題 5", issued="2026-11-20", series="JPRO コミックス"),
        Rec(fine, "JPRO の題 6", issued="2026-11", labels=NOVEL_LABELS),
        Rec(same, "JPRO の題 7", issued="2026-11-25"),
    )

    await jpro.harvest(conn, client, clock=clock, log=quiet)

    first = row(conn, coarse)
    assert (first["title"], first["format"]) == ("目録の題 5", "漫画")  # the catalogued names stay
    assert first["extra"]["line"] == "目録コミックス" and "via" not in first["extra"]
    assert (first["start_date"], first["start_precision"]) == ("2026-11-20", "day")  # the finer JPRO date
    second = row(conn, fine)
    assert (second["start_date"], second["start_precision"], second["format"]) == ("2026-11-05", "day", "漫画")
    third = row(conn, same)
    assert (third["start_date"], third["title"]) == ("2026-11-05", "同じ精度")  # equal precision: kept


async def test_a_jpro_row_is_updated_but_keeps_a_finer_date(conn, client, clock, server):
    isbn = make_isbn(1)
    server.add("2026-10-01", Rec(isbn, "古い題 1", issued="2026-11-20"))
    await jpro.harvest(conn, client, clock=clock, log=quiet)

    clock.today = date(2026, 10, 20)
    server.add("2026-10-18", Rec(isbn, "新しい題 1", issued="2026-11", series="新レーベル"))
    await jpro.harvest(conn, client, clock=clock, log=quiet, recent=True)

    updated = row(conn, isbn)
    assert updated["title"] == "新しい題 1" and updated["extra"]["line"] == "新レーベル"
    assert (updated["start_date"], updated["start_precision"]) == ("2026-11-20", "day")


async def test_the_catalogued_harvest_later_takes_over_the_row(conn, client, clock, server):
    """After NDL catalogues the book the OpenSearch harvester rewrites the same row."""
    isbn = make_isbn(1)
    server.add("2026-10-10", Rec(isbn, "近刊の題 3", issued="2026-11-20", series="近刊コミックス"))
    await jpro.harvest(conn, client, clock=clock, log=quiet)

    later = catalogued(isbn, title="近刊の題 3", start_date="2026-11-01", start_precision="month")
    ndl_harvest._keep_finer_dates(conn, [later])
    db.upsert_entries(conn, [later])

    [only] = rows(conn)
    assert only["id"] == f"ndl:{isbn}"
    assert "via" not in only["extra"] and only["extra"]["line"] == "目録コミックス"
    assert (only["start_date"], only["start_precision"]) == ("2026-11-20", "day")  # the JPRO day survives


# -- recorded pages end to end ---------------------------------------------------------------------------------------


async def test_recorded_pages_are_stored_and_link_to_a_work(conn, client, clock, server):
    first, second = recorded("jpro_oai_2026-10-14_p1.xml"), recorded("jpro_oai_2026-10-14_p2.xml")

    def replay(params):
        if params.get("from") == "2026-10-14":
            return httpx.Response(200, content=first)
        if params.get("resumptionToken", "").startswith("dcndl/2026-10-14"):
            return httpx.Response(200, content=second)

    server.intercept = replay
    db.upsert_entries(conn, [
        db.Entry(source="anilist", external_id="1", kind="manga", title="MADE IN YAMATO", title_native="MADE IN YAMATO",
                 format="MANGA"),
        db.Entry(source="anilist", external_id="2", kind="novel", title="虫かぶり姫", title_native="虫かぶり姫",
                 format="NOVEL", creators=["由唯"]),
    ])

    result = await jpro.harvest(conn, client, clock=clock, log=quiet)

    stored = {r["external_id"] for r in rows(conn, "source = 'ndl'")}
    assert stored == {isbn for isbn, kind in RECORDED_OUTCOMES.items() if kind in ("manga", "novel")}
    assert result.fetched == 10
    assert [s.params.get("resumptionToken", "")[:5] for s in server.sent if "resumptionToken" in s.params] == ["dcndl"]

    run_link(conn, today=TODAY)

    def cluster(entry_id):
        return conn.execute("SELECT cluster_id FROM clusters WHERE entry_id = ?", (entry_id,)).fetchone()[0]

    assert cluster("ndl:9784065448236") == cluster("anilist:1")  # series "MADE IN YAMATO" -> the AniList manga
    assert cluster("ndl:9784825102347") == cluster("anilist:2")  # 虫かぶり姫8 -> the novel
    link = conn.execute("SELECT method, confidence FROM links WHERE a_id = 'ndl:9784825102347'").fetchone()
    assert link["confidence"] >= 0.8
    summary = conn.execute("SELECT next_date, next_precision FROM cluster_summary WHERE cluster_id = ?", (cluster("anilist:2"),)).fetchone()
    assert (summary["next_date"], summary["next_precision"]) == ("2026-12-02", "day")
