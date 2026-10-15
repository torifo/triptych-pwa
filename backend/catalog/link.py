"""Linker: cross-source links, works (clusters), list summaries and the search index.

`run_link(conn, today=...)` rebuilds `links`, `clusters`, `cluster_summary` and
`entries_fts` from `entries` / `relations` in one transaction.

1. Title matching ties every non-AniList row to at most one AniList entry
   (syoboi -> anime, narou / kakuyomu -> novel or manga, ndl -> novel or manga).
2. Works are made with union-find over the merge relations
   (ADAPTATION / SOURCE / SEQUEL / PREQUEL) and the links whose
   confidence is >= MERGE_THRESHOLD. Weaker links stay in `links` as candidates.
3. NDL books that joined no work are grouped per (series, kindHint, line).
4. `cluster_id` is the smallest member id, so it is stable across runs.
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

from .text import normalize_title, strip_notes, strip_season, strip_subtitle, title_key

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


def _same_author(a: set[str], b: set[str]) -> Optional[bool]:
    """True / False when both sides list authors, None when either side has none."""
    if not a or not b:
        return None
    return any(x in y or y in x for x in a for y in b)


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
    found = {keys(strip_subtitle(value)) for value in (row.title, row.native) if value}
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
    """One AniList row per non-AniList row: best confidence, then popularity, then nearest start."""
    best: Optional[tuple[int, float, str]] = None
    best_rank: Optional[tuple[Any, ...]] = None
    for idx, (confidence, method, _) in cands.items():
        other = rows[idx]
        rank = (-confidence, -other.pop, _distance(row.start, other.start), other.id)
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


def _series_key(row: _Row, keys: _Keys) -> str:
    series = row.extra.get("series")
    return keys(series if isinstance(series, str) and series.strip() else row.title)


def _match_book(
    row: _Row, novel: _Index, manga: _Index, rows: list[_Row], keys: _Keys
) -> Optional[tuple[int, float, str]]:
    key = _series_key(row, keys)
    if not key:
        return None
    hint = row.extra.get("kindHint")
    indexes = [novel] if hint == "novel" else [manga] if hint == "manga" else [novel, manga]
    for index in indexes:
        cands: _Candidates = {}
        for idx in (*index.primary.get(key, ()), *index.synonym.get(key, ())):
            _put(cands, idx, CONF_SERIES, METHOD_SERIES, len(key))
        for idx in index.base.get(key, ()):
            _put(cands, idx, CONF_BASE, METHOD_BASE, len(key))
        best = _choose(_check_authors(cands, row, rows), row, rows)
        if best is not None:
            return best
    return None


def _match_all(rows: list[_Row]) -> list[tuple[int, int, str, float]]:
    keys = _Keys()
    indexes = {"anime": _Index(), "manga": _Index(), "novel": _Index()}
    for row in rows:
        if row.source == "anilist" and row.kind in indexes:
            primary = _primary_keys(row, keys)
            indexes[row.kind].add(row.idx, primary, _synonym_keys(row, keys, primary))
            if row.kind in ("novel", "manga"):
                indexes[row.kind].add_base(row.idx, _base_keys(row, keys, primary))
    links: list[tuple[int, int, str, float]] = []
    for row in rows:
        if row.source == "syoboi" and row.kind == "anime":
            best = _match_syoboi(row, indexes["anime"], rows, keys)
        elif row.source in ("narou", "kakuyomu"):
            best = _match_web(row, indexes["novel"], indexes["manga"], rows, keys)
        elif row.source == "ndl" and row.kind == "book":
            best = _match_book(row, indexes["novel"], indexes["manga"], rows, keys)
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


def _build_clusters(
    rows: list[_Row], edges: list[tuple[int, int]], merged: dict[int, int]
) -> list[list[int]]:
    uf = _UnionFind(len(rows))
    for a, b in edges:
        uf.union(a, b)
    for a, b in merged.items():
        uf.union(a, b)
    # Books that joined no work: one work per (series, kindHint, line), not one per volume.
    keys = _Keys()
    shelves: dict[tuple[str, str, str], int] = {}
    for row in rows:
        if row.source != "ndl" or row.kind != "book" or row.idx in merged:
            continue
        key = _series_key(row, keys)
        if not key:
            continue
        shelf = (key, str(row.extra.get("kindHint") or ""), str(row.extra.get("line") or ""))
        first = shelves.setdefault(shelf, row.idx)
        if first != row.idx:
            uf.union(first, row.idx)
    groups: dict[int, list[int]] = {}
    for row in rows:
        groups.setdefault(uf.find(row.idx), []).append(row.idx)
    return list(groups.values())


# --------------------------------------------------------------------------- summaries


def _best(rows: Iterable[_Row]) -> Optional[_Row]:
    return min(rows, key=lambda r: (-r.pop, r.start or date.max, r.id), default=None)


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


def _next_event(
    members: list[_Row], today: date, status_of: dict[int, Optional[str]]
) -> Optional[tuple[date, str, str]]:
    """Nearest upcoming (date, precision, label) on or after `today`."""
    candidates: list[tuple[date, int, int, str, str]] = []

    def add(when: Optional[date], precision: Optional[str], order: int, label: str) -> None:
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
            add(row.start, row.precision, 2, row.title)
    if not candidates:
        return None
    when, _, _, label, precision = min(candidates)
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
    upcoming = _next_event(members, today, status_of)

    anilist = [r for r in members if r.source == "anilist"]
    popularity = max((r.pop for r in anilist), default=0)
    covered = _best(r for r in anilist if r.cover)

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
        covered.cover if covered else None,
    )


def _fts_text(row: _Row) -> str:
    parts: list[str] = []
    for value in (row.title, row.native, row.romaji, row.english, row.reading, *row.synonyms):
        value = (value or "").strip()
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


# --------------------------------------------------------------------------- entry point


def run_link(conn: sqlite3.Connection, *, today: date) -> dict[str, Any]:
    """Rebuilds links / clusters / cluster_summary / entries_fts. Returns a JSON-able summary."""
    started = time.perf_counter()
    if isinstance(today, datetime):
        today = today.date()

    cur = conn.cursor()
    cur.row_factory = None
    rows = [_Row(idx, record) for idx, record in enumerate(cur.execute(_ENTRY_SQL))]
    index_of = {row.id: row.idx for row in rows}
    edges = _load_merge_edges(conn, index_of)

    links = _match_all(rows)
    merged = {a: b for a, b, _, confidence in links if confidence >= MERGE_THRESHOLD}
    groups = _build_clusters(rows, edges, merged)

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
            "seasons, next_date, next_precision, next_label, popularity, cover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            summary_rows,
        )
        conn.executemany("INSERT INTO entries_fts (entry_id, cluster_id, text) VALUES (?, ?, ?)", fts_rows)
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

