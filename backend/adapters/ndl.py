"""国会図書館サーチ (NDL Search) OpenSearch adapter — paper volumes per series.

One request per series (see specs/data-coverage/design.md, "決定事項"):
`title=<title>&mediatype=books&from=<this year - 2>&cnt=500`, cached 6h
(`refresh` skips the cache). NDL does not return records in date order, so
every record is parsed, filtered and sorted newest-first before the result
is capped.

Filtering — a record is dropped when:
- it has no ISBN, or the ISBN is not Japanese (978-4; translations),
- it is an electronic book (`category`),
- its title, normalized, does not start with the requested title,
- its title / volume / edition / series name holds an excluded word
  (アンソロジー・ファンブック・画集・設定資料・ガイド・カレンダー・ドラマCD・
  付き・特装版・限定版・同梱; hiragana spellings such as ふぁんぶっく count),
- its issued date is a range (`2018.1-2020.1`, sets) or missing.
The same ISBN listed twice (published record + 近刊 record) becomes one,
keeping the more precise date.

Kind — NDC (`dc:subject` NDC10 / NDC9) 726.x is manga and 9xx is a novel;
other NDC classes are ignored. 近刊 records carry no NDC: they are manga when
their genre or creator roles say so (漫画 / 作画), when the title or series
name holds コミック・漫画・まんが・COMIC, or when every creator NDL already
knows from this result appears only on comics (the comic artist of a
comicalization); otherwise they are novels.

Line (系統) — the series name (`dcndl:seriesTitle`) without its number
(`TOジュニア文庫 ; か-1-24` -> `TOジュニア文庫`), else a label written in the
title (`〜文庫`, `〜ジュニア`, `〜ノベルス`, `〜コミックス` ...). A record with
neither joins the line that holds most of the same publisher's labelled
books of that kind (when that line is not a children's line), so a 近刊
record of a label series is not split off; otherwise it is `''` (本編).
Spin-offs (other subtitles) stay in the line of their label. A 近刊 record's label
that is another spelling or a longer / shorter name of a catalogued line of the same
kind and publisher ("モーニング KC" / "モーニングKC", "ヤンマガKCスペシャル" / "ヤンマガKC")
joins that line; a requested line matches either name, spacing, case and
COMICS / コミックス aside. When no line is
requested, the line with the most books is used, children's lines (most
records `dcndl:genre` 児童図書, e.g. ジュニア文庫 re-editions) coming after
general ones.

近刊 from the catalog — `series()` also reads the catalog's JPRO rows (books NDL has
not catalogued yet, `backend.catalog.upcoming`; written by `python -m backend.catalog build
--source jpro`). A row whose title starts with the requested title and whose ISBN the title
search did not return is added as one more 近刊 record (its line comes from its label like any
other record); for an ISBN the search did return, only the kind is taken from the row (JPRO's
genre words) when the record has no NDC. Same ISBN, same volume: nothing is listed twice, and a
catalogued record keeps its NDC kind. Without a catalog nothing changes.

Dates keep their real precision: `2026.8.25` / `2026-08-25` -> day,
`2026.8` / `2026年8月` -> month, `2026` / `[2026]` -> year. For the picked
volumes dated within ±6 months of today with only month/year precision,
openBD is asked once (`/v1/get?isbn=a,b,c`) and a more precise pubdate wins.
Volumes openBD does not know keep their NDL date.

- User-Agent: `triptych/0.1 (personal tracker)`.
- Rate limit: 1 req/s for NDL; openBD has its own limiter.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Iterable, Literal, Mapping, Optional
from urllib.parse import urlencode

import httpx

from ..cache import TTLCache
from ..catalog.text import align_line as _align_line
from ..catalog.text import line_key as _line_key
from ..catalog.upcoming import load as load_upcoming
from ..dto import NdlLine, NdlSeriesDTO, ReleaseDTO, SearchResult, SourceUnavailable
from ..errors import record_adapter_error
from ..ratelimit import TokenBucket
from ..timeutil import DatePrecision, partial_date, today_jst
from . import openbd

logger = logging.getLogger(__name__)

NDL_API_URL = "https://ndlsearch.ndl.go.jp/api/opensearch"
NDL_SEARCH_URL = "https://ndlsearch.ndl.go.jp/search"
USER_AGENT = "triptych/0.1 (personal tracker)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
# cnt=500 の全件取得は数秒かかることがあり、httpx 既定の 5 秒では初回に ReadTimeout になる
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

BOOKS_TTL_SECONDS = 6 * 60 * 60  # 6h
YEARS_BACK = 2  # from=<this year - 2>
MAX_RECORDS = 500  # NDL's maximum `cnt`
MAX_RELEASES = 30
OPENBD_WINDOW = timedelta(days=183)  # ±6 months around today

Kind = Literal["novel", "manga"]
KINDS: tuple[Kind, ...] = ("novel", "manga")
MAIN_LINE_LABEL = "本編"
_MEDIA_TYPE = {"novel": "light-novel", "manga": "manga"}

_rate_limiter = TokenBucket(rate=1.0, capacity=1.0)

_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "openSearch": "http://a9.com/-/spec/opensearchrss/1.0/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
    "dcterms": "http://purl.org/dc/terms/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
_XSI_TYPE = f"{{{_NS['xsi']}}}type"
_ISBN_TYPES = ("dcndl:ISBN13", "dcndl:ISBN")
_NDC_TYPES = ("dcndl:NDC10", "dcndl:NDC9", "dcndl:NDC8")

_PRECISION_RANK = {None: -1, "year": 0, "month": 1, "day": 2}

# -- text normalization -------------------------------------------------------

# Ignored when comparing titles, so "下剋上 : 司書" equals "下剋上～司書～".
_IGNORED = frozenset(
    " \t\r\n:;~〜-‐‑‒–—―・/\\「」『』【】()[]{}<>〈〉《》\"'“”‘’,.、。!?♪☆★"
)
_EDGE_CHARS = " :;~〜-‐―・/"


def _nfkc(text: Optional[str]) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _key(text: Optional[str]) -> str:
    """Comparison key: NFKC, case-folded, separators and punctuation removed."""
    return "".join(c for c in _nfkc(text).casefold() if c not in _IGNORED)


def _fold(text: Optional[str]) -> str:
    """NFKC, upper case, hiragana folded to katakana (for word matching)."""
    upper = _nfkc(text).upper()
    return "".join(
        chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in upper
    )


def _match_prefix(text: str, prefix_key: str) -> Optional[int]:
    """Index in `text` (NFKC) just past `prefix_key` (a `_key()`), or None.

    Separators and punctuation in `text` are skipped while matching.
    """
    if not prefix_key:
        return None
    matched = 0
    for index, ch in enumerate(text):
        if matched >= len(prefix_key):
            return index
        if ch in _IGNORED:
            continue
        folded = ch.casefold()
        if not prefix_key.startswith(folded, matched):
            return None
        matched += len(folded)
    return len(text) if matched >= len(prefix_key) else None


def _tidy(text: str) -> str:
    """Readable fragment: separators collapsed to one space, edges trimmed."""
    collapsed = re.sub(r"\s*[:;~〜]+\s*|\s+", " ", text)
    return collapsed.strip(_EDGE_CHARS)


def _creator_key(raw: Optional[str]) -> str:
    """'香月, 美夜' / '香月美夜' / '香月／美夜' / '伏瀬, 1975-' -> one key."""
    parts = re.split(r"[,，/／]", _nfkc(raw))
    kept = [p for p in parts if p.strip() and not re.search(r"\d", p)]
    return re.sub(r"\s+", "", "".join(kept))


def _isbn13(raw: Optional[str]) -> Optional[str]:
    """Hyphen-free ISBN-13 (ISBN-10 converted), or None when malformed."""
    digits = re.sub(r"[^0-9Xx]", "", _nfkc(raw)).upper()
    if len(digits) == 10:
        core = "978" + digits[:9]
        if not core.isdigit():
            return None
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
        digits = core + str((10 - total % 10) % 10)
    try:
        return openbd.validate_isbn13(digits)
    except ValueError:
        return None


# -- dates ----------------------------------------------------------------------

_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$")
_DOTTED_DATE = re.compile(r"^(\d{4})(?:\.(\d{1,2})(?:\.(\d{1,2}))?)?$")
_KANJI_DATE = re.compile(r"^(\d{4})年(?:(\d{1,2})月(?:(\d{1,2})日)?)?$")


def parse_issued(raw: Optional[str]) -> Optional[tuple[str, DatePrecision]]:
    """Parses `dcterms:issued` into (YYYY-MM-DD, precision).

    '2026.8.25' / '2026-08-25' -> day, '2026.8' / '2024年12月' -> month,
    '2026' / '[2026]' -> year. Ranges ('2018.1-2020.1', '2019.-') and
    anything else return None.
    """
    if not raw:
        return None
    text = _nfkc(raw).strip().strip("[]()").strip().rstrip(".")
    text = re.sub(r"^[cp](?=\d{4})", "", text)  # 'c2019' (copyright year)
    for pattern in (_ISO_DATE, _DOTTED_DATE, _KANJI_DATE):
        match = pattern.match(text)
        if match:
            year, month, day = (int(g) if g else None for g in match.groups())
            return partial_date(year, month, day)
    return None


def _date_span(value: str, precision: str) -> tuple[date, date]:
    start = date.fromisoformat(value)
    if precision == "day":
        return start, start
    if precision == "month":
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    return date(start.year, 1, 1), date(start.year, 12, 31)


def _near(value: str, precision: str, today: date) -> bool:
    """True when the (partial) date overlaps today ± 6 months."""
    start, end = _date_span(value, precision)
    return start <= today + OPENBD_WINDOW and end >= today - OPENBD_WINDOW


def _format_year_month(value: str, precision: str) -> str:
    year, month = int(value[:4]), int(value[5:7])
    return f"{year}年" if precision == "year" else f"{year}年{month}月"


# -- parsing ----------------------------------------------------------------------


@dataclass(frozen=True)
class NdlRecord:
    """One NDL Search item, normalized (NFKC) but not yet filtered."""

    title: str
    volume: Optional[str] = None
    series_title: Optional[str] = None
    edition: Optional[str] = None
    publisher: Optional[str] = None
    author: Optional[str] = None  # <author>: names with roles (原作 / 漫画 / イラスト)
    creators: tuple[str, ...] = ()  # dc:creator, normalized by _creator_key
    isbn: Optional[str] = None  # ISBN-13 of any country
    ndc: Optional[str] = None
    genres: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    issued: Optional[str] = None  # raw dcterms:issued (or dc:date)
    date: Optional[str] = None
    precision: Optional[DatePrecision] = None


def _text(item: ET.Element, path: str) -> Optional[str]:
    element = item.find(path, _NS)
    if element is None or not element.text or not element.text.strip():
        return None
    return _nfkc(element.text).strip()


def _typed(item: ET.Element, path: str, types: tuple[str, ...]) -> Optional[str]:
    """First non-empty `path` element whose xsi:type is in `types` (in order)."""
    found = {
        el.get(_XSI_TYPE): el.text.strip()
        for el in reversed(item.findall(path, _NS))
        if el.get(_XSI_TYPE) in types and el.text and el.text.strip()
    }
    for kind in types:
        if kind in found:
            return found[kind]
    return None


def _parse_item(item: ET.Element) -> Optional[NdlRecord]:
    title = _text(item, "dc:title") or _text(item, "title")
    if not title:
        return None

    isbn = None
    for identifier in item.findall("dc:identifier", _NS):
        if identifier.get(_XSI_TYPE) in _ISBN_TYPES and identifier.text:
            isbn = _isbn13(identifier.text)
            if isbn:
                break

    issued = _text(item, "dcterms:issued") or _text(item, "dc:date")
    parsed = parse_issued(issued)
    return NdlRecord(
        title=title,
        volume=_text(item, "dcndl:volume"),
        series_title=_text(item, "dcndl:seriesTitle"),
        edition=_text(item, "dcndl:edition"),
        publisher=_text(item, "dc:publisher"),
        author=_text(item, "author"),
        creators=tuple(
            key
            for key in (_creator_key(el.text) for el in item.findall("dc:creator", _NS))
            if key
        ),
        isbn=isbn,
        ndc=_typed(item, "dc:subject", _NDC_TYPES),
        genres=tuple(
            _nfkc(el.text).strip() for el in item.findall("dcndl:genre", _NS) if el.text
        ),
        categories=tuple(
            _nfkc(el.text).strip() for el in item.findall("category") if el.text
        ),
        issued=issued,
        date=parsed[0] if parsed else None,
        precision=parsed[1] if parsed else None,
    )


def parse_records(xml_bytes: bytes) -> list[NdlRecord]:
    """Parses an NDL Search OpenSearch RSS payload (pure, no filtering).

    Items without a title are skipped. Raises SourceUnavailable on bad XML.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SourceUnavailable("ndl", f"invalid XML response: {exc}") from exc

    channel = root.find("channel")
    if channel is None:
        return []
    records = []
    for item in channel.findall("item"):
        record = _parse_item(item)
        if record is not None:
            records.append(record)
    return records


# -- filtering --------------------------------------------------------------------

_EXCLUDED_WORDS = (
    "アンソロジー",
    "ファンブック",
    "画集",
    "設定資料",
    "ガイド",
    "カレンダー",
    "ドラマCD",
    "特装版",
    "限定版",
    "同梱",
)
_EXCLUDED_RE = re.compile(
    "|".join(re.escape(_fold(word)) for word in _EXCLUDED_WORDS)
    + "|"
    + re.escape(_fold("付き"))
    + "(?!合)"  # 付き合う is a word, not a bundled extra
)
_COMIC_WORD_RE = re.compile("|".join(re.escape(_fold(w)) for w in ("コミック", "漫画", "まんが", "COMIC")))
_MANGA_ROLE_RE = re.compile("|".join(re.escape(_fold(w)) for w in ("漫画", "まんが", "作画", "コミカライズ")))


def exclusion_reason(record: NdlRecord, title_key: str) -> Optional[str]:
    """Why `record` is not a volume of the series titled `title_key`, or None."""
    if not record.isbn:
        return "no-isbn"
    if not record.isbn.startswith("9784"):
        return "foreign-isbn"
    if any("電子" in category for category in record.categories):
        return "electronic"
    if _match_prefix(record.title, title_key) is None:
        return "title"
    words = " ".join(
        filter(None, (record.title, record.volume, record.edition, record.series_title))
    )
    if _EXCLUDED_RE.search(_fold(words)):
        return "excluded-word"
    if not record.date:
        return "date"
    return None


def _merge(a: NdlRecord, b: NdlRecord) -> NdlRecord:
    """Two records of the same ISBN: keep the catalogued one, the finer date."""
    primary, other = (a, b) if (a.ndc or not b.ndc) else (b, a)
    merged = replace(
        primary,
        volume=primary.volume or other.volume,
        series_title=primary.series_title or other.series_title,
        edition=primary.edition or other.edition,
        publisher=primary.publisher or other.publisher,
        author=primary.author or other.author,
        creators=tuple(dict.fromkeys(primary.creators + other.creators)),
        ndc=primary.ndc or other.ndc,
        genres=tuple(dict.fromkeys(primary.genres + other.genres)),
    )
    if _PRECISION_RANK[other.precision] > _PRECISION_RANK[primary.precision]:
        merged = replace(
            merged, date=other.date, precision=other.precision, issued=other.issued
        )
    return merged


def _dedupe(records: Iterable[NdlRecord]) -> list[NdlRecord]:
    by_isbn: dict[str, NdlRecord] = {}
    for record in records:
        assert record.isbn
        existing = by_isbn.get(record.isbn)
        by_isbn[record.isbn] = record if existing is None else _merge(existing, record)
    return list(by_isbn.values())


# -- kind / line / label --------------------------------------------------------------


def _ndc_kind(ndc: Optional[str]) -> Optional[str]:
    if not ndc:
        return None
    if ndc.startswith("726"):
        return "manga"
    if ndc.startswith("9"):
        return "novel"
    return "other"


def _classifier(records: list[NdlRecord], known: Optional[Mapping[str, Kind]] = None):
    """Builds kind(record) using creators already classified by NDC.

    `known`: kinds the catalog read from JPRO's genre words, by ISBN; they beat the guess
    for a record without NDC (a catalogued record keeps its NDC kind).
    """
    known_kinds = known or {}
    novel_creators: set[str] = set()
    manga_creators: set[str] = set()
    for record in records:
        kind = _ndc_kind(record.ndc)
        if kind == "novel":
            novel_creators.update(record.creators)
        elif kind == "manga":
            manga_creators.update(record.creators)
    known = novel_creators | manga_creators
    manga_only = manga_creators - novel_creators

    def kind_of(record: NdlRecord) -> Optional[Kind]:
        kind = _ndc_kind(record.ndc)
        if kind == "other":
            return None
        if kind is not None:
            return kind  # type: ignore[return-value]
        if record.isbn in known_kinds:
            return known_kinds[record.isbn]
        if any("漫画" in genre for genre in record.genres):
            return "manga"
        if _MANGA_ROLE_RE.search(_fold(record.author)):
            return "manga"
        if _COMIC_WORD_RE.search(_fold(f"{record.title} {record.series_title or ''}")):
            return "manga"
        seen = [c for c in record.creators if c in known]
        if seen and all(c in manga_only for c in seen):
            return "manga"
        return "novel"

    return kind_of


_LABEL_BOUNDARY = r"\s()\[\]「」『』【】:;"
_TITLE_LABEL_RE = re.compile(
    rf"(?<![^{_LABEL_BOUNDARY}])"
    rf"([^{_LABEL_BOUNDARY}]+?(?:文庫|ジュニア|ノベルス|ノベルズ|NOVELS|コミックス|COMICS))"
    rf"(?=[{_LABEL_BOUNDARY}]|$)",
    re.IGNORECASE,
)


def _series_name(series_title: Optional[str]) -> str:
    """'TOジュニア文庫 ; か-1-24' -> 'TOジュニア文庫'."""
    if not series_title:
        return ""
    return re.sub(r"\s+", " ", series_title.split(";")[0]).strip()


def _title_label(remainder: str) -> Optional[str]:
    match = _TITLE_LABEL_RE.search(remainder)
    return match.group(1) if match else None


def _dominant_subtitle(remainders: Iterable[str]) -> str:
    """The subtitle most volumes share (the series' own one), as a key.

    "本好きの下剋上 : 司書になるためには手段を選んでいられません" is the
    main series; stripping its subtitle leaves labels like "第3部[10]" while
    spin-offs keep theirs ("ハンネローレの貴族院五年生 1").
    """
    counts = Counter(_key(r) for r in remainders)
    if not counts:
        return ""
    best, count = max(counts.items(), key=lambda kv: (kv[1], kv[0] == ""))
    return best if best and count >= 2 else ""


def _strip_subtitle(remainder: str, subtitle_key: str) -> str:
    if not subtitle_key:
        return remainder
    end = _match_prefix(remainder, subtitle_key)
    if end is None:
        return remainder
    if end < len(remainder) and remainder[end] not in _IGNORED:
        return remainder  # the subtitle is only the start of a longer word
    return remainder[end:]


def _label(record: NdlRecord, remainder: str, line_token: Optional[str]) -> str:
    """Readable volume name, e.g. '第3部[10]', 'ハンネローレの貴族院五年生 1',
    '第三部 領主の養女10' (近刊 records carry the volume in the title)."""
    if line_token:
        remainder = remainder.replace(line_token, " ")
        remainder = re.sub(r"[(\[]\s*[)\]]", " ", remainder)
    rest = _tidy(remainder)
    volume = _tidy(record.volume or "")
    if rest and volume:
        numbers = re.findall(r"\d+", volume)
        core = rest.rstrip(" 」』)]】>〉》\"'")
        if numbers and re.search(rf"(?<!\d){numbers[-1]}$", core):
            return rest
        return f"{rest} {volume}"
    return rest or volume or record.title


@dataclass(frozen=True)
class _Book:
    record: NdlRecord
    kind: Kind
    line: str
    children_line: bool
    label: str
    own_line: Optional[str] = None  # a 近刊 record's label as it states it, when it joined another spelling

    @property
    def isbn(self) -> str:
        assert self.record.isbn
        return self.record.isbn

    @property
    def date(self) -> str:
        assert self.record.date
        return self.record.date

    @property
    def precision(self) -> DatePrecision:
        assert self.record.precision
        return self.record.precision


def _newest_first(book: _Book) -> tuple[str, int, str]:
    return (book.date, _PRECISION_RANK[book.precision], book.isbn)


def _is_children(record: NdlRecord) -> bool:
    return any("児童" in genre for genre in record.genres)


def _mostly_children(members: list[NdlRecord]) -> bool:
    return sum(_is_children(r) for r in members) * 2 > len(members)


@dataclass(frozen=True)
class UpcomingRecord:
    """A 近刊 volume the catalog knows from JPRO, with the kind its genre words give."""

    record: NdlRecord
    kind: Kind


def analyze(
    xml_bytes: bytes, title: str, upcoming: Iterable[UpcomingRecord] = ()
) -> list[_Book]:
    """Every volume of `title` in an NDL payload, with kind, line and label.

    `upcoming` (catalog JPRO rows) adds the volumes the payload lacks and gives the kind of
    the ones it has (see the module docstring).
    """
    title_key = _key(title)
    if not title_key:
        return []

    parsed = parse_records(xml_bytes)
    listed = {r.isbn for r in parsed if r.isbn}
    extra = [u for u in upcoming if u.record.isbn]
    records = _dedupe(
        [
            *(r for r in parsed if exclusion_reason(r, title_key) is None),
            *(
                u.record
                for u in extra
                if u.record.isbn not in listed and exclusion_reason(u.record, title_key) is None
            ),
        ]
    )
    kind_of = _classifier(records, {u.record.isbn: u.kind for u in extra})  # type: ignore[misc]

    remainders: dict[str, str] = {}
    for record in records:
        end = _match_prefix(record.title, title_key)
        remainders[record.isbn] = record.title[end:] if end is not None else ""  # type: ignore[index]
    subtitle_key = _dominant_subtitle(remainders.values())

    # Kind and the line each record states itself (series name / title label).
    staged: list[tuple[NdlRecord, Kind, Optional[str], Optional[str]]] = []
    for record in records:
        kind = kind_of(record)
        if kind is None:
            continue
        remainder = remainders[record.isbn]  # type: ignore[index]
        token = None
        stated = _series_name(record.series_title) or None
        if stated is None:
            token = _title_label(remainder)
            stated = token
        if stated is not None and _match_prefix(stated, title_key) is not None:
            stated = ""  # the "series" is the work itself
        staged.append((record, kind, stated, token))

    # 近刊 records (no NDC) often spell the label another way than NDL's catalogue ("モーニング KC" /
    # "モーニングKC", "ヤンマガKCスペシャル" / "ヤンマガKC", "花とゆめコミックス" / "花とゆめCOMICS"):
    # such a label joins the catalogued line of the same kind and publisher it is a variant of, so the
    # upcoming volume lands in the line its series already has. The stated label is kept (`own`).
    catalogued: dict[tuple[str, str], Counter[str]] = {}
    for record, kind, stated, _ in staged:
        if record.ndc and stated:
            catalogued.setdefault((kind, _key(record.publisher)), Counter())[stated] += 1
    own_lines: dict[str, str] = {}
    for index, (record, kind, stated, token) in enumerate(staged):
        lines = catalogued.get((kind, _key(record.publisher)))
        if record.ndc or not stated or not lines:
            continue
        aligned = _align_line(stated, lines)
        if aligned != stated:
            own_lines[record.isbn] = stated  # type: ignore[index]
            staged[index] = (record, kind, aligned, token)

    # Lines whose books are mostly children's editions (ジュニア文庫 ...).
    line_members: dict[tuple[str, str], list[NdlRecord]] = {}
    for record, kind, stated, _ in staged:
        if stated is not None:
            line_members.setdefault((kind, stated), []).append(record)
    children_lines = {
        key for key, members in line_members.items() if _mostly_children(members)
    }

    # Unlabelled records join the same publisher's main general line, else 本編.
    labelled_by_publisher: dict[tuple[str, str], Counter[str]] = {}
    for record, kind, stated, _ in staged:
        if stated:
            pub = (kind, _key(record.publisher))
            labelled_by_publisher.setdefault(pub, Counter())[stated] += 1

    resolved: list[tuple[NdlRecord, Kind, str, Optional[str]]] = []
    for record, kind, stated, token in staged:
        line = stated
        if line is None:
            line = ""
            counter = labelled_by_publisher.get((kind, _key(record.publisher)))
            if counter:
                top, count = counter.most_common(1)[0]
                if count * 2 > sum(counter.values()) and (kind, top) not in children_lines:
                    line = top
        resolved.append((record, kind, line, token))

    # A line's children flag counts the records that joined it, too.
    final_members: dict[tuple[str, str], list[NdlRecord]] = {}
    for record, kind, line, _ in resolved:
        final_members.setdefault((kind, line), []).append(record)
    children_lines = {
        key for key, members in final_members.items() if _mostly_children(members)
    }

    books: list[_Book] = []
    for record, kind, line, token in resolved:
        remainder = _strip_subtitle(remainders[record.isbn], subtitle_key)  # type: ignore[index]
        books.append(
            _Book(
                record=record,
                kind=kind,
                line=line,
                children_line=(kind, line) in children_lines,
                label=_label(record, remainder, token),
                own_line=own_lines.get(record.isbn),  # type: ignore[arg-type]
            )
        )
    return books


def _line_label(key: str) -> str:
    return key or MAIN_LINE_LABEL


def _summarize_lines(books: list[_Book]) -> list[NdlLine]:
    counts = Counter(book.line for book in books)
    return [
        NdlLine(key=key, label=_line_label(key), count=count)
        for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _default_line(books: list[_Book]) -> str:
    """Most books wins; children's lines only when there is nothing else."""
    if not books:
        return ""
    groups: dict[str, list[_Book]] = {}
    for book in books:
        groups.setdefault(book.line, []).append(book)

    def score(item: tuple[str, list[_Book]]):
        key, members = item
        latest = max(_newest_first(b) for b in members)
        return (not members[0].children_line, len(members), latest, key == "")

    return max(groups.items(), key=score)[0]


# -- fetching -----------------------------------------------------------------------

_DEFAULT_CACHE: Any = object()
_shared_cache: Optional[TTLCache] = None


def default_cache() -> TTLCache:
    """The collector-wide response cache, opened on first use."""
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = TTLCache()
    return _shared_cache


def _resolve_cache(cache: Any) -> Optional[TTLCache]:
    return default_cache() if cache is _DEFAULT_CACHE else cache


def _today(today: Optional[date]) -> date:
    return today or date.fromisoformat(today_jst())


def query_params(title: str, today: date) -> dict[str, str]:
    return {
        "title": title,
        "mediatype": "books",
        "from": str(today.year - YEARS_BACK),
        "cnt": str(MAX_RECORDS),
    }


async def _fetch_books_xml(
    title: str,
    *,
    client: httpx.AsyncClient,
    cache: Optional[TTLCache],
    refresh: bool,
    today: date,
) -> bytes:
    params = query_params(title, today)
    cache_key = f"ndl:books:v2:{params['from']}:{title}"
    if cache is not None and not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached.encode("utf-8")

    await _rate_limiter.acquire()
    try:
        response = await client.get(NDL_API_URL, params=params, headers=REQUEST_HEADERS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceUnavailable("ndl", str(exc) or type(exc).__name__) from exc

    xml_bytes = response.content
    parse_records(xml_bytes)  # raises on a broken payload, which is not cached
    if cache is not None:
        cache.set(cache_key, xml_bytes.decode("utf-8"), BOOKS_TTL_SECONDS)
    return xml_bytes


def _to_release(book: _Book) -> ReleaseDTO:
    return ReleaseDTO(
        kind="volume",
        label=book.label,
        date=book.date,
        precision=book.precision,
        source="ndl",
        externalId=book.isbn,
    )


async def _sharpen_dates(
    releases: list[ReleaseDTO],
    *,
    client: httpx.AsyncClient,
    cache: Optional[TTLCache],
    refresh: bool,
    today: date,
) -> list[ReleaseDTO]:
    """Asks openBD once for near volumes known only to the month/year."""
    targets = [
        r.externalId
        for r in releases
        if r.externalId and r.precision != "day" and _near(r.date, r.precision, today)
    ]
    if not targets:
        return releases
    try:
        found = await openbd.books(targets, client=client, cache=cache, refresh=refresh)
    except SourceUnavailable as exc:
        logger.warning("openBD lookup failed, keeping NDL dates: %s", exc.message)
        record_adapter_error(exc.source, exc.message)
        return releases

    sharpened = []
    for release in releases:
        book = found.get(release.externalId or "")
        if (
            book is not None
            and book.pubdate
            and _PRECISION_RANK[book.pubdatePrecision] > _PRECISION_RANK[release.precision]
        ):
            release = release.model_copy(
                update={"date": book.pubdate, "precision": book.pubdatePrecision}
            )
        sharpened.append(release)
    return sharpened


_DEFAULT_UPCOMING: Any = object()


def _in_line(book: _Book, line: str) -> bool:
    """`book` is in the requested line: its line or, for a 近刊 that joined another spelling, its own."""
    wanted = _line_key(line)
    return _line_key(book.line) == wanted or (
        book.own_line is not None and _line_key(book.own_line) == wanted
    )


def catalog_upcoming(title: str) -> list[UpcomingRecord]:
    """The catalog's JPRO volumes whose title starts with `title` (none without a catalog)."""
    title_key = _key(title)
    if not title_key:
        return []
    try:
        books = load_upcoming()
    except Exception as exc:  # the catalog is a bonus: never fail a sync over it
        logger.warning("catalog upcoming rows unreadable: %s", exc)
        return []
    found = []
    for book in books:
        if _match_prefix(_nfkc(book.title), title_key) is None:
            continue
        record = NdlRecord(
            title=_nfkc(book.title).strip(),
            volume=book.volume,
            series_title=book.series_title,
            publisher=book.publisher,
            creators=tuple(key for key in (_creator_key(name) for name in book.creators) if key),
            isbn=book.isbn,
            genres=("児童図書",) if book.children else (),
            categories=("図書",),
            issued=book.date,
            date=book.date,
            precision=book.precision,  # type: ignore[arg-type]
        )
        found.append(UpcomingRecord(record=record, kind=book.kind))  # type: ignore[arg-type]
    return found


def _resolve_upcoming(upcoming: Any, title: str) -> list[UpcomingRecord]:
    if upcoming is _DEFAULT_UPCOMING:
        return catalog_upcoming(title)
    return list(upcoming or ())


async def series(
    title: str,
    kind: Kind,
    line: Optional[str] = None,
    refresh: bool = False,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Any = _DEFAULT_CACHE,
    today: Optional[date] = None,
    upcoming: Any = _DEFAULT_UPCOMING,
) -> NdlSeriesDTO:
    """New-volume candidates of one series, for one kind (novel / manga).

    `line` picks the 系統 (`''` = 本編); without it the default line is used
    (see the module docstring). Releases are the picked line's volumes,
    newest first, at most 30. `cache` defaults to the collector cache;
    pass None to disable caching. `upcoming` defaults to the catalog's JPRO
    rows (`catalog_upcoming`); pass None to leave them out.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    title = title.strip()
    today = _today(today)
    cache = _resolve_cache(cache)

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    try:
        xml_bytes = await _fetch_books_xml(
            title, client=http, cache=cache, refresh=refresh, today=today
        )
        extra = _resolve_upcoming(upcoming, title)
        kind_books = [b for b in analyze(xml_bytes, title, extra) if b.kind == kind]
        chosen = line if line is not None else _default_line(kind_books)
        picked = sorted(
            (b for b in kind_books if _in_line(b, chosen)), key=_newest_first, reverse=True
        )[:MAX_RELEASES]
        releases = await _sharpen_dates(
            [_to_release(b) for b in picked],
            client=http,
            cache=cache,
            refresh=refresh,
            today=today,
        )
    finally:
        if owns_client:
            await http.aclose()

    releases.sort(
        key=lambda r: (r.date, _PRECISION_RANK[r.precision], r.externalId or ""),
        reverse=True,
    )
    return NdlSeriesDTO(lines=_summarize_lines(kind_books), line=chosen, releases=releases)


# -- search ---------------------------------------------------------------------------


def _common_series_name(books: list[_Book], title_key: str) -> str:
    """Shared leading title of a group ('本好きの下剋上'), cut at a word edge."""
    titles = [b.record.title for b in books]
    prefix = titles[0]
    for other in titles[1:]:
        cut = 0
        while cut < min(len(prefix), len(other)) and prefix[cut] == other[cut]:
            cut += 1
        prefix = prefix[:cut]
    name = prefix.rstrip(_EDGE_CHARS + "(「『【[")
    whole = any(t.rstrip(_EDGE_CHARS) == name for t in titles)
    at_edge = all(len(t) == len(name) or t[len(name)] in _IGNORED for t in titles)
    if name and _match_prefix(name, title_key) is not None and (whole or at_edge):
        return name
    end = _match_prefix(titles[0], title_key)
    return titles[0][:end].rstrip(_EDGE_CHARS) if end else titles[0]


def _search_url(name: str, line: str) -> str:
    keyword = f"{name} {line}".strip()
    return f"{NDL_SEARCH_URL}?{urlencode({'cs': 'bib', 'keyword': keyword})}"


async def search_books(
    q: str,
    refresh: bool = False,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache: Any = _DEFAULT_CACHE,
    today: Optional[date] = None,
) -> list[SearchResult]:
    """Paper books whose title starts with `q`, one result per (kind, line).

    Same request, cache and filtering as `series()`. `title` is the series
    name; `externalId` is "<kind>:<line>:<series name>" so the novel, manga
    and e.g. ジュニア文庫 lines of one series stay distinct (the sync engine
    reads the line back from it); `subtitle` is 出版社・(系統・)最新巻・発行年月.
    """
    q = q.strip()
    title_key = _key(q)
    if not title_key:
        return []
    today = _today(today)
    cache = _resolve_cache(cache)

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    try:
        xml_bytes = await _fetch_books_xml(
            q, client=http, cache=cache, refresh=refresh, today=today
        )
    finally:
        if owns_client:
            await http.aclose()

    groups: dict[tuple[str, str], list[_Book]] = {}
    for book in analyze(xml_bytes, q):
        groups.setdefault((book.kind, book.line), []).append(book)

    ranked = sorted(
        groups.items(),
        key=lambda item: (len(item[1]), max(_newest_first(b) for b in item[1])),
        reverse=True,
    )
    results: list[SearchResult] = []
    for (kind, line), books in ranked:
        latest = max(books, key=_newest_first)
        name = _common_series_name(books, title_key)
        publisher = Counter(b.record.publisher for b in books if b.record.publisher)
        parts = [
            publisher.most_common(1)[0][0] if publisher else None,
            line or None,
            latest.label,
            _format_year_month(latest.date, latest.precision),
        ]
        results.append(
            SearchResult(
                source="ndl",
                externalId=f"{kind}:{line}:{name}",
                mediaType=_MEDIA_TYPE[kind],  # type: ignore[arg-type]
                title=name,
                url=_search_url(name, line),
                subtitle="・".join(p for p in parts if p),
            )
        )
    return results
