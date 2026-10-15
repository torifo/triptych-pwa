"""国会図書館サーチ OAI-PMH harvester for the catalog: 近刊 books from JPRO, before NDL catalogues them.

NDL Search re-publishes the records of 出版情報登録センター (JPRO): books publishers register
one to three months before release, with ISBN, the release day, the label (叢書名) and the
trade's genre words. The OpenSearch harvester (`ndl.py`) cannot see them (`ndc=` only
returns catalogued records), so this harvester reads the OAI-PMH set `jpro`:

    https://ndlsearch.ndl.go.jp/api/oaipmh?verb=ListRecords&metadataPrefix=dcndl&set=jpro&from=<day>&until=<day>
    https://ndlsearch.ndl.go.jp/api/oaipmh?verb=ListRecords&resumptionToken=<token>

Facts verified against the live API (2026-10-15):

- No key. 200 records a page (about 1.4 MB, gzip on the wire), 1.5-4 s a page. The
  first page's `<resumptionToken completeListSize=..>` tells how many records the whole
  list holds; there is no 500-record wall as in OpenSearch. The token is the only other
  argument of the next request. A day without records answers the `noRecordsMatch` error.
- `from` / `until` select the record's update datestamp, not the release day. The
  datestamps carry a `Z` but are Japanese time (`responseDate` 17:53Z came with an HTTP
  `Date` of 08:53 GMT), so `from=until=<day>` is one JST day. `from` without `until`
  means the whole set (3.58 million records), so every query names both ends.
- Days differ a lot: a normal day holds 1,000-2,600 records (10/01-10/07: 8,865,
  10/08-10/14: 6,649), but NDL re-stamped the whole set on 2026-09-27 (3,556,855 records,
  all at 16:30:00) and the days before hold almost nothing (09/15-09/23: 9 deleted records).
  So the first page of a day is read before the rest, and a day of more than
  MAX_DAY_RECORDS is skipped (marked done with a "bulk" message; it would be 17,800 pages).
  A book whose last update was such a re-stamp is picked up when its record changes again,
  or by the OpenSearch harvester once NDL catalogues it.
- Answers carry `Cache-Control: max-age=83212` (about 23 h, CloudFront). A day asked while
  it was still going on can be answered from that copy until the next evening, so a day is
  settled (done) only SETTLE_DAYS after it; today and yesterday stay open and are asked
  again by the next run.
- Under load the API answers HTTP 429 with an XML `<error>` body and no Retry-After (the
  concurrency limit OpenSearch has too): such a 429 gets a RATE_LIMIT_WAIT one.
- A record: `header/identifier` `oai:ndlsearch.ndl.go.jp:R100000137-I<ISBN>` (e-books carry
  a JP-e code and no ISBN); `header/@status="deleted"` records have no metadata. The first
  `dcndl:BibResource` holds `dcterms:identifier` (ISBN), `dcterms:title` (full-width; NFKC
  here, a tentative-title mark `（仮）` dropped), `dc:title//dcndl:transcription`,
  `dcndl:seriesTitle//rdf:value` (the label: "モーニング KC", "MF文庫J"), sometimes
  `dcndl:volume`, `dc:creator` ("卯月 まさゆき 著・文・その他"), `dcterms:publisher//foaf:name`,
  `dcterms:issued` (the release day, W3CDTF), `dcterms:description` (genre words, below),
  sometimes an NDC link `dcterms:subject/@rdf:resource` (`.../class/ndc10/726.1`) and a
  `foaf:thumbnail`.
- The thumbnails (`https://ndlsearch.ndl.go.jp/thumbnail/<isbn>.jpg`) answer 403 unless the
  Referer is NDL Search itself (tried as an <img> from the app's origin, with no Referer and
  with `no-referrer`), and openBD has no cover for these books either: `cover` stays empty.

Kind (`classify`). The genre words are the C code's 対象 / 形態 / 内容 ("一般" / "文庫" /
"日本文学小説・物語", "雑誌扱いコミックスおよびムック" / "コミックス" / "コミックス・劇画") and
JPRO's own ("文芸", "コミックス（書籍）"). In this order:

    deleted record, "電子書籍", no ISBN                        -> dropped
    adult ("Content warning", "Any adult", 成人向け / 成人指定)  -> dropped
    形態 ムック・絵本・図鑑・事典・写真集・電磁的記録媒体         -> dropped
    an NDC link 726.1 / 913.6 (NDL's own class)                -> manga / novel
    芸能・タレント, 趣味・実用, 実用, ビジネス, 学参              -> dropped (写真集, 戦術書)
    内容 "コミックス・劇画" / "日本文学小説・物語"                -> manga / novel
    JPRO's コミックス（書籍 / 雑誌扱 / 廉価版）, not 児童         -> manga (内容 諸芸・娯楽 ...)
    anything else (資格・専門書・評論・外国文学 ...)             -> dropped

then the OpenSearch harvester's filters (978-4 ISBN, 特装版・アンソロジー・ガイド ... words,
a date that parses), a few more title words (写真集・ビジュアルブック・キャラクターブック・
シールブック・特別版) and the release floor: only books released from the first day of last
month on are kept (`release_floor`).

Rows are the OpenSearch harvester's own `ndl:<ISBN>` rows (source `ndl`, kind `book`, the
same line extraction), so a book keeps one row before and after NDL catalogues it. The
series and volume are split from the title by `text.split_volume_title` (a 近刊 title holds
the volume, edition words and subtitles: "シャングリラ・フロンティア(28)エキスパンションパス ~…~"
-> series "シャングリラ・フロンティア", volume "(28)"). A row written here has
`extra.via = "jpro"` and the record's raw fields in `extra.jpro` (title, volume, seriesTitle,
genre words), which the collector's NDL series API reads back (`backend.catalog.upcoming`)
and from which the linker splits the series again. When the row is already there:

- a catalogued row (no `via`) keeps its title, series, line and kind, and only takes the
  JPRO release day when that is finer than its own date (NDL dates are mostly months);
- an earlier JPRO row is replaced by the newer record, keeping a finer stored date;
- when NDL catalogues the book, the OpenSearch harvester overwrites the row and keeps the
  finer JPRO day (`ndl._keep_finer_dates`).

Slices are JST days, `day:<YYYY-MM-DD>`, newest first. A build asks the last WINDOW_DAYS
days and skips the settled ones (`--fresh` forgets them). `recent=True` (`build --recent`)
asks every day from RECENT_MARGIN_DAYS before the last finished run to today, settled or
not; the last finished run is the `harvest_state` row `run` (its `cursor` is the run's JST
day), written by a run that went through every day without an error or a `limit`. Without
it a recent run works like a plain build. An interrupted day is simply asked again.
1 req/s, the catalog's User-Agent.
"""

from __future__ import annotations

import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, fields
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Optional

import httpx

from ...adapters.ndl import (
    _PRECISION_RANK,
    NdlRecord,
    _creator_key,
    _date_span,
    _isbn13,
    _key,
    _nfkc,
    exclusion_reason,
    parse_issued,
)
from ...timeutil import JST
from .. import db
from ..runtime import Clock, HarvestResult, Pacer, request_with_retry
from ..text import split_volume_title
from . import ndl
from .ndl import CLASSES, BookClass, _PatientClient

SOURCE = "jpro"  # harvest_state / meta / `--source` name
ROW_SOURCE = ndl.SOURCE  # the rows are NDL book rows: "ndl:<isbn>"
VIA = "jpro"  # extra.via of a row written here
OAI_URL = "https://ndlsearch.ndl.go.jp/api/oaipmh"
SET_SPEC = "jpro"
METADATA_PREFIX = "dcndl"
REQUEST_INTERVAL = 1.0  # seconds between requests (1 req/s)
REQUEST_TIMEOUT = httpx.Timeout(90.0, connect=10.0)  # a first page was once 41 s
PAGE_SIZE = 200  # records per page (set by the server)
WINDOW_DAYS = 30  # a build asks the updates of the last 30 days (2026-10-15: ~23,000 records, ~150 requests, 12-22 min)
SETTLE_DAYS = 2  # a day is final this many days after it (CloudFront keeps answers ~23 h)
RECENT_MARGIN_DAYS = 1  # build --recent starts this long before the last finished run
MAX_DAY_RECORDS = 20_000  # a day above this is a bulk re-stamp of the set and is skipped
MAX_PAGES_PER_SLICE = MAX_DAY_RECORDS // PAGE_SIZE + 10
RATE_LIMIT_WAIT = 20.0  # seconds to wait after a 429 that names no Retry-After
RETRIES = 6
RUN_KEY = "run"  # harvest_state row of the last finished run (cursor = its JST day)

NOVEL, MANGA = CLASSES

_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
}
_RDF_RESOURCE = f"{{{_NS['rdf']}}}resource"
_RDF_DATATYPE = f"{{{_NS['rdf']}}}datatype"
_NDC_LINK = re.compile(r"/class/ndc\d*/([0-9][0-9.]*)$")
_TENTATIVE = re.compile(r"\s*[(（]\s*仮題?\s*[)）]\s*")  # 「（仮）」: the title is not final yet
_KEY_VALUE = re.compile(r"^[^:]{1,20} :(?: |$)")  # "装丁 : B6", "著者略歴 : ..." (or empty), "JP-eコード : ..."


def _label_key(text: str) -> str:
    return re.sub(r"\s+", "", _nfkc(text))


_ELECTRONIC = _label_key("電子書籍")
_MANGA_CONTENT = _label_key("コミックス・劇画")
_NOVEL_CONTENT = _label_key("日本文学小説・物語")
_EXCLUDED_FORMS = frozenset(
    _label_key(form)
    for form in ("ムック その他", "ムック", "絵本", "図鑑", "辞典・事典", "事・辞典", "電磁的記録媒体など", "写真集")
)
# JPRO's own comic genres: comics whose 内容 is not "コミックス・劇画" (マーマレードコミックス: 諸芸・娯楽)
_COMIC_GENRES = frozenset(_label_key(g) for g in ("コミックス（書籍）", "コミックス（雑誌扱）", "コミックス（廉価版）"))
# Not a story whatever 内容 says: 写真集 filed as 日本文学小説・物語 with 芸能・タレント, a 麻雀 book with 趣味・実用
_NON_FICTION = frozenset(
    _label_key(g)
    for g in ("芸能・タレント", "趣味・実用", "実用", "ビジネス", "学参", "学参Ⅰ（小中学生対象）", "学参Ⅱ（高校生対象）")
)
_ADULT_PREFIXES = ("Content warning", "Any adult")
_ADULT_WORDS = ("成人向け", "成人指定")
# Books about a work rather than a volume of it (on top of the OpenSearch harvester's words)
_EXCLUDED_TITLE_WORDS = ("写真集", "ビジュアルブック", "キャラクターブック", "シールブック", "特別版")


class JproError(Exception):
    """A response the harvester cannot use (error body, OAI error, bad XML)."""


# -- parsing --------------------------------------------------------------------------------


@dataclass(frozen=True)
class JproRecord:
    """One OAI-PMH record of the jpro set, normalized (NFKC) but not yet classified."""

    identifier: str  # "R100000137-I9784065448236"
    datestamp: str
    deleted: bool = False
    isbn: Optional[str] = None
    title: str = ""
    reading: Optional[str] = None
    volume: Optional[str] = None
    series_title: Optional[str] = None
    edition: Optional[str] = None
    publisher: Optional[str] = None
    authors: tuple[str, ...] = ()  # dc:creator, "<name> <role>"
    names: tuple[str, ...] = ()  # dcterms:creator//foaf:name
    issued: Optional[str] = None
    labels: tuple[str, ...] = ()  # genre words of dcterms:description
    ndcs: tuple[str, ...] = ()  # NDC links, "726.1"
    url: Optional[str] = None
    thumbnail: Optional[str] = None


@dataclass(frozen=True)
class OaiPage:
    records: list[JproRecord]
    token: Optional[str]  # None on the last page
    total: int  # completeListSize
    cursor: int


def _clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"\s+", " ", _nfkc(text)).strip() or None


def _value(parent: ET.Element, path: str) -> Optional[str]:
    """Text of `path`, or of its `rdf:Description/rdf:value` (dcndl writes both forms)."""
    element = parent.find(path, _NS)
    if element is None:
        return None
    value = element.findtext("rdf:Description/rdf:value", namespaces=_NS)
    return _clean(value if value is not None else element.text)


def _isbn_of(bib: ET.Element) -> Optional[str]:
    for identifier in bib.findall("dcterms:identifier", _NS):
        if (identifier.get(_RDF_DATATYPE) or "").endswith("/ISBN") and identifier.text:
            isbn = _isbn13(identifier.text)
            if isbn:
                return isbn
    return None


def _parse_record(element: ET.Element) -> JproRecord:
    header = element.find("oai:header", _NS)
    if header is None:
        raise JproError("a record without <header>")
    identifier = (header.findtext("oai:identifier", namespaces=_NS) or "").strip().rsplit(":", 1)[-1]
    datestamp = (header.findtext("oai:datestamp", namespaces=_NS) or "").strip()
    if header.get("status") == "deleted":
        return JproRecord(identifier=identifier, datestamp=datestamp, deleted=True)
    bib = next(
        (b for b in element.iterfind(".//dcndl:BibResource", _NS) if b.find("dcterms:title", _NS) is not None),
        None,
    )
    if bib is None:
        return JproRecord(identifier=identifier, datestamp=datestamp)
    title = _clean(_TENTATIVE.sub(" ", _nfkc(bib.findtext("dcterms:title", namespaces=_NS) or ""))) or ""
    labels = tuple(
        text
        for text in (_clean(el.text) for el in bib.findall("dcterms:description", _NS))
        if text and not _KEY_VALUE.match(text)
    )
    ndcs = tuple(
        match.group(1)
        for match in (_NDC_LINK.search(el.get(_RDF_RESOURCE) or "") for el in bib.findall("dcterms:subject", _NS))
        if match
    )
    thumbnail = bib.find("foaf:thumbnail", _NS)
    return JproRecord(
        identifier=identifier,
        datestamp=datestamp,
        isbn=_isbn_of(bib),
        title=title,
        reading=_clean(bib.findtext("dc:title/rdf:Description/dcndl:transcription", namespaces=_NS)),
        volume=_value(bib, "dcndl:volume"),
        series_title=_value(bib, "dcndl:seriesTitle"),
        edition=_value(bib, "dcndl:edition"),
        publisher=_clean(bib.findtext("dcterms:publisher/foaf:Agent/foaf:name", namespaces=_NS)),
        authors=tuple(text for text in (_clean(el.text) for el in bib.findall("dc:creator", _NS)) if text),
        names=tuple(
            text
            for text in (_clean(el.text) for el in bib.findall("dcterms:creator/foaf:Agent/foaf:name", _NS))
            if text
        ),
        issued=_clean(bib.findtext("dcterms:issued", namespaces=_NS)),
        labels=labels,
        ndcs=ndcs,
        url=f"https://ndlsearch.ndl.go.jp/books/{identifier}",
        thumbnail=thumbnail.get(_RDF_RESOURCE) if thumbnail is not None else None,
    )


def parse_page(body: bytes) -> OaiPage:
    """One ListRecords answer; raises JproError on an error body (bar `noRecordsMatch`) or bad XML."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise JproError(f"invalid XML response: {exc}") from exc
    if root.tag == "error":  # the 429 body: <error><code>429</code><message>...</message></error>
        raise JproError(f"{root.findtext('code') or '?'} {(root.findtext('message') or '').strip()[:80]}")
    error = root.find("oai:error", _NS)
    if error is not None:
        code = error.get("code") or "?"
        if code == "noRecordsMatch":
            return OaiPage(records=[], token=None, total=0, cursor=0)
        raise JproError(f"{code} {(error.text or '').strip()[:80]}")
    listing = root.find("oai:ListRecords", _NS)
    if listing is None:
        raise JproError("no <ListRecords> in the response")
    records = [_parse_record(element) for element in listing.findall("oai:record", _NS)]
    resumption = listing.find("oai:resumptionToken", _NS)
    if resumption is None:
        return OaiPage(records=records, token=None, total=len(records), cursor=0)
    size = resumption.get("completeListSize") or ""
    cursor = resumption.get("cursor") or ""
    return OaiPage(
        records=records,
        token=(resumption.text or "").strip() or None,
        total=int(size) if size.isdigit() else len(records),
        cursor=int(cursor) if cursor.isdigit() else 0,
    )


# -- classification ---------------------------------------------------------------------------


def _is_adult(label: str) -> bool:
    return label.startswith(_ADULT_PREFIXES) or any(word in label for word in _ADULT_WORDS)


def classify(record: JproRecord) -> tuple[Optional[BookClass], Optional[str]]:
    """(novel / manga class, None) or (None, why it is dropped). See the module docstring."""
    if record.deleted:
        return None, "deleted"
    labels = {_label_key(label) for label in record.labels}
    if _ELECTRONIC in labels:
        return None, "electronic"
    if not record.isbn:
        return None, "no-isbn"
    if any(_is_adult(label) for label in record.labels):
        return None, "adult"
    if labels & _EXCLUDED_FORMS:
        return None, "form"
    for code in record.ndcs:
        if code.startswith(MANGA.ndc):
            return MANGA, None
        if code.startswith(NOVEL.ndc):
            return NOVEL, None
    if labels & _NON_FICTION:
        return None, "genre"
    if _MANGA_CONTENT in labels:
        return MANGA, None
    if _NOVEL_CONTENT in labels:
        return NOVEL, None
    if labels & _COMIC_GENRES and not any("児童" in label for label in labels):  # 学習まんが stay out
        return MANGA, None
    return None, "genre"


def release_floor(today: date) -> date:
    """First day of last month: older releases are not kept."""
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def _ndl_record(record: JproRecord) -> NdlRecord:
    """The record in the shape the OpenSearch code reads (a 近刊 record: no NDC, the volume in the title)."""
    parsed = parse_issued(record.issued)
    return NdlRecord(
        title=record.title,
        volume=record.volume,
        series_title=record.series_title,
        edition=record.edition,
        publisher=record.publisher,
        author=",".join(record.authors) or None,
        creators=tuple(key for key in (_creator_key(name) for name in record.names) if key),
        isbn=record.isbn,
        genres=("児童図書",) if any("児童" in label for label in record.labels) else (),
        categories=("図書",),
        issued=record.issued,
        date=parsed[0] if parsed else None,
        precision=parsed[1] if parsed else None,
    )


def _creators(record: NdlRecord) -> list[str]:
    """Names without roles and without the space JPRO puts between 姓 and 名 (NDL's own form)."""
    names = [re.sub(r"\s+", "", name) for name in ndl.creators_of(record)]
    return list(dict.fromkeys(name for name in names if name))


def _entry(record: JproRecord, book: NdlRecord, cls: BookClass) -> db.Entry:
    assert book.isbn and book.date and book.precision
    # the linker splits `extra.jpro` again the same way (`link._upcoming_title`)
    split = split_volume_title(book.title, book.volume)
    series, volume = split.series, split.volume
    ndc = next((code for code in record.ndcs if code.startswith(cls.ndc)), cls.ndc)
    return db.Entry(
        source=ROW_SOURCE,
        external_id=book.isbn,
        kind="book",
        format=cls.format,
        title=ndl.display_title(book.title, book.volume),
        title_reading=record.reading,
        start_date=book.date,
        start_precision=book.precision,
        creators=_creators(book),
        url=record.url,
        cover=None,  # NDL's thumbnails answer 403 to other sites (module docstring)
        extra={
            "isbn": book.isbn,
            "volume": volume,
            "series": series,
            "line": ndl.line_of(book, series),
            "publisher": book.publisher,
            "ndc": ndc,
            "kindHint": cls.hint,
            "via": VIA,
            "jpro": {
                "title": record.title,
                "volume": record.volume,
                "seriesTitle": record.series_title,
                "labels": list(record.labels),
            },
        },
    )


def outcome(record: JproRecord, since: date) -> tuple[Optional[db.Entry], Optional[str]]:
    """(entry, None) for a novel / manga released on or after `since`, else (None, why)."""
    cls, reason = classify(record)
    if cls is None:
        return None, reason
    book = _ndl_record(record)
    reason = exclusion_reason(book, _key(book.title))
    if reason is not None:
        return None, reason
    if any(word in _nfkc(book.title) for word in _EXCLUDED_TITLE_WORDS):
        return None, "excluded-word"
    assert book.date and book.precision
    _, last_day = _date_span(book.date, book.precision)
    if last_day < since:
        return None, "old"
    return _entry(record, book, cls), None


def entries_of(records: Iterable[JproRecord], since: date) -> tuple[list[db.Entry], Counter[str]]:
    """Entries of a page (one per ISBN) and how many records went where (kind or drop reason)."""
    counts: Counter[str] = Counter()
    by_isbn: dict[str, db.Entry] = {}
    for record in records:
        entry, reason = outcome(record, since)
        if entry is None:
            counts[reason or "?"] += 1
            continue
        counts[entry.extra["kindHint"]] += 1
        by_isbn[entry.external_id] = entry
    return list(by_isbn.values()), counts


# -- storage ------------------------------------------------------------------------------------


def _stored_entry(data: dict[str, Any]) -> db.Entry:
    return db.Entry(**{f.name: data[f.name] for f in fields(db.Entry) if f.name in data})


def _merge_stored(conn: sqlite3.Connection, entries: list[db.Entry]) -> list[db.Entry]:
    """What to write for each entry, given the row stored under its id (module docstring)."""
    merged: list[db.Entry] = []
    for offset in range(0, len(entries), 500):
        chunk = entries[offset : offset + 500]
        marks = ", ".join("?" for _ in chunk)
        stored = {
            row["id"]: db.row_to_dict(row)
            for row in conn.execute(f"SELECT * FROM entries WHERE id IN ({marks})", [e.id for e in chunk])
        }
        for entry in chunk:
            data = stored.get(entry.id)
            if data is None:
                merged.append(entry)
                continue
            stored_rank = _PRECISION_RANK.get(data["start_precision"], -1)
            new_rank = _PRECISION_RANK[entry.start_precision]
            if (data.get("extra") or {}).get("via") == VIA:  # an earlier JPRO record of the book
                if stored_rank > new_rank:
                    entry.start_date, entry.start_precision = data["start_date"], data["start_precision"]
                merged.append(entry)
                continue
            kept = _stored_entry(data)  # catalogued: names, line and kind stay
            if new_rank > stored_rank:
                kept.start_date, kept.start_precision = entry.start_date, entry.start_precision
            merged.append(kept)
    return merged


# -- slices -------------------------------------------------------------------------------------


def slice_key(day: date) -> str:
    return f"day:{day.isoformat()}"


def window_days(today: date, *, start: Optional[date] = None) -> list[date]:
    """Days from `today` back to `start` (default: WINDOW_DAYS days in all), newest first."""
    first = start if start is not None else today - timedelta(days=WINDOW_DAYS - 1)
    count = max((today - first).days + 1, 1)
    return [today - timedelta(days=n) for n in range(count)]


def _last_run(conn: sqlite3.Connection) -> Optional[date]:
    row = db.slice_status(conn, SOURCE, RUN_KEY)
    if row is None or row["status"] != "done" or not row["cursor"]:
        return None
    try:
        return date.fromisoformat(row["cursor"])
    except ValueError:
        return None


# -- fetching -----------------------------------------------------------------------------------


async def _fetch(
    client: Any, params: dict[str, str], *, clock: Clock, pacer: Pacer, log: Callable[[str], None]
) -> OaiPage:
    response = await request_with_retry(
        client,
        "GET",
        OAI_URL,
        clock=clock,
        pacer=pacer,
        retries=RETRIES,
        log=log,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise JproError(f"HTTP {response.status_code}")
    return parse_page(response.content)


def _summary(counts: Counter[str]) -> str:
    return ", ".join(f"{name} {count}" for name, count in counts.most_common())


async def _harvest_day(
    conn: sqlite3.Connection,
    client: Any,
    day: date,
    *,
    today: date,
    since: date,
    clock: Clock,
    pacer: Pacer,
    limit: Optional[int],
    result: HarvestResult,
    log: Callable[[str], None],
) -> bool:
    """Harvests one day. Returns False when the run's `limit` was reached."""
    key = slice_key(day)
    params = {
        "verb": "ListRecords",
        "metadataPrefix": METADATA_PREFIX,
        "set": SET_SPEC,
        "from": day.isoformat(),
        "until": day.isoformat(),
    }
    stored = 0
    requests = 0
    total = 0
    counts: Counter[str] = Counter()
    began = clock.monotonic()
    try:
        while True:
            if requests >= MAX_PAGES_PER_SLICE:
                raise JproError(f"more than {MAX_PAGES_PER_SLICE} pages")
            requests += 1
            page = await _fetch(client, params, clock=clock, pacer=pacer, log=log)
            if requests == 1:
                total = page.total
                if total > MAX_DAY_RECORDS:  # measured before anything else is asked
                    message = f"bulk: {total} records in one day (a re-stamp of the whole set), skipped"
                    db.mark_slice(conn, SOURCE, key, "done", fetched=0, message=message)
                    result.slices_done += 1
                    log(f"  ! {key}: {total} records, a bulk re-stamp: skipped")
                    return True
                if total:
                    log(f"    {key}: {total} records, {math.ceil(total / PAGE_SIZE)} pages")
            entries, page_counts = entries_of(page.records, since)
            counts.update(page_counts)
            if limit is not None:
                entries = entries[: max(limit - result.fetched, 0)]
            db.upsert_entries(conn, _merge_stored(conn, entries))
            stored += len(entries)
            result.fetched += len(entries)
            if limit is not None and result.fetched >= limit:
                db.mark_slice(conn, SOURCE, key, "pending", fetched=stored, message="limit")
                return False
            if not page.token:
                break
            params = {"verb": "ListRecords", "resumptionToken": page.token}
    except (JproError, httpx.HTTPError) as exc:
        message = f"{key}: {type(exc).__name__}: {exc}"
        db.mark_slice(conn, SOURCE, key, "error", fetched=stored, message=message[:300])
        result.slices_error += 1
        result.errors.append(message)
        log(f"  ! {message}")
        return True

    if day <= today - timedelta(days=SETTLE_DAYS):
        db.mark_slice(conn, SOURCE, key, "done", fetched=stored, message=_summary(counts) or None)
        result.slices_done += 1
    else:
        db.mark_slice(conn, SOURCE, key, "pending", fetched=stored, message="open: asked again until it is settled")
    if total:
        seconds = clock.monotonic() - began
        log(f"  {key}: {stored} books of {total} records, {requests} requests in {seconds:.0f}s ({_summary(counts)})")
    return True


async def harvest(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    clock: Clock,
    limit: Optional[int] = None,
    log: Callable[[str], None] = print,
    recent: bool = False,
) -> HarvestResult:
    """Upcoming novels / manga registered with JPRO into the `ndl` rows. `limit` caps the rows of this run.

    `recent`: every day from a day before the last finished run on, settled or not.
    """
    result = HarvestResult(source=SOURCE)
    pacer = Pacer(REQUEST_INTERVAL, clock)
    patient = _PatientClient(client, RATE_LIMIT_WAIT)
    today = clock.now().astimezone(JST).date()
    since = release_floor(today)
    last_run = _last_run(conn) if recent else None
    if recent and last_run is None:
        log("  no finished run yet: the whole window, settled days skipped")
    start = last_run - timedelta(days=RECENT_MARGIN_DAYS) if last_run is not None else None
    days = window_days(today, start=start)
    finished = True
    for day in days:
        if limit is not None and result.fetched >= limit:
            finished = False
            break
        if last_run is None and db.is_slice_done(conn, SOURCE, slice_key(day)):
            result.slices_skipped += 1
            continue
        keep_going = await _harvest_day(
            conn,
            patient,
            day,
            today=today,
            since=since,
            clock=clock,
            pacer=pacer,
            limit=limit,
            result=result,
            log=log,
        )
        if not keep_going:
            finished = False
            break
    if finished and result.slices_error == 0:
        db.mark_slice(
            conn,
            SOURCE,
            RUN_KEY,
            "done",
            fetched=result.fetched,
            cursor=today.isoformat(),
            message=f"days {days[-1].isoformat()}..{days[0].isoformat()}",
        )
    return result
