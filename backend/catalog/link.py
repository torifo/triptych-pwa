"""Linker: cross-source links, works (clusters), list summaries and the search index.

`run_link(conn, today=...)` rebuilds `links`, `clusters`, `cluster_summary` and
`entries_fts` from `entries` / `relations` in one transaction (and mends the series / volume
of JPRO rows, below).

1. Title matching ties every non-AniList row to at most one AniList entry
   (syoboi -> anime, narou / kakuyomu -> novel or manga, ndl -> novel or manga).
2. Works are made with union-find over the merge relations
   (ADAPTATION / SOURCE / SEQUEL / PREQUEL) and the links whose
   confidence is >= MERGE_THRESHOLD. Weaker links stay in `links` as candidates.
3. NDL books that joined no work are grouped per (series, kindHint, line). A JPRO book
   (`extra.via = "jpro"`, not catalogued yet) joins its series' catalogued shelf when its
   label is a variant of that shelf's line, or when the series has only that one shelf,
   unless the authors of both sides differ. Otherwise it joins the work of a catalogued book,
   a Web novel or an AniList novel / manga titled like it (below) whose authors overlap
   with its own, else a shelf of JPRO books.
4. `cluster_id` is the smallest member id, so it is stable across runs.

A JPRO book's series and volume are split again from the record's own title (`extra.jpro`,
`text.split_volume_title`) and written back to `extra.series` / `extra.volume` when they differ,
so `link` alone mends rows an older harvester stored. Its own title is the title without the
volume, notes such as "(コミック)" / "〈完全版〉" kept (NDL names those series "… : コミック").
A book whose own title names a catalogued shelf follows those volumes. One without also
matches by further forms, which only count when the authors overlap: the title without its
notes, the series cut before the text after its volume ("山、買いました10 ~…~" -> "山、買いました")
and the heads before a subtitle, a volume title or " : " ("仏の辰捕物帳 老いた掏摸" -> "仏の辰捕物帳").

A catalogued book whose series matches no AniList entry of its kind well enough also matches, with
the authors overlapping, by its series without the words of a comic ("… : コミック", "…@COMIC",
"… : THE COMIC") and the heads of that ("十番目のユナス : 呪いの伯爵と死なない生贄" -> "十番目のユナス"),
and the other kind by those keys (not by synonyms): the comic of a novel joins the novel, the novel the
comic. A volume without authors uses its shelf's. Authors written in the other order ("とち うえやま" /
"うえやまとち") are the same, and between entries of one title the one sharing more authors wins. A JPRO
book whose catalogued volumes all joined a work follows them there.
"""

from __future__ import annotations

import bisect
import calendar
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable, Optional

from .text import (
    VolumeTitle,
    align_line,
    normalize_title,
    search_text,
    split_volume_title,
    strip_adaptation,
    strip_notes,
    strip_season,
    strip_subtitle,
    title_key,
    volume_label,
)

JPRO_VIA = "jpro"  # extra.via of an NDL row the JPRO harvester wrote (not catalogued yet)
MERGE_THRESHOLD = 0.8
# PARENT は SIDE_STORY / SPIN_OFF の逆向き。統合に使うと共通の特番を介して別シリーズがつながる
MERGE_RELATIONS = ("ADAPTATION", "SOURCE", "SEQUEL", "PREQUEL")

# syoboi <-> AniList anime
CONF_TITLE_YEAR = 0.95
CONF_TITLE_ONLY = 0.7
SYNONYM_PENALTY = 0.1  # the match went through a synonym (略称 / AniList synonyms)
YEAR_WINDOW = 1
# narou / kakuyomu <-> AniList novel (else manga)
CONF_WEB_TITLE = 0.9
CONF_WEB_SYNONYM = 0.85
CONF_WEB_PREFIX = 0.6
PREFIX_MIN = 8  # the shorter key of a prefix match must be at least this long
BASE_KEY_MIN = 4  # subtitle-stripped keys shorter than this are too generic to match on
# ndl book <-> AniList novel / manga
CONF_SERIES = 0.85
# 部・巻ごとに分かれた AniList 原作（「本好きの下剋上 ~…~ 第一部」）と副題を除いた題名で一致
CONF_BASE = 0.82
BASE_INDEX_MIN = 6  # 副題を除いた AniList 側のキーはこれ以上の長さだけ索引する
# 作者で裏を取る: 一致すれば上げ、両方に作者がいて一致しない短い題名・副題除去の一致は候補止まりにする
CONF_AUTHOR_MATCH = 0.95
CONF_AUTHOR_MISMATCH = 0.6
SHORT_TITLE_KEY = 10  # 「約束」「短編集」のような短い題名は偶然の一致が多い
# AniList の名前は「名 姓」の順のことがある（「とち うえやま」/「うえやまとち」）。2文字は「田中」/「中田」で別人
NAME_ROTATION_MIN = 3
# 近刊（JPRO）の本体: 副題・巻の題の前までの頭や、巻の後ろの語を落としたシリーズ名は作者が重なる時だけ結ぶ
CONF_HEAD = 0.9
HEAD_KEY_MIN = BASE_KEY_MIN  # 頭のキーはこれ以上の長さだけ使う
# 目録済みの本: 漫画化の印（「 : コミック」「@COMIC」）を外した本体・その頭、もう一方の種類（漫画化した漫画・
# 原作の小説）は作者が重なる時だけ結ぶ。これより短いキー（「銀の匙」「こころ」）は使わない
BODY_KEY_MIN = HEAD_KEY_MIN
# 本体で結ぶ先が複数ある時: 長いキー、AniList、目録済みの本、Web 小説の順
JOIN_SOURCE_RANK = {"anilist": 0, "ndl": 1, "narou": 2, "kakuyomu": 2}

SYNONYM_KEY_MIN = 2  # very short synonyms ("S2", "劇") collide across unrelated works

METHOD_TITLE_YEAR = "title-year"
METHOD_TITLE_EXACT = "title-exact"
METHOD_SYNONYM = "synonym"
METHOD_PREFIX = "title-prefix"
METHOD_SERIES = "series"
METHOD_BASE = "base-title"

KIND_ORDER = ("anime", "manga", "novel", "web-novel", "book")
# 何かの翻案ではない原作（漫画・小説の代表題名に優先する）
ORIGINAL_SOURCES = frozenset({None, "ORIGINAL", "WEB_NOVEL", "OTHER"})
ANIME_STATUS_RANK = {"RELEASING": 5, "NOT_YET_RELEASED": 4, "HIATUS": 3, "FINISHED": 2, "CANCELLED": 1}
SEASON_ORDER = {"WINTER": 0, "SPRING": 1, "SUMMER": 2, "FALL": 3}
PRECISION_RANK = {"day": 0, "month": 1, "year": 2}
# A novel adapted from one of these is a novelization, not the original work.
NOVELIZATION_SOURCES = frozenset(
    {"MANGA", "ANIME", "VISUAL_NOVEL", "VIDEO_GAME", "GAME", "LIVE_ACTION", "COMIC", "MULTIMEDIA_PROJECT", "PICTURE_BOOK"}
)

_FAR = 10**9  # date distance when a date is missing

_ENTRY_SQL = (
    "SELECT id, source, kind, title, title_native, title_romaji, title_english, title_reading, "
    "synonyms, status, start_date, start_precision, season, season_year, popularity, cover, "
    "source_material, extra, creators FROM entries ORDER BY id"
)


# --------------------------------------------------------------------------- rows


class _Row:
    __slots__ = (
        "idx",
        "id",
        "source",
        "kind",
        "title",
        "native",
        "romaji",
        "english",
        "reading",
        "synonyms",
        "status",
        "start",
        "precision",
        "season",
        "season_year",
        "popularity",
        "cover",
        "source_material",
        "extra",
        "creators",
        "book",
        "stored",
    )

    def __init__(self, idx: int, record: tuple[Any, ...]) -> None:
        (
            self.id,
            self.source,
            self.kind,
            self.title,
            self.native,
            self.romaji,
            self.english,
            self.reading,
            synonyms,
            self.status,
            start_date,
            start_precision,
            self.season,
            self.season_year,
            self.popularity,
            self.cover,
            self.source_material,
            extra,
            creators,
        ) = record
        self.idx = idx
        self.title = self.title or ""
        self.synonyms = _json_list(synonyms)
        self.start, self.precision = parse_partial_date(start_date, start_precision)
        self.extra = _extra(self.source, self.kind, extra)
        self.creators = _creator_keys(_json_list(creators))
        # a JPRO book: the title split again; `stored` is what the row holds (written back when it differs)
        self.book: Optional[VolumeTitle] = _upcoming_title(self.extra) if self.source == "ndl" else None
        self.stored: Optional[tuple[Any, Any]] = None
        if self.book is not None:
            self.stored = (self.extra.get("series"), self.extra.get("volume"))
            self.extra["series"], self.extra["volume"] = self.book.series, self.book.volume

    @property
    def pop(self) -> int:
        return self.popularity or 0

    @property
    def year(self) -> Optional[int]:
        if self.start is not None:
            return self.start.year
        return self.season_year


_AUTHOR_NOTE = re.compile(r"[@＠].*$")


def _creator_keys(names: list[str]) -> set[str]:
    """Normalized author names; なろうの「名前@告知」は告知を落とす。"""
    keys = {normalize_title(_AUTHOR_NOTE.sub("", name)) for name in names if name}
    return {k for k in keys if len(k) >= 2}


def _same_person(x: str, y: str) -> bool:
    """One name holds the other ("奏多" / "佐槻奏多"), or the same name in the other order ("信輔近藤" / "近藤信輔")."""
    return x in y or y in x or (len(x) == len(y) >= NAME_ROTATION_MIN and y in x + x)


def _same_author(a: set[str], b: set[str]) -> Optional[bool]:
    """True / False when both sides list authors, None when either side has none."""
    if not a or not b:
        return None
    return any(_same_person(x, y) for x in a for y in b)


def _shared_authors(a: set[str], b: set[str]) -> int:
    """How many authors of `a` are also in `b` (two adaptations of one original: the artist decides)."""
    return sum(1 for x in a if any(_same_person(x, y) for y in b))


def _upcoming_title(extra: dict[str, Any]) -> Optional[VolumeTitle]:
    """The JPRO record's own title (`extra.jpro`) split as the harvester does; None for other rows."""
    raw = extra.get("jpro")
    if extra.get("via") != JPRO_VIA or not isinstance(raw, dict) or not raw.get("title"):
        return None
    volume = raw.get("volume")
    return split_volume_title(str(raw["title"]), str(volume) if volume else None)


def _json_list(value: Any) -> list[str]:
    if not value or value == "[]":
        return []
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in data if item] if isinstance(data, list) else []


def _extra(source: str, kind: str, value: Any) -> dict[str, Any]:
    # Only the linker's keys are needed; skip decoding the (large) AniList extras otherwise.
    if not value or value == "{}":
        return {}
    if source == "anilist" and (kind != "anime" or "nextAiringEpisode" not in value):
        return {}
    if source not in ("anilist", "ndl"):
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_partial_date(value: Optional[str], precision: Optional[str]) -> tuple[Optional[date], Optional[str]]:
    """"YYYY[-MM[-DD]]" + precision -> (date with 01 fillers, precision). Bad dates give (None, None)."""
    if not value:
        return None, None
    parts = str(value).strip()[:10].split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 and parts[1] else None
        day = int(parts[2]) if len(parts) > 2 and parts[2] else None
    except ValueError:
        return None, None
    if precision not in PRECISION_RANK:
        precision = "day" if day else "month" if month else "year"
    if precision == "year":
        month = day = None
    elif precision == "month":
        day = None
    try:
        return date(year, month or 1, day or 1), precision
    except ValueError:
        return None, None


def period_end(start: date, precision: Optional[str]) -> date:
    """Last day covered by a partial date (the month / year it names)."""
    if precision == "year":
        return date(start.year, 12, 31)
    if precision == "month":
        return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start


def _distance(a: Optional[date], b: Optional[date]) -> int:
    if a is None or b is None:
        return _FAR
    return abs(a.toordinal() - b.toordinal())


# --------------------------------------------------------------------------- AniList index


class _Index:
    """Title keys of one AniList kind: primary (title / native / romaji / english) and synonyms."""

    __slots__ = ("primary", "synonym", "base", "_sorted")

    def __init__(self) -> None:
        self.primary: dict[str, list[int]] = {}
        self.synonym: dict[str, list[int]] = {}
        self.base: dict[str, list[int]] = {}
        self._sorted: Optional[list[str]] = None

    def add(self, idx: int, primary: Iterable[str], synonyms: Iterable[str]) -> None:
        for key in primary:
            self.primary.setdefault(key, []).append(idx)
        for key in synonyms:
            self.synonym.setdefault(key, []).append(idx)

    def add_base(self, idx: int, keys: Iterable[str]) -> None:
        for key in keys:
            self.base.setdefault(key, []).append(idx)

    def sorted_primary(self) -> list[str]:
        if self._sorted is None:
            self._sorted = sorted(self.primary)
        return self._sorted


class _Keys:
    """Memoized title_key (the same strings repeat across title / native / synonyms)."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def __call__(self, value: Optional[str]) -> str:
        if not value:
            return ""
        key = self._cache.get(value)
        if key is None:
            key = self._cache[value] = title_key(value)
        return key


def _primary_keys(row: _Row, keys: _Keys) -> set[str]:
    found = {keys(value) for value in (row.title, row.native, row.romaji, row.english)}
    found.discard("")
    return found


def _base_keys(row: _Row, keys: _Keys, exclude: set[str]) -> set[str]:
    """Keys of the title without its subtitle, and without the words of a comic ("… THE COMIC", "…@COMIC")."""
    names = [value for value in (row.title, row.native) if value]
    found = {keys(strip_subtitle(value)) for value in names} | {keys(strip_adaptation(value)) for value in names}
    return {k for k in found if len(k) >= BASE_INDEX_MIN and k not in exclude}


def _synonym_keys(row: _Row, keys: _Keys, exclude: set[str]) -> set[str]:
    return {k for k in map(keys, row.synonyms) if len(k) >= SYNONYM_KEY_MIN and k not in exclude}


# --------------------------------------------------------------------------- matching

# candidates: AniList idx -> (confidence, method, matched key length); the strongest match per AniList row wins.
_Candidates = dict[int, tuple[float, str, int]]


def _put(cands: _Candidates, idx: int, confidence: float, method: str, key_len: int = 99) -> None:
    confidence = round(confidence, 2)
    current = cands.get(idx)
    if current is None or confidence > current[0]:
        cands[idx] = (confidence, method, key_len)


def _check_authors(cands: _Candidates, row: _Row, rows: list[_Row]) -> _Candidates:
    checked: _Candidates = {}
    for idx, (confidence, method, key_len) in cands.items():
        same = _same_author(row.creators, rows[idx].creators)
        if same:
            confidence = max(confidence, CONF_AUTHOR_MATCH)
        elif same is False and (key_len < SHORT_TITLE_KEY or method == METHOD_BASE):
            confidence = min(confidence, CONF_AUTHOR_MISMATCH)
        checked[idx] = (round(confidence, 2), method, key_len)
    return checked


def _choose(cands: _Candidates, row: _Row, rows: list[_Row]) -> Optional[tuple[int, float, str]]:
    """One AniList row per non-AniList row: best confidence, then more shared authors, popularity, nearest start."""
    best: Optional[tuple[int, float, str]] = None
    best_rank: Optional[tuple[Any, ...]] = None
    for idx, (confidence, method, _) in cands.items():
        other = rows[idx]
        shared = _shared_authors(row.creators, other.creators) if len(cands) > 1 else 0
        rank = (-confidence, -shared, -other.pop, _distance(row.start, other.start), other.id)
        if best_rank is None or rank < best_rank:
            best_rank, best = rank, (idx, confidence, method)
    return best


def _match_syoboi(row: _Row, anime: _Index, rows: list[_Row], keys: _Keys) -> Optional[tuple[int, float, str]]:
    queries: list[tuple[str, bool]] = []
    main = keys(row.title)
    if main:
        queries.append((main, False))
    for synonym in row.synonyms:
        key = keys(synonym)
        if len(key) >= SYNONYM_KEY_MIN and key != main:
            queries.append((key, True))
    cands: _Candidates = {}
    year = row.year
    for key, via_synonym in queries:
        for hits, synonym_hit in ((anime.primary.get(key, ()), False), (anime.synonym.get(key, ()), True)):
            for idx in hits:
                other_year = rows[idx].year
                if year is not None and other_year is not None and abs(year - other_year) <= YEAR_WINDOW:
                    confidence, method = CONF_TITLE_YEAR, METHOD_TITLE_YEAR
                else:
                    confidence, method = CONF_TITLE_ONLY, METHOD_TITLE_EXACT
                if via_synonym or synonym_hit:
                    confidence -= SYNONYM_PENALTY
                _put(cands, idx, confidence, method)
    return _choose(cands, row, rows)


def _web_queries(row: _Row, keys: _Keys) -> list[str]:
    """Full title, title without 【notes】, and title without its subtitle."""
    queries: list[str] = []
    for value, minimum in ((row.title, 1), (strip_notes(row.title), 1), (strip_subtitle(row.title), BASE_KEY_MIN)):
        key = keys(value)
        if len(key) >= minimum and key not in queries:
            queries.append(key)
    return queries


def _web_candidates(queries: list[str], index: _Index) -> _Candidates:
    cands: _Candidates = {}
    for query in queries:
        for idx in index.primary.get(query, ()):
            _put(cands, idx, CONF_WEB_TITLE, METHOD_TITLE_EXACT, len(query))
        for idx in index.synonym.get(query, ()):
            _put(cands, idx, CONF_WEB_SYNONYM, METHOD_SYNONYM, len(query))
        for idx in index.base.get(query, ()):
            _put(cands, idx, CONF_BASE, METHOD_BASE, len(query))
    ordered = index.sorted_primary()
    for query in queries:
        # An AniList title that is a proper prefix of the web-novel title ...
        for length in range(PREFIX_MIN, len(query)):
            for idx in index.primary.get(query[:length], ()):
                _put(cands, idx, CONF_WEB_PREFIX, METHOD_PREFIX)
        # ... or the web-novel title is a proper prefix of an AniList title.
        if len(query) >= PREFIX_MIN:
            pos = bisect.bisect_right(ordered, query)
            while pos < len(ordered) and ordered[pos].startswith(query):
                for idx in index.primary[ordered[pos]]:
                    _put(cands, idx, CONF_WEB_PREFIX, METHOD_PREFIX)
                pos += 1
    return cands


def _match_web(
    row: _Row, novel: _Index, manga: _Index, rows: list[_Row], keys: _Keys
) -> Optional[tuple[int, float, str]]:
    queries = _web_queries(row, keys)
    if not queries:
        return None
    best_novel = _choose(_check_authors(_web_candidates(queries, novel), row, rows), row, rows)
    if best_novel is not None and best_novel[1] >= CONF_WEB_TITLE:
        return best_novel
    best_manga = _choose(_check_authors(_web_candidates(queries, manga), row, rows), row, rows)
    # Novels first; a manga only wins with a strictly better match.
    if best_novel is not None and (best_manga is None or best_novel[1] >= best_manga[1]):
        return best_novel
    return best_manga


def _series_text(row: _Row) -> str:
    series = row.extra.get("series")
    return series if isinstance(series, str) and series.strip() else row.title


def _series_key(row: _Row, keys: _Keys) -> str:
    return keys(_series_text(row))


# Where a subtitle, a volume title or " : " may start: the text before it is a head of the title.
_HEAD_END = re.compile(r"\s*[:：~〜～\-‐―]|\s+")


def _heads(title: str) -> list[str]:
    """Leading parts of `title` that end before a separator ("仏の辰捕物帳 老いた掏摸" -> ["仏の辰捕物帳"]).

    A space between two Latin words is not a break ("BREAK BACK", "Only Sense Online").
    """
    heads: list[str] = []
    for found in _HEAD_END.finditer(title):
        start, end = found.start(), found.end()
        if start == 0 or end >= len(title):
            continue
        if found.group(0).isspace() and title[start - 1].isascii() and title[end].isascii():
            continue
        head = title[:start].strip()
        if head and head not in heads:
            heads.append(head)
    return heads


# How far a key of a JPRO book is from its title as written (stricter checks the further it is)
FORM_OWN = 0  # the title without the volume, notes kept ("佐武と市捕物控〈完全版〉"): what catalogued shelves match
FORM_BARE = 1  # without its notes ("佐武と市捕物控")
FORM_CUT = 2  # the series cut before the text after its volume ("山、買いました")
FORM_HEAD = 3  # a head of the title ("仏の辰捕物帳")


def _noted(text: str, notes: str) -> str:
    return f"{text} {notes}" if notes else text


def _book_forms(row: _Row, keys: _Keys) -> list[tuple[str, int]]:
    """(key, FORM_*) of a book's title, each key once at its nearest form.

    A catalogued book: its series, the series without the words of a comic ("… : コミック",
    "…@COMIC") and the heads of that. A JPRO book: its title without the volume (and without its
    notes), its series (cut when the text after a volume in the middle was dropped) and its heads.
    """
    book = row.book
    if book is None:
        return _catalogued_forms(row, keys)
    forms: dict[str, int] = {}

    def add(text: str, form: int) -> None:
        key = keys(text)
        if key and form < forms.get(key, FORM_HEAD + 1):
            forms[key] = form

    cut, bare = FORM_CUT if book.rest else FORM_OWN, FORM_BARE if book.notes else FORM_OWN
    add(_noted(book.whole, book.notes), FORM_OWN)
    add(_noted(book.series, book.notes), cut)
    add(book.whole, bare)
    add(book.series, max(cut, bare))
    for head in _heads(book.whole):
        key = keys(head)
        # "萩尾望都 創作の泉": a head that is the author's own name is not a series
        if len(key) >= HEAD_KEY_MIN and key not in row.creators:
            add(head, FORM_HEAD)
    return list(forms.items())


def _catalogued_forms(row: _Row, keys: _Keys) -> list[tuple[str, int]]:
    """A catalogued book: its series, then the body without the words of a comic and the heads of it.

    The further forms are used only with the authors overlapping, so they skip short keys (BODY_KEY_MIN)
    and a key that is one of the book's authors ("萩尾望都 : 創作の泉").
    """
    series = _series_text(row)
    own = keys(series)
    if not own:
        return []
    forms = {own: FORM_OWN}
    bare = strip_adaptation(series)
    for text, form in ((bare, FORM_BARE), *((head, FORM_HEAD) for head in _heads(bare))):
        key = keys(text)
        if len(key) >= BODY_KEY_MIN and key not in row.creators and key not in forms:
            forms[key] = form
    return list(forms.items())


def _shelf_allows(row: _Row, authors: set[str], form: int) -> bool:
    """A book may join a shelf by a key of this form: not when the authors differ, and a cut series
    or a head only when they overlap."""
    same = _same_author(row.creators, authors)
    return same is True if form >= FORM_CUT else same is not False


def _confirmed_head(
    heads: dict[int, tuple[int, str]],
    row: _Row,
    rows: list[_Row],
    kind: Optional[str] = None,
    authors: Optional[set[str]] = None,
) -> Optional[tuple[int, float, str]]:
    """The longest head match whose authors overlap with the book's (idx -> (key length, method)).

    At the same length an entry of `kind` (the book's own kind), then the one sharing more authors
    comes first. `authors` stands in for the book's own (a catalogued volume without any: its shelf's).
    """
    authors = row.creators if authors is None else authors
    best: Optional[tuple[int, float, str]] = None
    best_rank: Optional[tuple[Any, ...]] = None
    for idx, (key_len, method) in heads.items():
        other = rows[idx]
        if not _same_author(authors, other.creators):
            continue
        shared = _shared_authors(authors, other.creators)
        rank = (-key_len, other.kind != kind, -shared, -other.pop, _distance(row.start, other.start), other.id)
        if best_rank is None or rank < best_rank:
            best_rank, best = rank, (idx, CONF_HEAD, method)
    return best


def _book_candidates(
    index: _Index, queries: list[tuple[str, bool]], *, synonyms: bool = True
) -> tuple[_Candidates, dict[int, tuple[int, str]]]:
    """Matches of the (key, guarded) queries: plain candidates, and the ones that need the authors
    (idx -> (key length, method))."""
    cands: _Candidates = {}
    heads: dict[int, tuple[int, str]] = {}
    for key, guarded in queries:
        found = [(idx, METHOD_SERIES) for idx in index.primary.get(key, ())]
        found += [(idx, METHOD_SERIES) for idx in index.synonym.get(key, ())] if synonyms else []
        found += [(idx, METHOD_BASE) for idx in index.base.get(key, ())]
        for idx, method in found:
            if not guarded:
                _put(cands, idx, CONF_SERIES if method == METHOD_SERIES else CONF_BASE, method, len(key))
            elif heads.get(idx, (0, ""))[0] < len(key):
                heads[idx] = (len(key), method)
    return cands, heads


def _match_book(
    row: _Row,
    novel: _Index,
    manga: _Index,
    rows: list[_Row],
    keys: _Keys,
    *,
    loose: bool = True,
    authors: Optional[set[str]] = None,
) -> Optional[tuple[int, float, str]]:
    """The AniList novel / manga of a book. Its own title as before; the further forms (`loose`) only
    with the authors (`authors`, else the book's own) overlapping, at CONF_HEAD. A catalogued book
    also matches the other kind (the comic of its novel, the novel of its comic) that way."""
    queries = [(key, form != FORM_OWN) for key, form in _book_forms(row, keys) if loose or form == FORM_OWN]
    if not queries:
        return None
    hint = row.extra.get("kindHint")
    indexes = [novel] if hint == "novel" else [manga] if hint == "manga" else [novel, manga]
    for index in indexes:
        cands, heads = _book_candidates(index, queries)
        best = _choose(_check_authors(cands, row, rows), row, rows)
        if best is None or best[1] < MERGE_THRESHOLD:
            if row.book is None and len(indexes) == 1:
                # not by a synonym: a collection lists the stories it holds ("捨てないで" / "小さな故意の物語")
                other = manga if index is novel else novel
                crossing = [(key, True) for key, _ in queries if len(key) >= BODY_KEY_MIN]
                heads.update(_book_candidates(other, crossing, synonyms=False)[1])
            best = _confirmed_head(heads, row, rows, hint, authors) or best
        if best is not None:
            return best
    return None


def _catalogued_authors(rows: Iterable[_Row], keys: _Keys) -> dict[tuple[str, str], set[str]]:
    """(series key, kindHint) of the catalogued books -> their authors."""
    shelves: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        if row.source == "ndl" and row.kind == "book" and row.extra.get("via") != JPRO_VIA:
            key = _series_key(row, keys)
            if key:
                shelves.setdefault((key, str(row.extra.get("kindHint") or "")), set()).update(row.creators)
    return shelves


def _sibling_shelf(
    row: _Row, keys: _Keys, shelves: dict[tuple[str, str], set[str]]
) -> Optional[tuple[str, str]]:
    """The catalogued (series, kindHint) a JPRO book belongs to by its title, a head aside."""
    hint = str(row.extra.get("kindHint") or "")
    for key, form in _book_forms(row, keys):
        authors = shelves.get((key, hint))
        if form < FORM_HEAD and authors is not None and _shelf_allows(row, authors, form):
            return key, hint
    return None


def _match_all(rows: list[_Row], keys: _Keys) -> list[tuple[int, int, str, float]]:
    indexes = {"anime": _Index(), "manga": _Index(), "novel": _Index()}
    for row in rows:
        if row.source == "anilist" and row.kind in indexes:
            primary = _primary_keys(row, keys)
            indexes[row.kind].add(row.idx, primary, _synonym_keys(row, keys, primary))
            if row.kind in ("novel", "manga"):
                indexes[row.kind].add_base(row.idx, _base_keys(row, keys, primary))
    # A JPRO book with catalogued volumes of its series follows them: its further forms are not tried.
    shelves = _catalogued_authors(rows, keys)
    links: list[tuple[int, int, str, float]] = []
    for row in rows:
        if row.source == "syoboi" and row.kind == "anime":
            best = _match_syoboi(row, indexes["anime"], rows, keys)
        elif row.source in ("narou", "kakuyomu"):
            best = _match_web(row, indexes["novel"], indexes["manga"], rows, keys)
        elif row.source == "ndl" and row.kind == "book":
            loose = row.book is None or _sibling_shelf(row, keys, shelves) is None
            # a catalogued volume without authors is vouched for by the other volumes of its shelf
            authors = None
            if row.book is None and not row.creators:
                authors = shelves.get((_series_key(row, keys), str(row.extra.get("kindHint") or "")))
            best = _match_book(row, indexes["novel"], indexes["manga"], rows, keys, loose=loose, authors=authors)
        else:
            continue
        if best is not None:
            idx, confidence, method = best
            links.append((row.idx, idx, method, confidence))
    return links


# --------------------------------------------------------------------------- works


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, x: int) -> int:
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def _load_merge_edges(conn: sqlite3.Connection, index_of: dict[str, int]) -> list[tuple[int, int]]:
    cur = conn.cursor()
    cur.row_factory = None
    marks = ", ".join("?" * len(MERGE_RELATIONS))
    edges: list[tuple[int, int]] = []
    for from_id, to_id in cur.execute(
        f"SELECT from_id, to_id FROM relations WHERE relation_type IN ({marks})", MERGE_RELATIONS
    ):
        a, b = index_of.get(from_id), index_of.get(to_id)
        if a is not None and b is not None:
            edges.append((a, b))
    return edges


def _work_keys(row: _Row, keys: _Keys) -> set[str]:
    """Title keys a JPRO book may join `row` by: an AniList novel / manga, a Web novel, a catalogued book."""
    if row.source == "anilist":
        if row.kind not in ("novel", "manga"):
            return set()
        names = [row.title, row.native, row.romaji, row.english]
        found = {keys(name) for name in names} | {keys(strip_subtitle(name)) for name in names[:2] if name}
    elif row.source in ("narou", "kakuyomu"):
        found = {keys(row.title), keys(strip_notes(row.title)), keys(strip_subtitle(row.title))}
    elif row.source == "ndl" and row.kind == "book" and row.extra.get("via") != JPRO_VIA:
        series = row.extra.get("series")
        series = series if isinstance(series, str) and series.strip() else row.title
        found = {keys(series), keys(strip_subtitle(series)), keys(series.split(" : ")[0])}
    else:
        return set()
    found.discard("")
    return found


class _WorkIndex:
    """Rows by the title keys JPRO books ask for (only those keys are kept)."""

    def __init__(self, rows: list[_Row], keys: _Keys, wanted: set[str]) -> None:
        self.by_key: dict[str, list[int]] = {}
        for row in rows:
            for key in _work_keys(row, keys) & wanted:
                self.by_key.setdefault(key, []).append(row.idx)


def _join_target(row: _Row, index: _WorkIndex, rows: list[_Row], keys: _Keys) -> Optional[int]:
    """The row a JPRO book joins by its body: a key of the book is a key of that row and their authors overlap.

    Longer keys first (a spin-off's own title beats the head of the series it belongs to), then
    AniList, catalogued books and Web novels, then popularity.
    """
    best: Optional[int] = None
    best_rank: Optional[tuple[Any, ...]] = None
    for key, _ in _book_forms(row, keys):
        for idx in index.by_key.get(key, ()):
            other = rows[idx]
            if not _same_author(row.creators, other.creators):
                continue
            rank = (-len(key), JOIN_SOURCE_RANK.get(other.source, 3), -other.pop, other.id)
            if best_rank is None or rank < best_rank:
                best_rank, best = rank, idx
    return best


def _build_clusters(
    rows: list[_Row], edges: list[tuple[int, int]], merged: dict[int, int], keys: _Keys
) -> list[list[int]]:
    uf = _UnionFind(len(rows))
    for a, b in edges:
        uf.union(a, b)
    for a, b in merged.items():
        uf.union(a, b)
    # Books that joined no work: one work per (series, kindHint, line), not one per volume.
    shelves: dict[tuple[str, ...], int] = {}
    shelf_authors: dict[tuple[str, ...], set[str]] = {}
    catalogued: dict[tuple[str, str], Counter[str]] = {}  # (series, kindHint) -> catalogued line -> books
    catalogued_authors: dict[tuple[str, str], set[str]] = {}
    joined: dict[tuple[str, str], int] = {}  # (series, kindHint) of catalogued books that joined a work -> one
    joined_authors: dict[tuple[str, str], set[str]] = {}
    upcoming: list[tuple[_Row, tuple[str, str, str]]] = []
    for row in rows:
        if row.source != "ndl" or row.kind != "book":
            continue
        key = _series_key(row, keys)
        if not key:
            continue
        shelf = (key, str(row.extra.get("kindHint") or ""), str(row.extra.get("line") or ""))
        if row.idx in merged:
            if row.extra.get("via") != JPRO_VIA:
                joined.setdefault(shelf[:2], row.idx)
                joined_authors.setdefault(shelf[:2], set()).update(row.creators)
            continue
        if row.extra.get("via") == JPRO_VIA:
            upcoming.append((row, shelf))
            continue
        catalogued.setdefault(shelf[:2], Counter())[shelf[2]] += 1
        catalogued_authors.setdefault(shelf[:2], set()).update(row.creators)
        first = shelves.setdefault(shelf, row.idx)
        if first != row.idx:
            uf.union(first, row.idx)
    # A JPRO book (not catalogued yet) joins its series' catalogued shelf: JPRO often names the label
    # differently ("ヤンマガKCスペシャル" / "ヤンマガKC", "Letter Comics" / "No.9 COMICS").
    index: Optional[_WorkIndex] = None
    cut_shelves: set[tuple[str, ...]] = set()
    for row, shelf in upcoming:
        sibling = _sibling_shelf(row, keys, catalogued_authors)
        if sibling is not None:
            lines, line = catalogued[sibling], shelf[2]
            if line not in lines:
                aligned = align_line(line, lines) if line else line
                if aligned in lines:
                    line = aligned
                elif len(lines) == 1:  # one catalogued shelf: that is the series
                    line = next(iter(lines))
            uf.union(shelves.setdefault((*sibling, line), row.idx), row.idx)
            continue
        # Its catalogued volumes all joined a work ("… : コミック" -> the AniList novel): it follows them
        sibling = _sibling_shelf(row, keys, joined_authors)
        if sibling is not None:
            uf.union(joined[sibling], row.idx)
            continue
        # Else the work of a row titled with its body and written by the same authors
        if index is None:
            wanted = {key for book, _ in upcoming for key, _ in _book_forms(book, keys)}
            index = _WorkIndex(rows, keys, wanted)
        target = _join_target(row, index, rows, keys)
        if target is not None:
            uf.union(target, row.idx)
            continue
        # Else a shelf of JPRO books, apart from a catalogued shelf of the same name whose authors differ
        own: tuple[str, ...] = (*shelf, JPRO_VIA) if shelf[:2] in catalogued else shelf
        cut = row.book is not None and bool(row.book.rest)
        if own in shelves and (cut or own in cut_shelves) and not _shelf_allows(row, shelf_authors[own], FORM_CUT):
            own = (*own, row.id)
        first = shelves.setdefault(own, row.idx)
        shelf_authors.setdefault(own, set()).update(row.creators)
        if cut and first == row.idx:
            cut_shelves.add(own)
        if first != row.idx:
            uf.union(first, row.idx)
    groups: dict[int, list[int]] = {}
    for row in rows:
        groups.setdefault(uf.find(row.idx), []).append(row.idx)
    return list(groups.values())


# --------------------------------------------------------------------------- summaries


def _best(rows: Iterable[_Row]) -> Optional[_Row]:
    return min(rows, key=lambda r: (-r.pop, r.start or date.max, r.id), default=None)


_NOVEL_SOURCES = frozenset({"LIGHT_NOVEL", "NOVEL", "WEB_NOVEL"})


def _first_covered(rows: Iterable[_Row], named: str = "") -> Optional[_Row]:
    """The first series (earliest start, then the more popular) among rows that have a cover.

    Rows named after the work (`named`, a normalized title) come first: an older one-shot or pilot under another
    title (お隣の天使様's 2019 one-chapter manga) must not stand for the series.
    """

    def is_named(row: _Row) -> bool:
        return bool(named) and normalize_title(row.title or "").startswith(named)

    return min(
        (r for r in rows if r.cover),
        key=lambda r: (not is_named(r), r.start or date.max, -r.pop, r.id),
        default=None,
    )


def _origin(anilist: list[_Row]) -> Optional[str]:
    """The work's original medium. The adaptations' AniList `source` decides; without one, the medium that came first."""
    told = {r.source_material for r in anilist if r.kind in ("anime", "manga")}
    if told & _NOVEL_SOURCES:
        return "novel"
    if "MANGA" in told:
        return "manga"
    # a novel / manga that calls itself the original (a novel from a web novel counts); both: the earlier one
    claims = [
        r for r in anilist
        if (r.kind == "manga" and r.source_material == "ORIGINAL")
        or (r.kind == "novel" and r.source_material in _NOVEL_SOURCES | {"ORIGINAL"})
    ]
    if claims:
        return min(claims, key=lambda r: (r.start or date.max, -r.pop, r.id)).kind
    if any(r.kind == "anime" and r.source_material == "ORIGINAL" for r in anilist):
        return "anime"
    candidates = [
        r for r in anilist
        if r.kind in ("anime", "manga") or (r.kind == "novel" and r.source_material not in NOVELIZATION_SOURCES)
    ]
    first = min(candidates, key=lambda r: (r.start or date.max, -r.pop, r.id), default=None)
    return first.kind if first else None


def _covers(anilist: list[_Row], named: str = "") -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """(origin, the work's cover, novel cover, manga cover). The original's art stands for the work; adaptations are not.

    A novel-origin work (web novels that got a light novel included) shows the original novel, a manga-origin work its
    first manga, an anime original its first season; a medium without a cover gives way to the next one.
    """
    novels = [r for r in anilist if r.kind == "novel"]
    originals = [r for r in novels if r.source_material not in NOVELIZATION_SOURCES]
    novel = _first_covered(originals, named) or _first_covered(novels, named)
    manga = _first_covered((r for r in anilist if r.kind == "manga"), named)
    anime = _first_covered((r for r in anilist if r.kind == "anime"), named)
    origin = _origin(anilist)
    order = {
        "novel": (_first_covered(originals, named), manga, anime),
        "manga": (manga, anime, novel),
        "anime": (anime, manga, novel),
    }.get(origin or "", (_best(r for r in anilist if r.cover),))
    chosen = next((r for r in order if r is not None), None)
    return (
        origin,
        chosen.cover if chosen else None,
        novel.cover if novel else None,
        manga.cover if manga else None,
    )


def _display_title(row: _Row) -> str:
    title = row.title
    if row.source == "ndl":
        series = row.extra.get("series")
        if isinstance(series, str) and series.strip():
            title = series.strip()
    return strip_season(title) or title


def _representative(members: list[_Row]) -> _Row:
    anilist = [r for r in members if r.source == "anilist"]
    novels = [r for r in anilist if r.kind == "novel"]
    tiers = (
        [r for r in anilist if r.kind in ("novel", "manga") and r.source_material in ORIGINAL_SOURCES],
        [r for r in novels if r.source_material not in NOVELIZATION_SOURCES],  # the original novel
        [r for r in anilist if r.kind == "manga"],
        novels,
        [r for r in anilist if r.kind == "anime"],
        [r for r in members if r.kind == "web-novel"],
        [r for r in members if r.kind == "anime"],
        [r for r in members if r.kind == "book"],
        members,
    )
    for tier in tiers:
        best = _best(tier)
        if best is not None:
            return best
    raise ValueError("empty cluster")


def _book_label(row: _Row, work_title: str) -> Optional[str]:
    """The upcoming book in the list: its volume ("5巻", "第五部 女神の化身 12巻", "28巻 エキスパンションパス").

    A book without a volume shows its title, or nothing when that is the work's own title (its
    【…】 notes aside: "【書籍化決定】…" of a Web novel).
    """
    volume = volume_label(row.extra.get("volume"))
    if volume:
        book = row.book
        return " ".join(part for part in (book and book.part, volume, book and book.edition) if part)
    if normalize_title(row.title) in (normalize_title(work_title), normalize_title(strip_notes(work_title))):
        return None
    return row.title


def _next_event(
    members: list[_Row], today: date, status_of: dict[int, Optional[str]], work_title: str = ""
) -> Optional[tuple[date, str, Optional[str]]]:
    """Nearest upcoming (date, precision, label) on or after `today`."""
    candidates: list[tuple[date, int, int, Optional[str], str]] = []

    def add(when: Optional[date], precision: Optional[str], order: int, label: Optional[str]) -> None:
        if when is None or precision is None:
            return
        if period_end(when, precision) >= today:
            candidates.append((when, PRECISION_RANK[precision], order, label, precision))

    for row in members:
        if row.kind == "anime":
            if status_of.get(row.idx) in (None, "NOT_YET_RELEASED"):
                add(row.start, row.precision, 0, row.title)
            upcoming = row.extra.get("nextAiringEpisode")
            if isinstance(upcoming, dict):
                when, precision = parse_partial_date(upcoming.get("date"), "day")
                episode = upcoming.get("episode")
                add(when, precision, 1, f"第{episode}話" if episode is not None else row.title)
        elif row.kind == "book":
            add(row.start, row.precision, 2, _book_label(row, work_title))
    if not candidates:
        return None
    # ties: the label text, a missing label last
    when, _, _, label, precision = min(candidates, key=lambda c: (*c[:3], c[3] is None, c[3] or ""))
    return when, precision, label


def _summary_row(
    cluster_id: str, members: list[_Row], today: date, status_of: dict[int, Optional[str]]
) -> tuple[Any, ...]:
    rep = _representative(members)
    title = _display_title(rep)
    kinds_present = {r.kind for r in members}
    kinds = [k for k in KIND_ORDER if k in kinds_present]

    statuses = [status_of.get(r.idx) for r in members if r.kind == "anime"]
    ranked = [s for s in statuses if s in ANIME_STATUS_RANK]
    anime_status = max(ranked, key=ANIME_STATUS_RANK.__getitem__) if ranked else None

    years = [r.start.year for r in members if r.start is not None]
    seasons = sorted(
        {(r.season_year, r.season) for r in members if r.season_year and r.season in SEASON_ORDER},
        key=lambda s: (s[0], SEASON_ORDER[s[1]]),
    )
    upcoming = _next_event(members, today, status_of, title)

    anilist = [r for r in members if r.source == "anilist"]
    popularity = max((r.pop for r in anilist), default=0)
    origin, cover, novel_cover, manga_cover = _covers(anilist, normalize_title(title))

    return (
        cluster_id,
        title,
        normalize_title(title),
        json.dumps(kinds),
        int("anime" in kinds_present),
        int("manga" in kinds_present),
        int("novel" in kinds_present),
        int("web-novel" in kinds_present),
        int("book" in kinds_present),
        len(members),
        anime_status,
        min(years) if years else None,
        max(years) if years else None,
        json.dumps([f"{year:04d}-{season}" for year, season in seasons]),
        upcoming[0].isoformat() if upcoming else None,
        upcoming[1] if upcoming else None,
        upcoming[2] if upcoming else None,
        popularity,
        cover,
        origin,
        novel_cover,
        manga_cover,
    )


def _fts_text(row: _Row) -> str:
    """All names of the entry plus their width-folded forms (see `search_text`)."""
    return search_text((row.title, row.native, row.romaji, row.english, row.reading, *row.synonyms))


# --------------------------------------------------------------------------- entry point


def run_link(conn: sqlite3.Connection, *, today: date) -> dict[str, Any]:
    """Rebuilds links / clusters / cluster_summary / entries_fts and mends the series / volume of JPRO rows.

    Returns a JSON-able summary.
    """
    started = time.perf_counter()
    if isinstance(today, datetime):
        today = today.date()

    cur = conn.cursor()
    cur.row_factory = None
    rows = [_Row(idx, record) for idx, record in enumerate(cur.execute(_ENTRY_SQL))]
    index_of = {row.id: row.idx for row in rows}
    edges = _load_merge_edges(conn, index_of)

    keys = _Keys()  # shared: the work index of JPRO books reuses the keys the matching made
    links = _match_all(rows, keys)
    merged = {a: b for a, b, _, confidence in links if confidence >= MERGE_THRESHOLD}
    groups = _build_clusters(rows, edges, merged, keys)

    status_of: dict[int, Optional[str]] = {}
    for row in rows:
        if row.kind != "anime":
            continue
        if row.source == "anilist":
            status_of[row.idx] = row.status
        else:
            # syoboi has no status: borrow it from the AniList anime it joined.
            target = merged.get(row.idx)
            status_of[row.idx] = rows[target].status if target is not None and rows[target].kind == "anime" else None

    cluster_rows: list[tuple[str, str]] = []
    summary_rows: list[tuple[Any, ...]] = []
    cluster_of: dict[int, str] = {}
    largest: Optional[tuple[int, str, str]] = None
    for group in groups:
        members = [rows[i] for i in group]
        cluster_id = min(r.id for r in members)
        for row in members:
            cluster_of[row.idx] = cluster_id
            cluster_rows.append((row.id, cluster_id))
        summary = _summary_row(cluster_id, members, today, status_of)
        summary_rows.append(summary)
        if largest is None or len(members) > largest[0] or (len(members) == largest[0] and cluster_id < largest[1]):
            largest = (len(members), cluster_id, summary[1])

    link_rows = [(rows[a].id, rows[b].id, method, confidence) for a, b, method, confidence in links]
    fts_rows = [(row.id, cluster_of[row.idx], _fts_text(row)) for row in rows]
    # JPRO rows whose stored series / volume differ from the ones split from their title again
    mended = [
        (row.extra["series"], row.extra["volume"], row.id)
        for row in rows
        if row.book is not None and row.stored != (row.extra["series"], row.extra["volume"])
    ]

    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM links")
        conn.execute("DELETE FROM clusters")
        conn.execute("DELETE FROM cluster_summary")
        conn.execute("DELETE FROM entries_fts")
        conn.executemany("INSERT INTO links (a_id, b_id, method, confidence) VALUES (?, ?, ?, ?)", link_rows)
        conn.executemany("INSERT INTO clusters (entry_id, cluster_id) VALUES (?, ?)", cluster_rows)
        conn.executemany(
            "INSERT INTO cluster_summary (cluster_id, title, title_norm, kinds, has_anime, has_manga, "
            "has_novel, has_web_novel, has_book, entry_count, anime_status, first_year, last_year, "
            "seasons, next_date, next_precision, next_label, popularity, cover, origin, novel_cover, manga_cover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            summary_rows,
        )
        conn.executemany("INSERT INTO entries_fts (entry_id, cluster_id, text) VALUES (?, ?, ?)", fts_rows)
        conn.executemany(
            "UPDATE entries SET extra = json_set(extra, '$.series', ?, '$.volume', ?) WHERE id = ?", mended
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise

    by_method = Counter(method for _, _, method, _ in links)
    return {
        "entries": len(rows),
        "links": len(links),
        "linksByMethod": dict(sorted(by_method.items())),
        "clusters": len(summary_rows),
        "largestCluster": (
            {"id": largest[1], "title": largest[2], "size": largest[0]}
            if largest
            else {"id": None, "title": None, "size": 0}
        ),
        "seconds": round(time.perf_counter() - started, 2),
    }

