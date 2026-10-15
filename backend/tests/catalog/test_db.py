from backend.catalog import db
from backend.catalog.text import fold_for_search, normalize_title, search_text, strip_season, title_key


def test_upsert_is_idempotent_and_updates(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    e = db.Entry(source="anilist", external_id="1", kind="anime", title="A", genres=["Drama"])
    db.upsert_entries(conn, [e])
    e.title = "B"
    db.upsert_entries(conn, [e])
    rows = conn.execute("SELECT id, title, genres FROM entries").fetchall()
    assert [(r["id"], r["title"]) for r in rows] == [("anilist:1", "B")]
    assert db.row_to_dict(rows[0])["genres"] == ["Drama"]


def test_relations_and_slices(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    db.replace_relations(conn, "anilist:1", [("anilist:2", "SEQUEL"), ("anilist:3", "ADAPTATION")])
    db.replace_relations(conn, "anilist:1", [("anilist:2", "SEQUEL")])
    assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1
    assert not db.is_slice_done(conn, "anilist", "ANIME:2024")
    db.mark_slice(conn, "anilist", "ANIME:2024", "done", fetched=10)
    assert db.is_slice_done(conn, "anilist", "ANIME:2024")
    db.reset_source(conn, "anilist")
    assert not db.is_slice_done(conn, "anilist", "ANIME:2024")


def test_fts_trigram(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    conn.execute("INSERT INTO entries_fts (entry_id, cluster_id, text) VALUES ('x','x','本好きの下剋上')")
    assert conn.execute("SELECT entry_id FROM entries_fts WHERE entries_fts MATCH '下剋上'").fetchall()


def test_title_helpers():
    assert normalize_title("薬屋の ひとりごと！") == "薬屋のひとりごと"
    assert strip_season("薬屋のひとりごと 第3期") == "薬屋のひとりごと"
    assert strip_season("無職転生Ⅱ") == "無職転生"
    assert title_key("Re:ゼロから始める異世界生活 2nd season") == title_key("Re:ゼロから始める異世界生活")


def test_fold_for_search_absorbs_width_and_case_but_keeps_punctuation():
    assert fold_for_search("Ｒｅ：ゼロ") == "re:ゼロ"  # full-width latin and colon
    assert fold_for_search("ﾄﾞﾗｺﾞﾝﾎﾞｰﾙ") == "ドラゴンボール"  # half-width kana, voiced marks join their kana
    assert fold_for_search("無職転生Ⅱ") == "無職転生ii"  # compatibility characters
    assert fold_for_search("  The   Apothecary　Diaries\t") == "the apothecary diaries"
    assert fold_for_search("★!?") == "★!?"
    assert fold_for_search("") == "" and fold_for_search(None) == ""


def test_search_text_lists_the_names_then_the_folded_forms_that_differ():
    assert search_text(["薬屋のひとりごと", "Kusuriya no Hitorigoto", None, "", " 薬屋のひとりごと "]) == (
        "薬屋のひとりごと Kusuriya no Hitorigoto"
    )  # an ASCII name that differs only by case is found through SQLite's lower() instead
    assert search_text(["ＳＰＹ×ＦＡＭＩＬＹ", "ﾄﾞﾗｺﾞﾝ"]) == "ＳＰＹ×ＦＡＭＩＬＹ ﾄﾞﾗｺﾞﾝ spy×family ドラゴン"
    assert search_text(["Re:ゼロ"]) == "Re:ゼロ"
    assert search_text(["Ōkami"]) == "Ōkami ōkami"  # lower() leaves non-ASCII capitals alone
    assert search_text(["A  B"]) == "A  B a b"
    assert search_text(["Ｒｅ", "ＲＥ"]) == "Ｒｅ ＲＥ re"  # equal folded forms are listed once
    assert search_text([]) == ""
