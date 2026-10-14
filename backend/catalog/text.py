"""Title normalization shared by the linker and the catalog API."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)

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
