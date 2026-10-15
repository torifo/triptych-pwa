"""Title normalization shared by the linker and the catalog API."""

from __future__ import annotations

import re
import string
import unicodedata
from typing import Iterable, Mapping, NamedTuple, Optional

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


# -- 近刊の題名: シリーズ名・巻・版 -------------------------------------------------------------

# JPRO の近刊は巻を題名に書く。書き方は「本体(28)」「本体10 ~副題~」「本体 3 ~副題~」「本体~副題~ 9」
# 「本体【三】巻の題」など。巻より前がシリーズ名（本体）で、巻の後ろの語（副題・巻の題）は落とす。

# 同じ巻の別の版。題名から外して巻の表示に添える（特装版・限定版・同梱は収集の段階で除外される）
EDITION_WORDS = ("エキスパンションパス", "限定版", "特装版", "通常版", "特別版", "同梱版", "豪華版")
_BRACKET_OPEN = r"(\[［【〔<〈"
_BRACKET_CLOSE = r")\]］】〕>〉"
_EDITION = re.compile(
    rf"[{_BRACKET_OPEN}]?\s*(?:初回|数量)?(?P<word>{'|'.join(EDITION_WORDS)})\s*[{_BRACKET_CLOSE}]?"
)
# 括弧の注記（形態・完結・版）。シリーズ名には含めない。完結の印のほかは `notes` に残す
# （目録は「… : コミック」「… : 完全版」と書くので、同じシリーズの目録済みの本を探すのに使う）
_NOTE = re.compile(
    rf"[{_BRACKET_OPEN}]\s*(?P<word>完|完結|コミック|コミックス|コミック版|小説版|新装版|新版|完全版|愛蔵版|文庫版|"
    rf"ワイド版|図書館版|決定版|改訂版)\s*[{_BRACKET_CLOSE}]"
)
_END_MARKS = ("完", "完結")
_KANJI_DIGITS = "一二三四五六七八九十"
_COUNT = rf"\d{{1,3}}|[{_KANJI_DIGITS}]{{1,3}}"  # 巻の番号: 3桁まで（2024 のような年は題名の一部）
_HALF = "[上中下前後]"
_VOLUME_WORD = r"(?<![A-Za-z])[\[(]?(?:vol|volume|ep|episode|life)[\])]?\.?\s*"
# 巻の数字の直前に来ない文字（"天国2.0" "テルスター'86" "ゴジラ -0.0" "No.9" の数字は題名の一部）
_NOT_BEFORE = r"0-9.．,'’\-‐―−/:#"
_SEPARATOR_AFTER = r"~〜～\-‐―:【\[(<〈"
# 題名の末尾の巻（順に試す）
_TRAILING_VOLUMES = (
    re.compile(rf"[{_BRACKET_OPEN}]\s*(?:第\s*)?(?P<n>{_COUNT}|{_HALF})\s*(?:巻|話|集)?\s*[{_BRACKET_CLOSE}]$"),
    re.compile(rf"(?:第\s*)?(?P<n>{_COUNT}|{_HALF})\s*[巻冊集]$"),
    re.compile(rf"{_VOLUME_WORD}(?P<n>\d{{1,3}})$", re.IGNORECASE),
    re.compile(r"#\s*(?P<n>\d{1,3})$"),
    re.compile(rf"(?<=\s)(?P<n>[{_KANJI_DIGITS}]{{1,3}}|{_HALF})$"),
    re.compile(rf"(?<![{_NOT_BEFORE}])(?P<n>\d{{1,3}})$"),
)
# 題名の途中の巻: 本体の直後にあり、あとに副題・巻の題が続く
_MIDDLE_VOLUMES = (
    re.compile(rf"[{_BRACKET_OPEN}]\s*(?:第\s*)?(?P<n>{_COUNT}|{_HALF})\s*(?:巻|話|集)?\s*[{_BRACKET_CLOSE}]"),
    re.compile(rf"(?:第\s*)?(?P<n>{_COUNT})\s*[巻冊](?=\s|[~〜～])"),
    re.compile(rf"{_VOLUME_WORD}(?P<n>\d{{1,3}})(?=\s|[~〜～])", re.IGNORECASE),
    re.compile(rf"(?<=[^\s{_NOT_BEFORE}])(?P<n>\d{{1,3}})(?=\s|[{_SEPARATOR_AFTER}])"),
    re.compile(rf"(?<=\s)(?P<n>\d{{1,3}})(?=\s+\S|\s*[{_SEPARATOR_AFTER}])"),
    re.compile(rf"(?<=\s)(?P<n>[{_KANJI_DIGITS}]{{1,3}}|{_HALF})(?=\s)"),
)
# 部の印から先は巻の側（「本好きの下剋上 第五部 女神の化身12」「デルフィニア戦記 : 第III部 動乱の序章3」）
_PART = re.compile(rf"[\s~〜～:：\-]*第\s*(?:{_COUNT}|[IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)\s*部.*$")
_EDGE_SEPARATORS = re.compile(r"[\s:：/,、・]+$")
_PAIRED_MARKS = ("~〜～", "-‐―")  # 副題を挟む印（開きと閉じが同じ形）


class VolumeTitle(NamedTuple):
    """近刊の題名を分けたもの。`series` が extra.series になる。"""

    series: str  # 巻より前（本体）。版の語・注記・部から先を除く
    volume: Optional[str]  # 巻の書き方そのまま（"(28)" "10" "第16巻"）。JPRO の巻の欄があればそれ
    edition: Optional[str]  # 版の語（"エキスパンションパス"）
    rest: str = ""  # 途中の巻の後ろにあって series から外した語（副題・巻の題）
    part: str = ""  # series から外した部の印から先（"第五部 女神の化身"）
    notes: str = ""  # 外した括弧の注記（完結の印を除く。"コミック" "完全版"）

    @property
    def whole(self) -> str:
        """巻・版の語・注記だけを除いた題名（途中の巻の後ろの語も残す）。"""
        return f"{self.series} {self.rest}".strip() if self.rest else self.series


def _count(text: str) -> Optional[int]:
    """'12' / '十二' / '二十三' -> the number; anything else None."""
    if text.isdigit():
        return int(text)
    if not text or any(c not in _KANJI_DIGITS for c in text):
        return None
    tens, _, ones = text.partition("十")
    if "十" not in text:
        return _KANJI_DIGITS.index(text) + 1 if len(text) == 1 else None
    value = (_KANJI_DIGITS.index(tens) + 1 if tens else 1) * 10
    return value + (_KANJI_DIGITS.index(ones) + 1 if ones else 0) if len(tens) <= 1 and len(ones) <= 1 else None


def _same_volume(a: str, b: str) -> bool:
    x, y = _count(a), _count(b)
    return a == b if x is None or y is None else x == y


def _trim(text: str) -> str:
    """Trailing spaces and separators off; a closing ~ / ― stays when it closes a subtitle."""
    text = _EDGE_SEPARATORS.sub("", text.strip())
    while text and text[-1] in _PAIRED_MARKS:
        family = next(marks for marks in _PAIRED_MARKS if text[-1] in marks)
        if sum(text.count(c) for c in family) > 1:
            break
        text = _EDGE_SEPARATORS.sub("", text[:-1].strip())
    return text


def _long_enough(head: str) -> bool:
    return len(normalize_title(head)) >= 2


def split_volume_title(title: str, volume: Optional[str] = None) -> VolumeTitle:
    """近刊の題名 -> (シリーズ名, 巻, 版の語, 巻の後ろの語, 部から先)。

    `volume` は JPRO の巻の欄（dcndl:volume）。題名の末尾がそれと同じなら巻として外す。
    末尾の巻と同じ番号が途中（本体の直後）にもあれば、途中で切る（"…なります9~副題~ 9"）。
    末尾に巻が無く途中にあれば、途中で切る（"山、買いました10 ~副題~"）。末尾と違う番号の途中の数字は
    題名の一部（"ファンタシースターオンライン2 es ぎゅ!!! 1"）。
    """
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", title or "")).strip()
    edition: Optional[str] = None
    match = _EDITION.search(text)
    if match:
        edition = match.group("word")
        text = _EDITION.sub(" ", text)
    notes = " ".join(found.group("word") for found in _NOTE.finditer(text) if found.group("word") not in _END_MARKS)
    text = re.sub(r"\s+", " ", _NOTE.sub(" ", text)).strip()
    stated = unicodedata.normalize("NFKC", volume).strip() if volume else None

    end = len(text)  # where the trailing volume starts
    token: Optional[str] = None  # the trailing volume as written
    if stated and text.endswith(stated) and _long_enough(text[: -len(stated)]):
        end, token = len(text) - len(stated), stated
    else:
        for pattern in _TRAILING_VOLUMES:
            trailing = pattern.search(text)
            if trailing and _long_enough(text[: trailing.start()]):
                end, token = trailing.start(), trailing.group(0).strip()
                break
    # a volume in the middle must be the same as the trailing one (or the stated one)
    expected = _volume_number(token or stated or "")
    middle: Optional[tuple[int, int, str]] = None
    for pattern in _MIDDLE_VOLUMES:
        for found in pattern.finditer(text, 0, end):
            if found.end() >= end or not _long_enough(text[: found.start()]):
                continue
            if expected and not _same_volume(found.group("n"), expected):
                continue
            if middle is None or found.start() < middle[0]:
                middle = (found.start(), found.end(), found.group(0).strip())
            break
    if middle is not None:
        series, rest, token = text[: middle[0]], _trim(text[middle[1] : end]), middle[2]
    else:
        series, rest = text[:end], ""
    series = _trim(series)
    part = ""
    found_part = _PART.search(series)
    if found_part and _long_enough(series[: found_part.start()]):
        part = _trim(found_part.group(0).strip(" ~〜～:：-"))
        series = _trim(series[: found_part.start()])
    if not series:
        series = _trim(text) or text
    return VolumeTitle(series=series, volume=stated or token, edition=edition, rest=rest, part=part, notes=notes)


def _volume_number(volume: str) -> str:
    """The number of a volume as written ("第2巻" -> "2", "(上)" -> "上"), else ""."""
    found = re.search(rf"(?<![0-9])(?:{_COUNT}|{_HALF})(?![0-9])", volume)
    return found.group(0) if found else ""


_LABEL_WRAPPED = re.compile(
    rf"^[{_BRACKET_OPEN}]\s*(?P<inner>[^{_BRACKET_OPEN}{_BRACKET_CLOSE}]*?)\s*[{_BRACKET_CLOSE}]$"
)
_LABEL_NUMBER = re.compile(rf"^(?:第\s*)?(?P<n>{_COUNT})\s*巻?$")
_LABEL_WORD = re.compile(rf"^{_VOLUME_WORD}(?P<n>\d{{1,3}})$", re.IGNORECASE)
_LABEL_HALF = re.compile(rf"^(?P<n>{_HALF})巻?$")
_LABEL_ALL = re.compile(r"^全\s*(?P<n>\d{1,3})\s*巻?$")
_LABEL_TAIL = re.compile(rf"[{_BRACKET_OPEN}]\s*(?P<n>\d{{1,3}})\s*[{_BRACKET_CLOSE}]$")


def volume_label(volume: Optional[str]) -> Optional[str]:
    """巻の表示: '(1)' / '第2巻' / 'Vol. 2' / '二' -> '1巻' / '2巻'、'上' -> '上巻'、'第3部[10]' -> '第3部 10巻'。

    Library の巻の表示（数字で終わる巻に「巻」を足す）に合わせる。数字の無い巻の名前はそのまま、
    記号だけなら None。
    """
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", volume or "")).strip()
    wrapped = _LABEL_WRAPPED.match(text)
    if wrapped:
        text = wrapped.group("inner").strip()
    if not normalize_title(text):
        return None
    for pattern in (_LABEL_NUMBER, _LABEL_WORD):
        found = pattern.match(text)
        if found:
            number = _count(found.group("n"))
            return f"{number}巻" if number is not None else None
    found = _LABEL_HALF.match(text)
    if found:
        return f"{found.group('n')}巻"
    found = _LABEL_ALL.match(text)
    if found:
        return f"全{int(found.group('n'))}巻"
    text = _LABEL_TAIL.sub(lambda m: f" {int(m.group('n'))}", text).strip()
    if re.search(r"(?:^|\s)\d{1,3}$", text):
        return f"{text}巻"
    return text
