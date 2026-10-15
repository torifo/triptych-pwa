"""Title normalization shared by the linker and the catalog API."""

from __future__ import annotations

import re
import string
import unicodedata
from typing import Iterable, Mapping, Optional

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
# What SQLite's lower() does: only A-Z.
_SQLITE_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)

# 期・クール・シーズン表記（末尾）。例: 第3期 / 3期 / 第2クール / Season 2 / 2nd Season / Ⅱ
_SEASON_SUFFIXES = [
    re.compile(r"[\s　]*第?[0-9０-９一二三四五六七八九十]+期.*$"),
    re.compile(r"[\s　]*第?[0-9０-９一二三四五六七八九十]+クール.*$"),
    re.compile(r"[\s　]*(?:season|シーズン)[\s　]*[0-9０-９]+.*$", re.IGNORECASE),
    re.compile(r"[\s　]*[0-9０-９]+(?:st|nd|rd|th)[\s　]*season.*$", re.IGNORECASE),
    re.compile(r"[\s　]*[ⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$"),
    re.compile(r"[\s　]*\((?:第?[0-9０-９]+期|TV)\)$"),
]


def normalize_title(value: str) -> str:
    """NFKC → lowercase → drop whitespace and punctuation."""
    text = unicodedata.normalize("NFKC", value or "").lower()
    return _NON_WORD.sub("", text)


def strip_season(value: str) -> str:
    """Removes trailing season / cour markers (raw title in, raw title out)."""
    text = (value or "").strip()
    for pattern in _SEASON_SUFFIXES:
        stripped = pattern.sub("", text).strip()
        if stripped and stripped != text:
            text = stripped
    return text


def title_key(value: str) -> str:
    """Normalized title without season markers — the main matching key."""
    return normalize_title(strip_season(value))


# 副題の区切り。長音「ー」は含めない（題名の一部なので）
_SUBTITLE_SEPARATOR = re.compile(r"[~〜～\-－―—–‐─]")
# Web 小説の題名に付く注記：【書籍化】《完結》 や末尾の（web版）
_BRACKET_NOTES = re.compile(r"【[^】]*】|《[^》]*》")
_TRAILING_PAREN = re.compile(r"[\s　]*[(（][^()（）]*[)）][\s　]*$")


def strip_notes(value: str) -> str:
    """Drops 【…】 / 《…》 notes and a trailing (…) such as （web版）. Empty results fall back to the input."""
    original = (value or "").strip()
    text = _BRACKET_NOTES.sub(" ", original).strip()
    text = _TRAILING_PAREN.sub("", text).strip()
    return text or original


def strip_subtitle(value: str) -> str:
    """Drops notes and everything after the first 〜 / ― / - (raw title in, raw title out).

    "無職転生　- 異世界行ったら本気だす -" -> "無職転生". A title that would become empty is returned as is.
    """
    original = (value or "").strip()
    text = strip_notes(original)
    for match in _SUBTITLE_SEPARATOR.finditer(text):
        head = text[: match.start()].strip()
        if head:
            text = head
            break
    return text or original


def base_title_key(value: str) -> str:
    """title_key of the title without its subtitle (for long web-novel titles)."""
    return title_key(strip_subtitle(value))


# -- search ---------------------------------------------------------------------


def fold_for_search(value: Optional[str]) -> str:
    """NFKC → lowercase → collapse whitespace. Unlike `normalize_title`, punctuation and word gaps stay.

    Full-width latin / digits and half-width kana become their ordinary forms, which is the whole
    point; dropping the spaces too would let "heap" find "The Apothecary Diaries".
    """
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", value or "").lower()).strip()


def search_text(values: Iterable[Optional[str]]) -> str:
    """The `entries_fts.text` of one entry: its names as they are, then their folded forms.

    A folded form is left out when SQLite's own lower() already yields it (an ASCII name that
    differs only by case), because the short-query scan lowers the text and the trigram index
    folds case anyway; that keeps the text about 12% longer instead of about 70%.
    The query side applies `fold_for_search` to the user's input.
    """
    names: list[str] = []
    for value in values:
        value = (value or "").strip()
        if value and value not in names:
            names.append(value)
    folded: list[str] = []
    for name in names:
        key = fold_for_search(name)
        if key and key != name.translate(_SQLITE_LOWER) and key not in names and key not in folded:
            folded.append(key)
    return " ".join([*names, *folded])


# -- 叢書名 (NDL "line") --------------------------------------------------------------------

# Latin and kana spellings of the same label word ("花とゆめCOMICS" / "花とゆめコミックス").
_LINE_WORDS = (
    ("comics", "コミックス"),
    ("comic", "コミック"),
    ("novels", "ノベルス"),
    ("novel", "ノベル"),
    ("ノベルズ", "ノベルス"),
)
RELATED_LINE_MIN = 4  # shorter label keys ("KC") are too generic to be read as variants


def line_key(line: Optional[str]) -> str:
    """Comparison key of a 叢書名: `normalize_title` with the Latin label words in kana."""
    key = normalize_title(line or "")
    for word, kana in _LINE_WORDS:
        key = key.replace(word, kana)
    return key


def align_line(label: str, lines: Mapping[str, int]) -> str:
    """The line of `lines` (name -> books) that `label` is a spelling or a variant of, else `label`.

    JPRO's 近刊 records name the label another way than NDL's catalogue: "モーニング KC" ->
    "モーニングKC" (spacing, case, COMICS / コミックス), "ヤンマガKCスペシャル" -> "ヤンマガKC" (one
    name holds the other, both at least RELATED_LINE_MIN long). The line with more books wins.
    """
    key = line_key(label)
    same = [line for line in lines if line_key(line) == key]
    if same:
        return max(same, key=lambda line: (lines[line], line))
    if len(key) >= RELATED_LINE_MIN:
        related = [
            line
            for line in lines
            if len(line_key(line)) >= RELATED_LINE_MIN and (line_key(line) in key or key in line_key(line))
        ]
        if related:
            return max(related, key=lambda line: (lines[line], line))
    return label
