"""Linker tests on synthetic catalogs (tmp_path DBs only)."""

from __future__ import annotations

import json
import os
import random
import time
from datetime import date

import pytest

from backend.catalog import db
from backend.catalog.link import run_link
from backend.catalog.text import base_title_key, strip_subtitle, title_key

TODAY = date(2026, 10, 14)
RUN_SLOW = os.environ.get("TRIPTYCH_SLOW") == "1"  # pytest.ini registers no "slow" marker


# --------------------------------------------------------------------------- helpers


def _entry(source: str, external_id: str, kind: str, title: str, **fields) -> db.Entry:
    return db.Entry(source=source, external_id=str(external_id), kind=kind, title=title, **fields)


def _anilist(external_id, kind, title, **fields) -> db.Entry:
    fields.setdefault("title_native", title)
    return _entry("anilist", external_id, kind, title, **fields)


def _book(external_id, title, series, hint, start_date, *, line="", precision="day", **fields) -> db.Entry:
    extra = {"series": series, "kindHint": hint, "line": line, "isbn": f"978{external_id:0>10}"}
    return _entry(
        "ndl", external_id, "book", title, start_date=start_date, start_precision=precision, extra=extra, **fields
    )


def _open(tmp_path, entries, relations=()) -> "db.sqlite3.Connection":
    conn = db.connect(tmp_path / "catalog.db")
    db.upsert_entries(conn, entries)
    conn.executemany(
        "INSERT OR IGNORE INTO relations (from_id, to_id, relation_type) VALUES (?, ?, ?)", list(relations)
    )
    conn.commit()
    return conn


def _cluster(conn, entry_id: str) -> str:
    return conn.execute("SELECT cluster_id FROM clusters WHERE entry_id = ?", (entry_id,)).fetchone()[0]


def _members(conn, cluster_id: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT entry_id FROM clusters WHERE cluster_id = ?", (cluster_id,))}


def _summary(conn, cluster_id: str) -> dict:
    row = conn.execute("SELECT * FROM cluster_summary WHERE cluster_id = ?", (cluster_id,)).fetchone()
    data = dict(row)
    data["kinds"] = json.loads(data["kinds"])
    data["seasons"] = json.loads(data["seasons"])
    return data


def _links(conn, a_id: str) -> list[tuple[str, str, float]]:
    return [
        (r["b_id"], r["method"], r["confidence"])
        for r in conn.execute("SELECT b_id, method, confidence FROM links WHERE a_id = ? ORDER BY b_id", (a_id,))
    ]


def _dump(conn) -> dict[str, list[tuple]]:
    return {
        "links": [tuple(r) for r in conn.execute("SELECT * FROM links ORDER BY a_id, b_id")],
        "clusters": [tuple(r) for r in conn.execute("SELECT * FROM clusters ORDER BY entry_id")],
        "summary": [tuple(r) for r in conn.execute("SELECT * FROM cluster_summary ORDER BY cluster_id")],
        "fts": [tuple(r) for r in conn.execute("SELECT entry_id, cluster_id, text FROM entries_fts ORDER BY entry_id")],
    }


# --------------------------------------------------------------------------- 薬屋のひとりごと


def _kusuriya() -> tuple[list[db.Entry], list[tuple[str, str, str]]]:
    entries = [
        _anilist(
            101, "novel", "薬屋のひとりごと", title_romaji="Kusuriya no Hitorigoto", title_english="The Apothecary Diaries",
            popularity=20000, start_date="2014-08-01", start_precision="month", status="RELEASING",
            source_material="WEB_NOVEL", cover="https://img/novel.jpg",
        ),
        _anilist(
            102, "manga", "薬屋のひとりごと", title_romaji="Kusuriya no Hitorigoto", popularity=30000,
            start_date="2017-05-25", start_precision="day", status="RELEASING", source_material="LIGHT_NOVEL",
            cover="https://img/manga1.jpg",
        ),
        _anilist(
            103, "manga", "薬屋のひとりごと～猫猫の後宮謎解き手帳～",
            title_romaji="Kusuriya no Hitorigoto: Maomao no Koukyuu Nazotoki Techou", popularity=15000,
            start_date="2017-08-01", start_precision="month", status="RELEASING", source_material="LIGHT_NOVEL",
        ),
        _anilist(
            110, "anime", "薬屋のひとりごと", title_romaji="Kusuriya no Hitorigoto", title_english="The Apothecary Diaries",
            synonyms=["薬屋"], popularity=200000, start_date="2023-10-22", start_precision="day", status="FINISHED",
            season="FALL", season_year=2023, cover="https://img/anime1.jpg",
        ),
        _anilist(
            111, "anime", "薬屋のひとりごと 第2期", title_romaji="Kusuriya no Hitorigoto 2nd Season",
            popularity=100000, start_date="2025-01-10", start_precision="day", status="FINISHED",
            season="WINTER", season_year=2025, cover="https://img/anime2.jpg",
        ),
        _anilist(
            112, "anime", "薬屋のひとりごと 第3期", title_romaji="Kusuriya no Hitorigoto 3rd Season",
            popularity=50000, start_date="2026-10-10", start_precision="day", status="RELEASING",
            season="FALL", season_year=2026, extra={"nextAiringEpisode": {"date": "2026-10-17", "time": "00:25", "episode": 2}},
        ),
        _entry(
            "syoboi", "6900", "anime", "薬屋のひとりごと", title_reading="くすりやのひとりごと", synonyms=["薬屋"],
            start_date="2023-10-01", start_precision="month", extra={"cat": 1},
        ),
        _entry(
            "syoboi", "7300", "anime", "薬屋のひとりごと 第2期", title_reading="くすりやのひとりごと",
            start_date="2025-01-01", start_precision="month", extra={"cat": 1},
        ),
        _entry(
            "narou", "n9636x", "web-novel", "薬屋のひとりごと", creators=["日向夏"], popularity=380000,
            start_date="2011-10-01", start_precision="day",
        ),
        _book("9784074171201", "薬屋のひとりごと 1", "薬屋のひとりごと", "novel", "2014-08-29"),
        _book("9784074171202", "薬屋のひとりごと 16", "薬屋のひとりごと", "novel", "2026-11-28"),
        _book("9784757599202", "薬屋のひとりごと 15", "薬屋のひとりごと", "manga", "2026-12-25"),
    ]
    relations = [
        ("anilist:101", "anilist:102", "ADAPTATION"),
        ("anilist:101", "anilist:103", "ADAPTATION"),
        ("anilist:101", "anilist:110", "ADAPTATION"),
        ("anilist:110", "anilist:101", "SOURCE"),
        ("anilist:110", "anilist:111", "SEQUEL"),
        ("anilist:111", "anilist:110", "PREQUEL"),
        ("anilist:111", "anilist:112", "SEQUEL"),
        ("anilist:112", "anilist:999999", "SEQUEL"),  # points outside the catalog
    ]
    return entries, relations


def test_kusuriya_becomes_one_work_with_every_face(tmp_path):
    entries, relations = _kusuriya()
    conn = _open(tmp_path, entries, relations)
    result = run_link(conn, today=TODAY)

    ids = {e.id for e in entries}
    cid = _cluster(conn, "anilist:101")
    assert cid == min(ids)
    assert _members(conn, cid) == ids

    s = _summary(conn, cid)
    assert s["title"] == "薬屋のひとりごと"
    assert s["title_norm"] == "薬屋のひとりごと"
    assert s["kinds"] == ["anime", "manga", "novel", "web-novel", "book"]
    assert (s["has_anime"], s["has_manga"], s["has_novel"], s["has_web_novel"], s["has_book"]) == (1, 1, 1, 1, 1)
    assert s["entry_count"] == len(entries)
    assert s["anime_status"] == "RELEASING"
    assert (s["first_year"], s["last_year"]) == (2011, 2026)
    assert s["seasons"] == ["2023-FALL", "2025-WINTER", "2026-FALL"]
    assert (s["next_date"], s["next_precision"], s["next_label"]) == ("2026-10-17", "day", "第2話")
    assert s["popularity"] == 200000
    assert s["cover"] == "https://img/anime1.jpg"

    assert _links(conn, "syoboi:6900") == [("anilist:110", "title-year", 0.95)]
    assert _links(conn, "syoboi:7300") == [("anilist:111", "title-year", 0.95)]
    assert _links(conn, "narou:n9636x") == [("anilist:101", "title-exact", 0.9)]
    assert _links(conn, "ndl:9784074171201") == [("anilist:101", "series", 0.85)]
    assert _links(conn, "ndl:9784757599202") == [("anilist:102", "series", 0.85)]  # manga only, most popular

    assert result["entries"] == len(entries)
    assert result["links"] == 6
    assert result["linksByMethod"] == {"series": 3, "title-exact": 1, "title-year": 2}
    assert result["clusters"] == 1
    assert result["largestCluster"] == {"id": cid, "title": "薬屋のひとりごと", "size": len(entries)}
    assert isinstance(result["seconds"], float)
    json.dumps(result)  # the CLI stores it as JSON


def test_fts_has_one_row_per_entry_with_all_names(tmp_path):
    entries, relations = _kusuriya()
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)

    assert conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0] == len(entries)
    text = conn.execute("SELECT text FROM entries_fts WHERE entry_id = 'anilist:110'").fetchone()[0]
    assert text == "薬屋のひとりごと Kusuriya no Hitorigoto The Apothecary Diaries 薬屋"
    hits = conn.execute("SELECT entry_id, cluster_id FROM entries_fts WHERE entries_fts MATCH 'くすりや'").fetchall()
    assert {r[0] for r in hits} == {"syoboi:6900", "syoboi:7300"}
    assert {r[1] for r in hits} == {_cluster(conn, "anilist:101")}
    assert conn.execute("SELECT COUNT(*) FROM entries_fts WHERE entries_fts MATCH 'Apothecary'").fetchone()[0] == 2


def test_fts_text_also_holds_the_width_folded_names(tmp_path):
    entries = [
        _anilist(1, "anime", "ＳＰＹ×ＦＡＭＩＬＹ", title_romaji="SPY x FAMILY", title_english="Ｓｐｙ　Ｆａｍｉｌｙ"),
        _entry("syoboi", "9", "anime", "ﾄﾞﾗｺﾞﾝﾎﾞｰﾙ", title_reading="どらごんぼーる"),
        _anilist(3, "manga", "Abyss"),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)

    texts = dict(conn.execute("SELECT entry_id, text FROM entries_fts"))
    assert texts["anilist:1"] == "ＳＰＹ×ＦＡＭＩＬＹ SPY x FAMILY Ｓｐｙ　Ｆａｍｉｌｙ spy×family spy family"
    assert texts["syoboi:9"] == "ﾄﾞﾗｺﾞﾝﾎﾞｰﾙ どらごんぼーる ドラゴンボール"
    assert texts["anilist:3"] == "Abyss"  # nothing to add: lower() covers the case

    def hits(query: str) -> set[str]:
        return {r[0] for r in conn.execute("SELECT entry_id FROM entries_fts WHERE entries_fts MATCH ?", (query,))}

    assert hits('"spy family"') == {"anilist:1"}
    assert hits('"ドラゴン"') == {"syoboi:9"}
    assert hits('"ﾄﾞﾗｺﾞﾝ"') == {"syoboi:9"}  # the raw form is still searchable


# --------------------------------------------------------------------------- relations


def test_side_story_and_spin_off_do_not_merge_works(tmp_path):
    entries = [
        _anilist(1, "novel", "とある魔術の禁書目録", popularity=40000, start_date="2004-04-10"),
        _anilist(2, "anime", "とある魔術の禁書目録", popularity=150000, start_date="2008-10-05"),
        _anilist(3, "manga", "とある科学の超電磁砲", popularity=60000, start_date="2007-02-01"),
        _anilist(4, "anime", "とある科学の超電磁砲", popularity=200000, start_date="2009-10-03"),
        _anilist(5, "manga", "とある魔術の禁書目録 アンソロジー", popularity=500, start_date="2010-01-01"),
        _anilist(6, "anime", "とある魔術の禁書目録 総集編", popularity=800, start_date="2011-01-01"),
    ]
    relations = [
        ("anilist:1", "anilist:2", "ADAPTATION"),
        ("anilist:3", "anilist:4", "ADAPTATION"),
        ("anilist:2", "anilist:4", "SPIN_OFF"),
        ("anilist:4", "anilist:2", "SIDE_STORY"),
        ("anilist:1", "anilist:3", "ALTERNATIVE"),
        ("anilist:1", "anilist:5", "CHARACTER"),
        ("anilist:2", "anilist:6", "SUMMARY"),
        ("anilist:2", "anilist:6", "OTHER"),
    ]
    conn = _open(tmp_path, entries, relations)
    result = run_link(conn, today=TODAY)

    assert _members(conn, _cluster(conn, "anilist:1")) == {"anilist:1", "anilist:2"}
    assert _members(conn, _cluster(conn, "anilist:3")) == {"anilist:3", "anilist:4"}
    assert _members(conn, "anilist:5") == {"anilist:5"}
    assert _members(conn, "anilist:6") == {"anilist:6"}
    assert result["clusters"] == 4
    assert _summary(conn, "anilist:3")["title"] == "とある科学の超電磁砲"  # manga before anime


# --------------------------------------------------------------------------- title matching


def test_syoboi_confidence_by_year_and_synonym(tmp_path):
    entries = [
        _anilist(1, "anime", "ハイスコアガール", popularity=30000, start_date="2018-07-14"),
        _anilist(2, "anime", "ある魔女が死ぬまで", popularity=20000, start_date="2025-04-01"),
        _anilist(3, "anime", "この素晴らしい世界に祝福を！", synonyms=["このすば"], popularity=300000,
                 start_date="2016-01-14"),
        _anilist(4, "anime", "ダンジョン飯", popularity=250000, season_year=2024),  # no start date
        # 4 years off: same title only
        _entry("syoboi", "10", "anime", "ハイスコアガール", start_date="2022-04-01", start_precision="month"),
        # syoboi 略称 equals the AniList title, same year
        _entry("syoboi", "11", "anime", "ある魔女が死ぬまで 終わりの言葉と始まりの涙", synonyms=["ある魔女が死ぬまで"],
               start_date="2025-04-01", start_precision="month"),
        # syoboi title equals an AniList synonym, year unknown on the syoboi side
        _entry("syoboi", "12", "anime", "このすば"),
        # AniList has only season_year
        _entry("syoboi", "13", "anime", "ダンジョン飯", start_date="2024-01-01", start_precision="month"),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)

    assert _links(conn, "syoboi:10") == [("anilist:1", "title-exact", 0.7)]
    assert _links(conn, "syoboi:11") == [("anilist:2", "title-year", 0.85)]
    assert _links(conn, "syoboi:12") == [("anilist:3", "title-exact", 0.6)]
    assert _links(conn, "syoboi:13") == [("anilist:4", "title-year", 0.95)]
    # < 0.8 stays a candidate only
    assert _cluster(conn, "syoboi:10") == "syoboi:10"
    assert _cluster(conn, "syoboi:12") == "syoboi:12"
    assert _cluster(conn, "syoboi:11") == "anilist:2"
    assert _cluster(conn, "syoboi:13") == "anilist:4"


def test_web_novel_methods_and_threshold(tmp_path):
    entries = [
        _anilist(1, "novel", "転生したらスライムだった件", synonyms=["転スラ"], popularity=50000),
        _anilist(2, "novel", "魔導具師ダリヤはうつむかない", popularity=8000),
        _anilist(3, "manga", "シャングリラ・フロンティア〜クソゲーハンター、神ゲーに挑まんとす〜", popularity=40000),
        _anilist(4, "novel", "ありふれた職業で世界最強", synonyms=["ありふれた職業で世界最強 WEB版"], popularity=9000),
        _anilist(5, "novel", "本好きの下剋上", popularity=30000),
        _anilist(6, "manga", "本好きの下剋上", popularity=60000),
        _entry("narou", "n6316bn", "web-novel", "転生したらスライムだった件", popularity=900000),
        _entry("narou", "n1000a", "web-novel", "転スラ"),
        # AniList title is a proper prefix (>= 8 chars): only a candidate
        _entry("narou", "n2000a", "web-novel", "魔導具師ダリヤはうつむかないけど泣く"),
        # no AniList novel -> manga
        _entry("narou", "n6169dz", "web-novel", "シャングリラ・フロンティア〜クソゲーハンター、神ゲーに挑まんとす〜"),
        _entry("kakuyomu", "1177354054", "web-novel", "【書籍化】ありふれた職業で世界最強（WEB版）"),
        # the subtitle-stripped key matches; novel is preferred over the more popular manga
        _entry("narou", "n4830bu", "web-novel", "本好きの下剋上　～司書になるためには手段を選んでいられません～"),
        _entry("narou", "n3000a", "web-novel", "全く関係のない作品"),
    ]
    conn = _open(tmp_path, entries)
    result = run_link(conn, today=TODAY)

    assert _links(conn, "narou:n6316bn") == [("anilist:1", "title-exact", 0.9)]
    assert _links(conn, "narou:n1000a") == [("anilist:1", "synonym", 0.85)]
    assert _links(conn, "narou:n2000a") == [("anilist:2", "title-prefix", 0.6)]
    assert _links(conn, "narou:n6169dz") == [("anilist:3", "title-exact", 0.9)]
    assert _links(conn, "kakuyomu:1177354054") == [("anilist:4", "title-exact", 0.9)]
    assert _links(conn, "narou:n4830bu") == [("anilist:5", "title-exact", 0.9)]
    assert _links(conn, "narou:n3000a") == []

    assert _cluster(conn, "narou:n6316bn") == "anilist:1"
    assert _cluster(conn, "narou:n1000a") == "anilist:1"
    assert _cluster(conn, "narou:n2000a") == "narou:n2000a"  # prefix match is not merged
    assert _cluster(conn, "narou:n6169dz") == "anilist:3"
    assert result["linksByMethod"] == {"synonym": 1, "title-exact": 4, "title-prefix": 1}


def test_season_and_subtitle_markers_are_ignored(tmp_path):
    entries = [
        _anilist(1, "novel", "無職転生 ～異世界行ったら本気だす～", title_romaji="Mushoku Tensei: Isekai Ittara Honki Dasu",
                 popularity=40000),
        _anilist(2, "anime", "無職転生 ～異世界行ったら本気だす～", popularity=300000, start_date="2021-01-11"),
        _anilist(3, "anime", "無職転生Ⅱ ～異世界行ったら本気だす～", popularity=150000, start_date="2023-07-03"),
        _anilist(4, "anime", "Re:ゼロから始める異世界生活 3rd season", popularity=90000, start_date="2024-10-02"),
        _entry("narou", "n9669bk", "web-novel", "無職転生　- 異世界行ったら本気だす -", popularity=500000),
        _entry("syoboi", "6000", "anime", "無職転生Ⅱ ～異世界行ったら本気だす～", start_date="2023-07-01",
               start_precision="month"),
        _entry("syoboi", "7000", "anime", "Re:ゼロから始める異世界生活 第3期", start_date="2024-10-01",
               start_precision="month"),
    ]
    relations = [("anilist:1", "anilist:2", "ADAPTATION"), ("anilist:2", "anilist:3", "SEQUEL")]
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)

    assert title_key("無職転生 ～異世界行ったら本気だす～") == title_key("無職転生　- 異世界行ったら本気だす -")
    assert strip_subtitle("無職転生　- 異世界行ったら本気だす -") == "無職転生"
    assert base_title_key("【書籍化】追放された俺（web版）") == "追放された俺"
    assert _links(conn, "narou:n9669bk") == [("anilist:1", "title-exact", 0.9)]
    assert _links(conn, "syoboi:6000") == [("anilist:3", "title-year", 0.95)]
    assert _links(conn, "syoboi:7000") == [("anilist:4", "title-year", 0.95)]
    cid = _cluster(conn, "anilist:1")
    assert {"narou:n9669bk", "syoboi:6000"} <= _members(conn, cid)
    assert _summary(conn, cid)["title"] == "無職転生 ～異世界行ったら本気だす～"
    assert _summary(conn, "anilist:4")["title"] == "Re:ゼロから始める異世界生活"  # season marker dropped


def test_one_link_to_the_most_popular_candidate(tmp_path):
    entries = [
        _anilist(1, "anime", "ヒカルの碁", popularity=60000, start_date="2001-10-10"),
        _anilist(2, "anime", "ヒカルの碁", popularity=3000, start_date="2002-01-01", title_english="Hikaru no Go Special"),
        _anilist(3, "novel", "魔法使いの嫁", popularity=5000, start_date="2014-01-01"),
        _anilist(4, "novel", "魔法使いの嫁", popularity=5000, start_date="2020-06-01"),
        _entry("syoboi", "100", "anime", "ヒカルの碁", start_date="2001-10-01", start_precision="month"),
        # popularity tie -> the nearest start date
        _entry("kakuyomu", "200", "web-novel", "魔法使いの嫁", start_date="2019-12-01", start_precision="day"),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)

    assert _links(conn, "syoboi:100") == [("anilist:1", "title-year", 0.95)]
    assert _links(conn, "kakuyomu:200") == [("anilist:4", "title-exact", 0.9)]
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 2
    assert _cluster(conn, "anilist:2") == "anilist:2"  # the loser stays on its own


def test_books_link_by_series_and_kind_hint(tmp_path):
    entries = [
        _anilist(1, "novel", "魔女の旅々", popularity=12000),
        _book("100", "魔女の旅々 22", "魔女の旅々", "novel", "2025-03-14"),
        _book("101", "魔女の旅々 23", "魔女の旅々", "novel", "2026-01-15"),
        # a manga volume never links to a novel-only work
        _book("102", "魔女の旅々 8", "魔女の旅々", "manga", "2025-08-07"),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)

    assert _links(conn, "ndl:100") == [("anilist:1", "series", 0.85)]
    assert _links(conn, "ndl:101") == [("anilist:1", "series", 0.85)]
    assert _links(conn, "ndl:102") == []
    assert _members(conn, "anilist:1") == {"anilist:1", "ndl:100", "ndl:101"}
    assert _members(conn, "ndl:102") == {"ndl:102"}
    assert _summary(conn, "ndl:102")["title"] == "魔女の旅々"  # series name, not the volume title


def test_unlinked_books_group_by_series_kind_and_line(tmp_path):
    entries = [
        _book("1", "辺境の薬師 1", "辺境の薬師", "novel", "2025-11-10"),
        _book("2", "辺境の薬師 2", "辺境の薬師", "novel", "2026-03-10"),
        _book("3", "辺境の薬師 3", "辺境の薬師", "novel", "2026-07-10"),
        _book("4", "辺境の薬師 1", "辺境の薬師", "manga", "2026-05-01"),
        _book("5", "辺境の薬師 外伝", "辺境の薬師", "novel", "2026-08-01", line="外伝"),
        _book("6", "別の本", "別の本", "novel", "2026-02-01"),
    ]
    conn = _open(tmp_path, entries)
    result = run_link(conn, today=TODAY)

    assert _members(conn, "ndl:1") == {"ndl:1", "ndl:2", "ndl:3"}
    assert _members(conn, "ndl:4") == {"ndl:4"}
    assert _members(conn, "ndl:5") == {"ndl:5"}
    assert _members(conn, "ndl:6") == {"ndl:6"}
    assert result["clusters"] == 4
    assert result["largestCluster"] == {"id": "ndl:1", "title": "辺境の薬師", "size": 3}
    s = _summary(conn, "ndl:1")
    assert s["kinds"] == ["book"] and s["entry_count"] == 3
    assert (s["first_year"], s["last_year"]) == (2025, 2026)
    assert s["popularity"] == 0 and s["cover"] is None and s["anime_status"] is None


# --------------------------------------------------------------------------- summary


def test_next_date_prefers_the_nearest_with_month_start_for_partial_dates(tmp_path):
    entries = [
        _anilist(1, "anime", "未来の作品 第2期", status="NOT_YET_RELEASED", popularity=100,
                 start_date="2027-01-01", start_precision="month"),
        _anilist(2, "anime", "未来の作品", status="FINISHED", popularity=500, start_date="2024-04-01",
                 extra={"nextAiringEpisode": {"date": "2026-10-01", "time": "23:00", "episode": 12}}),  # stale
        _anilist(3, "novel", "未来の作品", popularity=50),
        _book("10", "未来の作品 5", "未来の作品", "novel", "2026-10-01", precision="month"),  # this month
        _book("11", "未来の作品 4", "未来の作品", "novel", "2026-09-01", precision="month"),  # last month
        _book("12", "未来の作品 6", "未来の作品", "novel", "2026-10-20"),
    ]
    relations = [("anilist:3", "anilist:2", "ADAPTATION"), ("anilist:2", "anilist:1", "SEQUEL")]
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)

    s = _summary(conn, _cluster(conn, "anilist:1"))
    # 2026-10 (month) still covers today and sorts by its first day, before 10-20
    assert (s["next_date"], s["next_precision"], s["next_label"]) == ("2026-10-01", "month", "未来の作品 5")
    assert s["anime_status"] == "NOT_YET_RELEASED"


def test_next_date_from_episode_unaired_anime_and_year_precision(tmp_path):
    entries = [
        _anilist(1, "anime", "放送中", status="RELEASING", popularity=10, start_date="2026-10-03",
                 extra={"nextAiringEpisode": {"date": "2026-10-17", "time": "01:00", "episode": 3}}),
        _anilist(2, "anime", "放送中 第2期", status="NOT_YET_RELEASED", popularity=5, start_date="2027-04-01",
                 start_precision="month"),
        _anilist(10, "anime", "来年の作品", status="NOT_YET_RELEASED", popularity=5, start_date="2026-01-01",
                 start_precision="year"),
        _anilist(11, "anime", "来年の作品 特別編", status="NOT_YET_RELEASED", popularity=5, start_date="2026-11-03",
                 start_precision="day"),
        _anilist(20, "anime", "終わった作品", status="FINISHED", popularity=5, start_date="2020-01-01"),
        # syoboi row joined to a RELEASING anime is not "unaired" even though its month covers today
        _anilist(30, "anime", "今期の作品", status="RELEASING", popularity=5, start_date="2026-10-05"),
        _entry("syoboi", "30", "anime", "今期の作品", start_date="2026-10-01", start_precision="month"),
        # unknown status (syoboi only) with a future month counts as unaired
        _entry("syoboi", "40", "anime", "秋の新作", start_date="2026-12-01", start_precision="month"),
    ]
    relations = [("anilist:1", "anilist:2", "SEQUEL"), ("anilist:10", "anilist:11", "SEQUEL")]
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)

    s = _summary(conn, "anilist:1")
    assert (s["next_date"], s["next_precision"], s["next_label"]) == ("2026-10-17", "day", "第3話")
    s = _summary(conn, "anilist:10")
    assert (s["next_date"], s["next_precision"], s["next_label"]) == ("2026-01-01", "year", "来年の作品")
    s = _summary(conn, "anilist:20")
    assert (s["next_date"], s["next_precision"], s["next_label"]) == (None, None, None)
    s = _summary(conn, _cluster(conn, "syoboi:30"))
    assert s["next_date"] is None and s["anime_status"] == "RELEASING"
    s = _summary(conn, "syoboi:40")
    assert (s["next_date"], s["next_precision"], s["next_label"]) == ("2026-12-01", "month", "秋の新作")
    assert s["anime_status"] is None


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["FINISHED", "RELEASING", "NOT_YET_RELEASED"], "RELEASING"),
        (["FINISHED", "NOT_YET_RELEASED", "HIATUS"], "NOT_YET_RELEASED"),
        (["CANCELLED", "HIATUS", "FINISHED"], "HIATUS"),
        (["CANCELLED", "FINISHED"], "FINISHED"),
        (["CANCELLED"], "CANCELLED"),
    ],
)
def test_anime_status_priority(tmp_path, statuses, expected):
    entries = [
        _anilist(i + 1, "anime", f"作品 第{i + 1}期", status=status, popularity=10, start_date=f"201{i}-04-01")
        for i, status in enumerate(statuses)
    ]
    entries.append(_anilist(99, "manga", "作品", status="RELEASING", popularity=10))
    relations = [(f"anilist:{i + 1}", f"anilist:{i + 2}", "SEQUEL") for i in range(len(statuses) - 1)]
    relations.append(("anilist:99", "anilist:1", "ADAPTATION"))
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)

    s = _summary(conn, _cluster(conn, "anilist:1"))
    assert s["entry_count"] == len(statuses) + 1
    assert s["anime_status"] == expected
    assert s["title"] == "作品"


def test_representative_title_skips_novelizations(tmp_path):
    entries = [
        _anilist(1, "manga", "鬼滅の刃", popularity=200000, source_material="ORIGINAL", cover="https://img/manga.jpg"),
        _anilist(2, "novel", "鬼滅の刃 しあわせの花", popularity=3000, source_material="MANGA"),
        _anilist(3, "anime", "鬼滅の刃", popularity=500000, cover=None),
    ]
    relations = [("anilist:1", "anilist:2", "ADAPTATION"), ("anilist:1", "anilist:3", "ADAPTATION")]
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)

    s = _summary(conn, "anilist:1")
    assert s["title"] == "鬼滅の刃"
    assert s["popularity"] == 500000
    assert s["cover"] == "https://img/manga.jpg"  # the most popular AniList entry that has a cover


# --------------------------------------------------------------------------- stability


def test_rerun_gives_the_same_tables_and_stable_cluster_ids(tmp_path):
    entries, relations = _kusuriya()
    conn = _open(tmp_path, entries, relations)
    first = run_link(conn, today=TODAY)
    before = _dump(conn)
    second = run_link(conn, today=TODAY)
    assert _dump(conn) == before
    assert {k: v for k, v in first.items() if k != "seconds"} == {k: v for k, v in second.items() if k != "seconds"}

    # A new member with a larger id does not move the work.
    db.upsert_entries(conn, [_anilist(900, "anime", "薬屋のひとりごと 第4期", popularity=1, start_date="2028-01-01")])
    conn.execute("INSERT INTO relations VALUES ('anilist:112', 'anilist:900', 'SEQUEL')")
    conn.commit()
    run_link(conn, today=TODAY)
    assert _cluster(conn, "anilist:900") == before["clusters"][0][1]
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == len(before["links"])


def test_rebuild_drops_stale_rows(tmp_path):
    entries, relations = _kusuriya()
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)
    conn.execute("DELETE FROM entries WHERE source = 'ndl'")
    conn.commit()
    result = run_link(conn, today=TODAY)
    assert result["linksByMethod"] == {"title-exact": 1, "title-year": 2}
    assert conn.execute("SELECT COUNT(*) FROM clusters WHERE entry_id LIKE 'ndl:%'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0] == len(entries) - 3
    assert not conn.in_transaction


def test_empty_catalog(tmp_path):
    conn = db.connect(tmp_path / "catalog.db")
    result = run_link(conn, today=TODAY)
    assert result["entries"] == 0 and result["clusters"] == 0 and result["links"] == 0
    assert result["largestCluster"] == {"id": None, "title": None, "size": 0}


# --------------------------------------------------------------------------- scale

_SYLLABLES = list("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん")
_WORDS = [
    "異世界", "転生", "魔王", "勇者", "令嬢", "追放", "最強", "学園", "聖女", "賢者", "辺境", "領主",
    "薬師", "錬金術", "竜", "剣", "魔法", "迷宮", "冒険者", "王女", "騎士", "精霊", "姫", "旅",
]
_LATIN = [
    "isekai", "tensei", "maou", "yuusha", "reijou", "tsuihou", "saikyou", "gakuen", "seijo", "kenja",
    "the", "of", "dragon", "sword", "magic", "dungeon", "knight", "princess", "journey", "spirit",
]


def _title(rng: random.Random, serial: int) -> str:
    words = "".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 3)))
    tail = "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 6)))
    return f"{words}の{tail}{serial:06d}"


def _fill_synthetic(conn, *, anilist: int, others: int, relations: int, seed: int = 7) -> None:
    """Franchises of AniList rows (novel / manga / anime seasons) plus rows from the other sources."""
    rng = random.Random(seed)
    now = db.utc_now()
    rows: list[tuple] = []
    rels: set[tuple[str, str, str]] = set()
    franchises: list[dict[str, list[tuple[str, str, int]]]] = []

    def latin(words: int) -> str:
        return " ".join(rng.choice(_LATIN) for _ in range(words)).title()

    def add(source, ext, kind, title, *, synonyms=(), status=None, start=None, precision=None,
            season=None, season_year=None, popularity=None, cover=None, extra=None, reading=None):
        is_anilist = source == "anilist"
        rows.append((
            f"{source}:{ext}", source, str(ext), kind, title, title if is_anilist else None,
            f"{latin(5)} {ext}" if is_anilist else None, f"{latin(4)} {ext}" if is_anilist else None,
            reading, json.dumps(list(synonyms), ensure_ascii=False), status, start, precision,
            season, season_year, popularity, cover, json.dumps(extra or {}, ensure_ascii=False), now,
        ))

    next_id = 1
    while next_id <= anilist:
        base = _title(rng, len(franchises))
        year = rng.randint(1990, 2027)
        fr: dict[str, list[tuple[str, str, int]]] = {"novel": [], "manga": [], "anime": []}
        size = rng.randint(3, 11)
        for n in range(size):
            if next_id > anilist:
                break
            kind = "novel" if n == 0 else "manga" if n < 3 else "anime"
            title = base if kind != "anime" or n == 3 else f"{base} 第{n - 2}期"
            start_year = min(year + n, 2027)
            status = rng.choice(["FINISHED", "FINISHED", "RELEASING", "NOT_YET_RELEASED", "HIATUS"])
            extra = {"idMal": next_id, "streaming": ["Netflix", "Crunchyroll"]}
            if kind == "anime" and status == "RELEASING":
                extra["nextAiringEpisode"] = {"date": f"2026-{rng.randint(9, 12):02d}-{rng.randint(1, 28):02d}",
                                              "time": "23:00", "episode": rng.randint(2, 12)}
            synonyms = [f"{base[:4]}{n}", f"{latin(3)} {next_id}", f"{base}（{latin(2)}）", f"{latin(6)}"]
            add("anilist", next_id, kind, title, synonyms=synonyms, status=status,
                start=f"{start_year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", precision="day",
                season=rng.choice(["WINTER", "SPRING", "SUMMER", "FALL"]) if kind == "anime" else None,
                season_year=start_year if kind == "anime" else None, popularity=rng.randint(0, 300000),
                cover=f"https://img/{next_id}.jpg")
            fr[kind].append((f"anilist:{next_id}", title, start_year))
            next_id += 1
        franchises.append(fr)

    anilist_ids = [r[0] for r in rows]
    for fr in franchises:
        members = [m[0] for kind in ("novel", "manga", "anime") for m in fr[kind]]
        for a, b in zip(members, members[1:]):
            rels.add((a, b, rng.choice(["ADAPTATION", "SEQUEL", "SOURCE", "PREQUEL", "PARENT", "SIDE_STORY"])))
    while len(rels) < relations:
        a = rng.choice(anilist_ids)
        if rng.random() < 0.3:
            b = f"anilist:{rng.randint(anilist + 1, anilist * 3)}"  # outside the catalog
            rtype = rng.choice(["ADAPTATION", "SEQUEL", "OTHER"])
        else:
            b = rng.choice(anilist_ids)
            rtype = rng.choice(["SIDE_STORY", "SPIN_OFF", "ALTERNATIVE", "CHARACTER", "OTHER", "SUMMARY"])
        rels.add((a, b, rtype))

    shares = {"syoboi": 0.27, "narou": 0.2, "kakuyomu": 0.13}
    counts = {source: int(others * share) for source, share in shares.items()}
    counts["ndl"] = others - sum(counts.values())
    with_anime = [fr for fr in franchises if fr["anime"]]
    with_novel = [fr for fr in franchises if fr["novel"]]
    for i in range(counts["syoboi"]):
        if rng.random() < 0.7:
            _, title, year = rng.choice(rng.choice(with_anime)["anime"])
        else:
            title, year = _title(rng, 900000 + i), rng.randint(1990, 2026)
        add("syoboi", i, "anime", title, synonyms=[title[:3]], start=f"{year + rng.choice([0, 0, 1, 3])}-04-01",
            precision="month", reading="".join(rng.choice(_SYLLABLES) for _ in range(12)), extra={"cat": 1})
    for source in ("narou", "kakuyomu"):
        for i in range(counts[source]):
            if rng.random() < 0.5:
                _, title, _ = rng.choice(rng.choice(with_novel)["novel"])
                title = rng.choice([title, f"{title} ～{_title(rng, i)}～", f"{title}{_title(rng, i)}"])
            else:
                title = f"{_title(rng, 800000 + i)}　- {_title(rng, i)} -"
            add(source, f"{source[0]}{i}", "web-novel", title, popularity=rng.randint(0, 500000),
                start=f"20{rng.randint(10, 26)}-01-01", precision="day")
    for i in range(counts["ndl"]):
        hint = rng.choice(["novel", "manga"])
        if rng.random() < 0.5:
            fr = rng.choice(franchises)
            pool = fr["novel"] if hint == "novel" else fr["manga"]
            series = pool[0][1] if pool else _title(rng, 700000 + i)
        else:
            series = _title(rng, 700000 + i // 4)
        add("ndl", f"978{i:010d}", "book", f"{series} {rng.randint(1, 30)}", reading="",
            start=f"202{rng.randint(5, 7)}-{rng.randint(1, 12):02d}-01", precision=rng.choice(["day", "month"]),
            extra={"series": series, "kindHint": hint, "line": rng.choice(["", "", "", "外伝"]), "isbn": f"978{i}"})

    conn.executemany(
        "INSERT INTO entries (id, source, external_id, kind, title, title_native, title_romaji, title_english, "
        "title_reading, synonyms, status, start_date, start_precision, season, season_year, popularity, cover, "
        "extra, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany("INSERT INTO relations (from_id, to_id, relation_type) VALUES (?, ?, ?)", sorted(rels))
    conn.commit()


def _check_scale(tmp_path, *, anilist: int, others: int, relations: int, budget: float) -> dict:
    conn = db.connect(tmp_path / "catalog.db")
    _fill_synthetic(conn, anilist=anilist, others=others, relations=relations)
    timings = []
    for _ in range(2):  # the second run also clears a full set of tables
        started = time.perf_counter()
        result = run_link(conn, today=TODAY)
        timings.append(time.perf_counter() - started)
        if len(timings) == 1:
            first = {k: v for k, v in result.items() if k != "seconds"}
    total = anilist + others

    assert max(timings) < budget, f"run_link took {timings} s for {total} rows"
    assert {k: v for k, v in result.items() if k != "seconds"} == first
    assert result["entries"] == total
    assert conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0] == total
    assert conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0] == total
    assert conn.execute("SELECT COUNT(DISTINCT cluster_id) FROM clusters").fetchone()[0] == result["clusters"]
    assert conn.execute("SELECT COUNT(*) FROM cluster_summary").fetchone()[0] == result["clusters"]
    assert conn.execute("SELECT SUM(entry_count) FROM cluster_summary").fetchone()[0] == total
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == result["links"] == sum(
        result["linksByMethod"].values()
    )
    # cluster ids are member ids, and every link starts from a non-AniList row
    assert conn.execute(
        "SELECT COUNT(*) FROM clusters c LEFT JOIN entries e ON e.id = c.cluster_id WHERE e.id IS NULL"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM links WHERE a_id LIKE 'anilist:%' OR b_id NOT LIKE 'anilist:%'"
                        ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM (SELECT a_id FROM links GROUP BY a_id HAVING COUNT(*) > 1)"
                        ).fetchone()[0] == 0
    assert result["links"] > others * 0.3
    assert set(result["linksByMethod"]) >= {"title-year", "title-exact", "series"}
    print(f"\nrun_link {total} rows: {timings[0]:.1f}s / rerun {timings[1]:.1f}s {json.dumps(result, ensure_ascii=False)}")
    return result


def test_scale_10k_rows(tmp_path):
    _check_scale(tmp_path, anilist=7_000, others=3_000, relations=10_000, budget=30)


@pytest.mark.skipif(not RUN_SLOW, reason="set TRIPTYCH_SLOW=1 to run the 100k-row timing")
def test_scale_100k_rows_within_two_minutes(tmp_path):
    _check_scale(tmp_path, anilist=70_000, others=30_000, relations=100_000, budget=120)


# --------------------------------------------------------------------------- regressions found on the real catalog


def test_parent_relation_does_not_bridge_two_franchises(tmp_path):
    # 実データ: アヌシー映画祭の記念特番が ONE PIECE とドラゴンボールの両方に PARENT でつながっていた
    entries = [
        _anilist(1, "manga", "ONE PIECE", popularity=200000, source_material="ORIGINAL"),
        _anilist(2, "anime", "One Piece: Annecy Festival 60th Anniversary", popularity=100),
        _anilist(3, "anime", "Dragon Ball: Annecy Festival 60th Anniversary", popularity=100),
        _anilist(4, "manga", "ドラゴンボール", popularity=50000, source_material="ORIGINAL"),
    ]
    relations = [
        ("anilist:2", "anilist:1", "PARENT"),
        ("anilist:3", "anilist:2", "PARENT"),
        ("anilist:3", "anilist:4", "PARENT"),
    ]
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)
    assert _cluster(conn, "anilist:1") != _cluster(conn, "anilist:4")


def test_web_novel_links_to_part_split_anilist_novel_by_base_title(tmp_path):
    # 実データ: AniList の原作は部ごと、なろう版は1作品
    entries = [
        _anilist(87383, "novel", "本好きの下剋上 ~司書になるためには手段を選んでいられません~ 第一部「兵士の娘」",
                 popularity=7876, source_material="WEB_NOVEL"),
        _anilist(110800, "novel", "本好きの下剋上~司書になるためには手段を選んでいられません~ 第二部「神殿の巫女見習い」",
                 popularity=2957, source_material="WEB_NOVEL"),
        _entry("narou", "n4830bu", "web-novel", "本好きの下剋上　～司書になるためには手段を選んでいられません～"),
        _entry("narou", "n7835cj", "web-novel", "本好きの下剋上　SS置き場"),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)
    assert _links(conn, "narou:n4830bu") == [("anilist:87383", "base-title", 0.82)]
    assert _cluster(conn, "narou:n4830bu") == _cluster(conn, "anilist:87383")
    assert _cluster(conn, "narou:n7835cj") != _cluster(conn, "anilist:87383")


def test_representative_prefers_the_original_over_a_novelization(tmp_path):
    entries = [
        _anilist(1, "manga", "ONE PIECE", popularity=234820, source_material="ORIGINAL"),
        _anilist(2, "novel", "ONE PIECE novel A", popularity=3000, source_material="MANGA"),
        _anilist(3, "anime", "ONE PIECE", popularity=300000, source_material="MANGA"),
    ]
    relations = [("anilist:2", "anilist:1", "SOURCE"), ("anilist:3", "anilist:1", "SOURCE")]
    conn = _open(tmp_path, entries, relations)
    run_link(conn, today=TODAY)
    assert _summary(conn, _cluster(conn, "anilist:1"))["title"] == "ONE PIECE"


def test_author_mismatch_keeps_short_generic_titles_as_candidates(tmp_path):
    entries = [
        _anilist(1, "manga", "約束", popularity=500, creators=["つむらちた"]),
        _entry("narou", "n1", "web-novel", "約束", creators=["神村 結美"]),
        _anilist(2, "manga", "MONSTERS", popularity=9000, creators=["尾田栄一郎"]),
        _entry("narou", "n2", "web-novel", "MONSTERＳ～モンスターズ～", creators=["RAI"]),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)
    assert _links(conn, "narou:n1") == [("anilist:1", "title-exact", 0.6)]
    assert _cluster(conn, "narou:n1") != _cluster(conn, "anilist:1")
    assert _cluster(conn, "narou:n2") != _cluster(conn, "anilist:2")


def test_author_match_strengthens_and_tolerates_name_variants(tmp_path):
    entries = [
        _anilist(1, "novel", "私は敵になりません！", popularity=500, creators=["佐槻奏多", "藤未都也"]),
        _entry("narou", "n1", "web-novel", "私は敵になりません！", creators=["奏多"]),
        _anilist(2, "novel", "ネットの『推し』とリアルの『推し』が隣に引っ越してきた", popularity=500, creators=["遥透子"]),
        _entry("narou", "n2", "web-novel", "【書籍化】ネットの『推し』とリアルの『推し』が隣に引っ越してきた",
               creators=["遥透子@5月に3冊出ます！"]),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)
    assert _links(conn, "narou:n1") == [("anilist:1", "title-exact", 0.95)]
    assert _links(conn, "narou:n2") == [("anilist:2", "title-exact", 0.95)]


def test_long_exact_title_survives_an_author_mismatch(tmp_path):
    # AniList の漫画には漫画家しか載らないことが多い
    title = "弱気MAX令嬢なのに、辣腕婚約者様の様子がおかしいのです"
    entries = [
        _anilist(1, "manga", title, popularity=500, creators=["村田あじ"]),
        _entry("narou", "n1", "web-novel", title, creators=["小田 ヒロ"]),
    ]
    conn = _open(tmp_path, entries)
    run_link(conn, today=TODAY)
    assert _cluster(conn, "narou:n1") == _cluster(conn, "anilist:1")
