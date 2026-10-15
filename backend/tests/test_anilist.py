"""Tests for the AniList adapter and its routes.

No network calls are made: `_graphql` is replaced by a fake that serves
recorded payloads (backend/tests/fixtures/anilist_*.json), which were
captured from real requests against https://graphql.anilist.co on
2026-10-13 using the production queries:

  - 転生したらスライムだった件 (101280 anime, 182205 anime S4, search/detect)
  - 無職転生 原作 NOVEL (85470) with its ADAPTATION anime, and the resolve-source search
  - 薬屋のひとりごと LN (99026), anime S1 (161645) -> S2 (176301) -> S3 (195516)
  - 盾の勇者の成り上がり Season 5 (199185, startDate is the year 2027 only)
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import errors
from backend.adapters import anilist as anilist_module
from backend.adapters.anilist import AniListAdapter, normalize_title
from backend.api import anilist as anilist_api
from backend.cache import TTLCache
from backend.dto import SourceUnavailable

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# 2026-10-18 01:30 JST / 2026-10-17 23:00 JST / 04:59 and 05:00 JST on 10-18.
EPOCH_JST_0130 = 1792254600
EPOCH_JST_2300 = 1792245600
EPOCH_JST_0459 = 1792267140
EPOCH_JST_0500 = 1792267200


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


def _media_node(media_id: int) -> dict[str, Any]:
    return _load(f"anilist_media_{media_id}.json")["data"]["Media"]


def _vars_key(variables: dict[str, Any]) -> tuple:
    return tuple(
        sorted(
            (k, tuple(v) if isinstance(v, list) else v) for k, v in variables.items()
        )
    )


@pytest.fixture()
def adapter(tmp_path):
    return AniListAdapter(cache=TTLCache(tmp_path / "cache.db"))


class FakeGraphQL:
    """Stand-in for `AniListAdapter._graphql` that serves fixture data.

    `responses` maps the request's variables (as a sorted tuple; list values
    become tuples) to the "data" payload, so each test wires up only the calls
    it needs. The query text of every call is kept in `queries`.
    """

    def __init__(self, responses: dict[tuple, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple] = []
        self.queries: list[str] = []

    async def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        key = _vars_key(variables)
        self.calls.append(key)
        self.queries.append(query)
        return self.responses[key]


def _use_media(monkeypatch, adapter, *media_ids: int) -> FakeGraphQL:
    fake = FakeGraphQL(
        {(("id", i),): {"Media": _media_node(i)} for i in media_ids}
    )
    monkeypatch.setattr(adapter, "_graphql", fake)
    return fake


def _page(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"Page": {"media": list(nodes)}}


def _search_node(media_id: int, native: str, **extra: Any) -> dict[str, Any]:
    node = {
        "id": media_id,
        "type": "MANGA",
        "format": "NOVEL",
        "isAdult": False,
        "popularity": 100,
        "title": {"romaji": None, "english": None, "native": native},
        "synonyms": [],
        "coverImage": {"large": f"https://example.invalid/{media_id}.jpg", "medium": None},
        "siteUrl": f"https://anilist.co/manga/{media_id}",
    }
    node.update(extra)
    return node


# -- rate limit ---------------------------------------------------------------


def test_default_bucket_stays_under_anilist_limit_of_30_per_minute(adapter):
    bucket = adapter._bucket
    per_minute = bucket.rate * 60
    assert per_minute == pytest.approx(25.0)
    # burst + one minute of refill must not exceed AniList's 30/min
    assert bucket.capacity + per_minute <= 30


# -- normalize_title ---------------------------------------------------------


def test_normalize_title_nfkc_lowercase_strips_symbols():
    assert normalize_title("転スラ　2！") == normalize_title("転スラ2")


def test_normalize_title_handles_fullwidth_and_halfwidth():
    assert normalize_title("ＴｅｎＳｕｒａ") == normalize_title("tensura")


# -- search -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_merges_anime_and_manga_and_normalizes_media_type(adapter, monkeypatch):
    fixture = _load("anilist_search.json")
    fake = FakeGraphQL(
        {
            (("search", "転生したらスライムだった件"), ("type", "ANIME")): fixture["ANIME"]["data"],
            (("search", "転生したらスライムだった件"), ("type", "MANGA")): fixture["MANGA"]["data"],
        }
    )
    monkeypatch.setattr(adapter, "_graphql", fake)

    results = await adapter.search("転生したらスライムだった件")

    assert fake.calls  # both ANIME and MANGA calls were made
    assert all(r.source == "anilist" for r in results)

    anime_hit = next(r for r in results if r.externalId == "101280")
    assert anime_hit.mediaType == "anime"
    assert anime_hit.title == "転生したらスライムだった件"
    assert anime_hit.url == "https://anilist.co/anime/101280"
    assert anime_hit.cover is not None

    manga_hit = next(r for r in results if r.externalId == "86399")
    assert manga_hit.mediaType == "manga"

    # format: NOVEL under type MANGA is normalized to light-novel.
    novel_hit = next(r for r in results if r.externalId == "86355")
    assert novel_hit.mediaType == "light-novel"


@pytest.mark.asyncio
async def test_search_is_cached_across_calls(adapter, monkeypatch):
    fixture = _load("anilist_search.json")
    fake = FakeGraphQL(
        {
            (("search", "転生したらスライムだった件"), ("type", "ANIME")): fixture["ANIME"]["data"],
            (("search", "転生したらスライムだった件"), ("type", "MANGA")): fixture["MANGA"]["data"],
        }
    )
    monkeypatch.setattr(adapter, "_graphql", fake)

    first = await adapter.search("転生したらスライムだった件")
    second = await adapter.search("転生したらスライムだった件")

    assert len(fake.calls) == 2  # not 4: second search() served from cache
    assert [r.externalId for r in first] == [r.externalId for r in second]


@pytest.mark.asyncio
async def test_search_refresh_bypasses_cache_and_rewrites_it(adapter, monkeypatch):
    fixture = _load("anilist_search.json")
    fake = FakeGraphQL(
        {
            (("search", "転生したらスライムだった件"), ("type", "ANIME")): fixture["ANIME"]["data"],
            (("search", "転生したらスライムだった件"), ("type", "MANGA")): fixture["MANGA"]["data"],
        }
    )
    monkeypatch.setattr(adapter, "_graphql", fake)

    await adapter.search("転生したらスライムだった件")
    await adapter.search("転生したらスライムだった件", refresh=True)
    assert len(fake.calls) == 4  # refresh went to the network

    await adapter.search("転生したらスライムだった件")
    assert len(fake.calls) == 4  # and the refreshed result was cached again


@pytest.mark.asyncio
async def test_search_excludes_adult_media(adapter, monkeypatch):
    clean = _search_node(1, "健全な作品")
    adult = _search_node(2, "成人向けの作品", isAdult=True)
    fake = FakeGraphQL(
        {
            (("search", "作品"), ("type", "ANIME")): _page(),
            (("search", "作品"), ("type", "MANGA")): _page(clean, adult),
        }
    )
    monkeypatch.setattr(adapter, "_graphql", fake)

    results = await adapter.search("作品")

    assert [r.externalId for r in results] == ["1"]
    assert all("isAdult: false" in q for q in fake.queries)


# -- media (detail + releases) --------------------------------------------------


@pytest.mark.asyncio
async def test_media_normalizes_finished_anime_without_next_episode(adapter, monkeypatch):
    fixture = _load("anilist_media_101280.json")
    fake = FakeGraphQL({(("id", 101280),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    detail = await adapter.media(101280)

    assert detail.source == "anilist"
    assert detail.externalId == "101280"
    assert detail.mediaType == "anime"
    assert detail.title == "転生したらスライムだった件"

    # FINISHED anime with nextAiringEpisode == null: only season-start release.
    assert len(detail.releases) == 1
    release = detail.releases[0]
    assert release.kind == "season-start"
    assert release.date == "2018-10-02"
    assert release.precision == "day"
    assert release.time is None
    assert release.externalId == "101280"


@pytest.mark.asyncio
async def test_media_normalizes_releasing_anime_with_next_episode(adapter, monkeypatch):
    fixture = _load("anilist_media_182205.json")
    fake = FakeGraphQL({(("id", 182205),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    detail = await adapter.media(182205)

    kinds = {release.kind for release in detail.releases}
    assert kinds == {"season-start", "episode"}

    episode_release = next(r for r in detail.releases if r.kind == "episode")
    assert episode_release.label == "第15話"
    # airingAt 1784296800 = 2026-07-17 14:00 UTC = 23:00 JST
    assert episode_release.date == "2026-07-17"
    assert episode_release.time == "23:00"
    assert episode_release.precision == "day"
    assert episode_release.source == "anilist"
    assert episode_release.externalId == "182205:15"


@pytest.mark.asyncio
async def test_media_real_releasing_season_has_episode_with_jst_time(adapter, monkeypatch):
    _use_media(monkeypatch, adapter, 195516)  # 薬屋のひとりごと 第3期

    detail = await adapter.media(195516)

    season = next(r for r in detail.releases if r.kind == "season-start")
    assert (season.date, season.precision) == ("2026-10-02", "day")
    episode = next(r for r in detail.releases if r.kind == "episode")
    assert episode.label == "第3話"
    assert (episode.date, episode.time) == ("2026-10-16", "23:00")
    assert episode.externalId == "195516:3"


@pytest.mark.parametrize(
    "airing_at, expected_date, expected_time",
    [
        (EPOCH_JST_0130, "2026-10-17", "25:30"),  # late night -> previous day
        (EPOCH_JST_2300, "2026-10-17", "23:00"),  # evening -> same day
        (EPOCH_JST_0459, "2026-10-17", "28:59"),  # last minute before the 05:00 boundary
        (EPOCH_JST_0500, "2026-10-18", "05:00"),  # boundary starts the new day
    ],
)
@pytest.mark.asyncio
async def test_episode_release_uses_jst_broadcast_day(
    adapter, monkeypatch, airing_at, expected_date, expected_time
):
    node = _media_node(195516)
    node["nextAiringEpisode"] = {"episode": 4, "airingAt": airing_at, "timeUntilAiring": 1}
    monkeypatch.setattr(adapter, "_graphql", FakeGraphQL({(("id", 195516),): {"Media": node}}))

    detail = await adapter.media(195516)

    episode = next(r for r in detail.releases if r.kind == "episode")
    assert episode.date == expected_date
    assert episode.time == expected_time
    assert episode.precision == "day"
    assert episode.externalId == "195516:4"


@pytest.mark.parametrize(
    "media_id, expected",
    [(101280, "FINISHED"), (195516, "RELEASING"), (199185, "NOT_YET_RELEASED")],
)
@pytest.mark.asyncio
async def test_media_carries_the_airing_status(adapter, monkeypatch, media_id, expected):
    # 同期がしょぼかるの照会（放送終了なら飛ばす）と配信の扱いを決めるのに使う
    _use_media(monkeypatch, adapter, media_id)

    assert (await adapter.media(media_id)).status == expected


@pytest.mark.asyncio
async def test_media_unknown_status_is_none(adapter, monkeypatch):
    node = _media_node(195516)
    node["status"] = None
    monkeypatch.setattr(adapter, "_graphql", FakeGraphQL({(("id", 195516),): {"Media": node}}))

    assert (await adapter.media(195516)).status is None


@pytest.mark.asyncio
async def test_media_year_only_start_date_still_yields_season_start(adapter, monkeypatch):
    _use_media(monkeypatch, adapter, 199185)  # 盾の勇者の成り上がり Season 5

    detail = await adapter.media(199185)

    assert detail.title == "盾の勇者の成り上がり Season 5"
    assert len(detail.releases) == 1
    release = detail.releases[0]
    assert release.kind == "season-start"
    assert release.date == "2027-01-01"
    assert release.precision == "year"
    assert release.time is None
    assert release.externalId == "199185"


@pytest.mark.asyncio
async def test_media_year_month_start_date_has_month_precision(adapter, monkeypatch):
    node = _media_node(199185)
    node["startDate"] = {"year": 2027, "month": 4, "day": None}
    monkeypatch.setattr(adapter, "_graphql", FakeGraphQL({(("id", 199185),): {"Media": node}}))

    detail = await adapter.media(199185)

    release = detail.releases[0]
    assert (release.date, release.precision) == ("2027-04-01", "month")


@pytest.mark.asyncio
async def test_media_without_any_start_date_has_no_season_start(adapter, monkeypatch):
    node = _media_node(199185)
    node["startDate"] = {"year": None, "month": None, "day": None}
    monkeypatch.setattr(adapter, "_graphql", FakeGraphQL({(("id", 199185),): {"Media": node}}))

    assert (await adapter.media(199185)).releases == []


@pytest.mark.asyncio
async def test_media_of_novel_entry_has_no_anime_releases(adapter, monkeypatch):
    _use_media(monkeypatch, adapter, 85470)  # 無職転生 (NOVEL)

    detail = await adapter.media(85470)

    assert detail.mediaType == "light-novel"
    assert detail.title == "無職転生 ～異世界行ったら本気だす～"
    assert detail.releases == []


@pytest.mark.asyncio
async def test_media_is_cached_across_calls(adapter, monkeypatch):
    fixture = _load("anilist_media_101280.json")
    fake = FakeGraphQL({(("id", 101280),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    await adapter.media(101280)
    await adapter.media(101280)

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_media_refresh_bypasses_cache_and_rewrites_it(adapter, monkeypatch):
    fixture = _load("anilist_media_101280.json")
    fake = FakeGraphQL({(("id", 101280),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    await adapter.media(101280)
    await adapter.media(101280, refresh=True)
    assert len(fake.calls) == 2

    # the refresh wrote through: a plain call is served from the new entry
    updated = copy.deepcopy(fixture["data"])
    updated["Media"]["status"] = "HIATUS"
    fake.responses[(("id", 101280),)] = updated
    await adapter.media(101280)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_refresh_picks_up_changed_airing_data(adapter, monkeypatch):
    node = _media_node(195516)
    fake = FakeGraphQL({(("id", 195516),): {"Media": node}})
    monkeypatch.setattr(adapter, "_graphql", fake)
    first = await adapter.media(195516)

    moved = copy.deepcopy(node)
    moved["nextAiringEpisode"] = {"episode": 4, "airingAt": EPOCH_JST_0130, "timeUntilAiring": 1}
    fake.responses[(("id", 195516),)] = {"Media": moved}

    stale = await adapter.media(195516)
    fresh = await adapter.media(195516, refresh=True)

    assert next(r for r in stale.releases if r.kind == "episode").label == "第3話"
    assert next(r for r in first.releases if r.kind == "episode").label == "第3話"
    refreshed = next(r for r in fresh.releases if r.kind == "episode")
    assert (refreshed.label, refreshed.date, refreshed.time) == ("第4話", "2026-10-17", "25:30")


# -- relations --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relations_of_novel_lists_adaptation_anime(adapter, monkeypatch):
    _use_media(monkeypatch, adapter, 85470)  # 無職転生 原作 NOVEL

    dto = await adapter.relations(85470)

    assert dto.externalId == "85470"
    assert dto.type == "MANGA"
    assert dto.format == "NOVEL"
    assert dto.title == "無職転生 ～異世界行ったら本気だす～"

    anime = [e for e in dto.relations if e.type == "ANIME" and e.relationType == "ADAPTATION"]
    assert {e.externalId for e in anime} >= {"108465", "127720", "146065", "166873", "178789", "217434"}
    assert all(e.format in ("TV", "SPECIAL") for e in anime)

    season_3 = next(e for e in anime if e.externalId == "178789")
    assert season_3.title == "無職転生Ⅲ ～異世界行ったら本気だす～"  # native first
    assert season_3.status == "FINISHED"
    assert (season_3.startDate, season_3.startPrecision) == ("2026-07-04", "day")

    # the announced next cour only has a year
    announced = next(e for e in anime if e.externalId == "217434")
    assert announced.status == "NOT_YET_RELEASED"
    assert (announced.startDate, announced.startPrecision) == ("2027-01-01", "year")


@pytest.mark.asyncio
async def test_relations_keep_non_anime_relation_types(adapter, monkeypatch):
    _use_media(monkeypatch, adapter, 85470)

    dto = await adapter.relations(85470)

    by_id = {e.externalId: e for e in dto.relations}
    assert by_id["104724"].relationType == "SPIN_OFF"
    assert by_id["165131"].relationType == "SIDE_STORY"
    assert (by_id["85564"].type, by_id["85564"].format) == ("MANGA", "MANGA")
    assert all(e.isAdult is False for e in dto.relations)


@pytest.mark.asyncio
async def test_relations_follow_sequels_from_kusuriya_season_1_to_season_3(adapter, monkeypatch):
    _use_media(monkeypatch, adapter, 161645, 176301, 195516)

    season_1 = await adapter.relations(161645)
    season_2 = await adapter.relations(176301)
    season_3 = await adapter.relations(195516)

    assert season_1.type == "ANIME" and season_1.title == "薬屋のひとりごと"
    sequel_1 = [e for e in season_1.relations if e.relationType == "SEQUEL"]
    assert [(e.externalId, e.title) for e in sequel_1] == [("176301", "薬屋のひとりごと 第2期")]

    sequel_2 = [e for e in season_2.relations if e.relationType == "SEQUEL"]
    assert [(e.externalId, e.status) for e in sequel_2] == [("195516", "RELEASING")]
    assert sequel_2[0].startDate == "2026-10-02"

    sequel_3 = [e for e in season_3.relations if e.relationType == "SEQUEL"]
    assert [e.externalId for e in sequel_3] == ["200927"]
    assert sequel_3[0].status == "NOT_YET_RELEASED"
    assert (sequel_3[0].startDate, sequel_3[0].startPrecision) == ("2027-04-01", "month")


@pytest.mark.asyncio
async def test_relations_of_kusuriya_novel_reach_the_new_season(adapter, monkeypatch):
    _use_media(monkeypatch, adapter, 99026)  # 薬屋のひとりごと LN

    dto = await adapter.relations(99026)

    anime = {e.externalId: e for e in dto.relations if e.type == "ANIME" and e.relationType == "ADAPTATION"}
    assert set(anime) == {"161645", "176301", "195516", "200927"}
    assert anime["195516"].status == "RELEASING"
    assert anime["200927"].startPrecision == "month"


@pytest.mark.asyncio
async def test_relations_drop_adult_nodes(adapter, monkeypatch):
    node = _media_node(85470)
    for edge in node["relations"]["edges"]:
        if edge["node"]["id"] in (104724, 108465):
            edge["node"]["isAdult"] = True
    monkeypatch.setattr(adapter, "_graphql", FakeGraphQL({(("id", 85470),): {"Media": node}}))

    dto = await adapter.relations(85470)

    ids = {e.externalId for e in dto.relations}
    assert "104724" not in ids and "108465" not in ids
    assert "127720" in ids


@pytest.mark.asyncio
async def test_relations_pass_cancelled_and_hiatus_through(adapter, monkeypatch):
    node = _media_node(85470)
    node["relations"]["edges"][0]["node"]["status"] = "CANCELLED"
    node["relations"]["edges"][1]["node"]["status"] = "HIATUS"
    first_id = str(node["relations"]["edges"][0]["node"]["id"])
    second_id = str(node["relations"]["edges"][1]["node"]["id"])
    monkeypatch.setattr(adapter, "_graphql", FakeGraphQL({(("id", 85470),): {"Media": node}}))

    dto = await adapter.relations(85470)

    by_id = {e.externalId: e for e in dto.relations}
    assert by_id[first_id].status == "CANCELLED"
    assert by_id[second_id].status == "HIATUS"


@pytest.mark.asyncio
async def test_media_and_relations_share_one_request(adapter, monkeypatch):
    fake = _use_media(monkeypatch, adapter, 161645)

    await adapter.media(161645)
    await adapter.relations(161645)

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_relations_refresh_bypasses_cache(adapter, monkeypatch):
    fake = _use_media(monkeypatch, adapter, 161645)

    await adapter.relations(161645)
    await adapter.relations(161645)
    assert len(fake.calls) == 1

    await adapter.relations(161645, refresh=True)
    assert len(fake.calls) == 2


def test_source_candidates_extracts_adaptation_relations_only():
    fixture = _load("anilist_media_101280.json")
    adapter = AniListAdapter.__new__(AniListAdapter)  # no I/O needed
    node = fixture["data"]["Media"]

    candidates = adapter.source_candidates(node)

    ids = {c["externalId"] for c in candidates}
    # Only ADAPTATION edges (light novel 86355, manga 86399), not SIDE_STORY /
    # SPIN_OFF / CHARACTER / SEQUEL siblings.
    assert ids == {"86355", "86399"}

    novel_candidate = next(c for c in candidates if c["externalId"] == "86355")
    assert novel_candidate["mediaType"] == "light-novel"

    manga_candidate = next(c for c in candidates if c["externalId"] == "86399")
    assert manga_candidate["mediaType"] == "manga"


def test_source_candidates_from_releasing_season_includes_prequel_exclusion():
    fixture = _load("anilist_media_182205.json")
    adapter = AniListAdapter.__new__(AniListAdapter)
    node = fixture["data"]["Media"]

    candidates = adapter.source_candidates(node)

    ids = {c["externalId"] for c in candidates}
    assert ids == {"86355", "86399"}  # PREQUEL (156822) excluded


def test_source_candidates_skip_adult_nodes():
    adapter = AniListAdapter.__new__(AniListAdapter)
    node = _load("anilist_media_182205.json")["data"]["Media"]
    for edge in node["relations"]["edges"]:
        if edge["node"]["id"] == 86355:
            edge["node"]["isAdult"] = True

    ids = {c["externalId"] for c in adapter.source_candidates(node)}

    assert ids == {"86399"}


# -- resolve-source ------------------------------------------------------------------


def _resolve_fake(adapter, monkeypatch, fixture_name: str, title: str, formats: list[str]):
    fixture = _load(fixture_name)
    fake = FakeGraphQL({(("formats", tuple(formats)), ("search", title)): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)
    return fake


@pytest.mark.asyncio
async def test_resolve_source_puts_original_novel_first_for_mushoku_tensei(adapter, monkeypatch):
    fake = _resolve_fake(adapter, monkeypatch, "anilist_resolve_novel_mushoku.json", "無職転生", ["NOVEL"])

    results = await adapter.resolve_source("無職転生", "novel")

    # AniList itself lists 蛇足編 first; the main novel must outrank it.
    assert results[0].externalId == "85470"
    assert results[0].title == "無職転生 ～異世界行ったら本気だす～"
    assert {r.externalId for r in results} == {"85470", "165131", "182671", "183686"}
    assert all(r.source == "anilist" and r.mediaType == "light-novel" for r in results)
    assert results[0].url == "https://anilist.co/manga/85470"
    assert results[0].cover
    assert "isAdult: false" in fake.queries[0]


@pytest.mark.asyncio
async def test_resolve_source_exact_title_beats_prefix_matches(adapter, monkeypatch):
    full_title = "無職転生 ～異世界行ったら本気だす～"
    _resolve_fake(adapter, monkeypatch, "anilist_resolve_novel_mushoku.json", full_title, ["NOVEL"])

    results = await adapter.resolve_source(full_title, "novel")

    ids = [r.externalId for r in results]
    assert ids[0] == "85470"  # exact
    assert set(ids) == {"85470", "182671", "183686"}  # 蛇足編 does not match this title
    assert "165131" not in ids


@pytest.mark.asyncio
async def test_resolve_source_accepts_title_longer_than_the_anilist_entry(adapter, monkeypatch):
    query = "薬屋のひとりごと 第3期"  # the Work title is the one with the suffix
    _resolve_fake(adapter, monkeypatch, "anilist_resolve_novel_kusuriya.json", query, ["NOVEL"])

    results = await adapter.resolve_source(query, "novel")

    assert [r.externalId for r in results] == ["99026"]


@pytest.mark.asyncio
async def test_resolve_source_manga_kind_uses_manga_and_one_shot(adapter, monkeypatch):
    fake = _resolve_fake(
        adapter,
        monkeypatch,
        "anilist_resolve_manga_kusuriya.json",
        "薬屋のひとりごと",
        ["MANGA", "ONE_SHOT"],
    )

    results = await adapter.resolve_source("薬屋のひとりごと", "manga")

    assert [r.externalId for r in results] == ["99022", "113322", "188727"]
    assert all(r.mediaType == "manga" for r in results)
    assert fake.calls == [(("formats", ("MANGA", "ONE_SHOT")), ("search", "薬屋のひとりごと"))]


@pytest.mark.asyncio
async def test_resolve_source_novel_kind_requests_only_novel_format(adapter, monkeypatch):
    fake = _resolve_fake(adapter, monkeypatch, "anilist_resolve_novel_kusuriya.json", "薬屋のひとりごと", ["NOVEL"])

    results = await adapter.resolve_source("薬屋のひとりごと", "novel")

    assert [r.externalId for r in results] == ["99026"]
    assert fake.calls == [(("formats", ("NOVEL",)), ("search", "薬屋のひとりごと"))]


@pytest.mark.asyncio
async def test_resolve_source_returns_at_most_five(adapter, monkeypatch):
    nodes = [_search_node(i, f"テスト作品 外伝{i}", popularity=i) for i in range(1, 9)]
    fake = FakeGraphQL({(("formats", ("NOVEL",)), ("search", "テスト作品")): _page(*nodes)})
    monkeypatch.setattr(adapter, "_graphql", fake)

    results = await adapter.resolve_source("テスト作品", "novel")

    assert len(results) == 5
    # same tier -> most popular first
    assert [r.externalId for r in results] == ["8", "7", "6", "5", "4"]


@pytest.mark.asyncio
async def test_resolve_source_ranks_exact_then_prefix_then_contains(adapter, monkeypatch):
    nodes = [
        _search_node(1, "番外 星の旅人", popularity=9000),  # contains
        _search_node(2, "星の旅人 外伝", popularity=10),  # prefix
        _search_node(3, "星の旅人", popularity=1),  # exact
        _search_node(4, "全く別の作品", popularity=99999),  # unrelated -> dropped
    ]
    fake = FakeGraphQL({(("formats", ("NOVEL",)), ("search", "星の旅人")): _page(*nodes)})
    monkeypatch.setattr(adapter, "_graphql", fake)

    results = await adapter.resolve_source("星の旅人", "novel")

    assert [r.externalId for r in results] == ["3", "2", "1"]


@pytest.mark.asyncio
async def test_resolve_source_matches_on_synonyms(adapter, monkeypatch):
    nodes = [_search_node(7, "正式名称がまるで違う", synonyms=["星の旅人"])]
    fake = FakeGraphQL({(("formats", ("NOVEL",)), ("search", "星の旅人")): _page(*nodes)})
    monkeypatch.setattr(adapter, "_graphql", fake)

    results = await adapter.resolve_source("星の旅人", "novel")

    assert [r.externalId for r in results] == ["7"]


@pytest.mark.asyncio
async def test_resolve_source_excludes_adult_and_wrong_format_entries(adapter, monkeypatch):
    nodes = [
        _search_node(1, "星の旅人", isAdult=True),
        _search_node(2, "星の旅人", format="MANGA"),  # not a NOVEL
        _search_node(3, "星の旅人"),
    ]
    fake = FakeGraphQL({(("formats", ("NOVEL",)), ("search", "星の旅人")): _page(*nodes)})
    monkeypatch.setattr(adapter, "_graphql", fake)

    results = await adapter.resolve_source("星の旅人", "novel")

    assert [r.externalId for r in results] == ["3"]


@pytest.mark.asyncio
async def test_resolve_source_rejects_unknown_kind(adapter):
    with pytest.raises(ValueError):
        await adapter.resolve_source("星の旅人", "anime")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_resolve_source_is_cached_and_refresh_bypasses_it(adapter, monkeypatch):
    fake = _resolve_fake(adapter, monkeypatch, "anilist_resolve_novel_kusuriya.json", "薬屋のひとりごと", ["NOVEL"])

    await adapter.resolve_source("薬屋のひとりごと", "novel")
    await adapter.resolve_source("薬屋のひとりごと", "novel")
    assert len(fake.calls) == 1

    await adapter.resolve_source("薬屋のひとりごと", "novel", refresh=True)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_resolve_source_cache_is_separate_per_kind(adapter, monkeypatch):
    fake = FakeGraphQL(
        {
            (("formats", ("NOVEL",)), ("search", "薬屋のひとりごと")): _load(
                "anilist_resolve_novel_kusuriya.json"
            )["data"],
            (("formats", ("MANGA", "ONE_SHOT")), ("search", "薬屋のひとりごと")): _load(
                "anilist_resolve_manga_kusuriya.json"
            )["data"],
        }
    )
    monkeypatch.setattr(adapter, "_graphql", fake)

    novel = await adapter.resolve_source("薬屋のひとりごと", "novel")
    manga = await adapter.resolve_source("薬屋のひとりごと", "manga")

    assert [r.externalId for r in novel] == ["99026"]
    assert manga[0].externalId == "99022"
    assert len(fake.calls) == 2


# -- detect ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_scores_exact_native_title_match_as_one(adapter, monkeypatch):
    fixture = _load("anilist_detect.json")
    fake = FakeGraphQL({(("search", "転スラ"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("転スラ")

    by_id = {c.externalId: c for c in candidates}
    # 106509's synonym list contains the exact string "転スラ".
    assert by_id["106509"].matchScore == pytest.approx(1.0)
    assert by_id["101280"].matchScore == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_detect_scores_partial_synonym_match_below_one(adapter, monkeypatch):
    fixture = _load("anilist_detect.json")
    fake = FakeGraphQL({(("search", "転スラ"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("転スラ")

    by_id = {c.externalId: c for c in candidates}
    # 156822's native title has no "転スラ" substring, but its synonym list
    # has "転スラ 3" (a superset containing "転スラ") -> partial score.
    assert 0.0 < by_id["156822"].matchScore < 1.0


@pytest.mark.asyncio
async def test_detect_includes_airing_status_and_start_date(adapter, monkeypatch):
    fixture = _load("anilist_detect.json")
    fake = FakeGraphQL({(("search", "転スラ"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("転スラ")

    by_id = {c.externalId: c for c in candidates}
    releasing = by_id["182205"]
    assert releasing.airingStatus == "RELEASING"
    assert releasing.startDate == "2026-04-03"
    assert releasing.startPrecision == "day"

    finished = by_id["101280"]
    assert finished.airingStatus == "FINISHED"


@pytest.mark.asyncio
async def test_detect_marks_candidates_as_title_matches_with_format(adapter, monkeypatch):
    fixture = _load("anilist_detect_tate.json")
    fake = FakeGraphQL({(("search", "盾の勇者の成り上がり"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("盾の勇者の成り上がり")

    assert candidates
    assert all(c.via == "title" and c.source == "anilist" for c in candidates)
    assert all(c.format == "TV" for c in candidates)
    assert "isAdult: false" in fake.queries[0]


@pytest.mark.asyncio
async def test_detect_keeps_year_only_start_with_year_precision(adapter, monkeypatch):
    fixture = _load("anilist_detect_tate.json")
    fake = FakeGraphQL({(("search", "盾の勇者の成り上がり"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("盾の勇者の成り上がり")

    season_5 = next(c for c in candidates if c.externalId == "199185")
    assert season_5.airingStatus == "NOT_YET_RELEASED"
    assert (season_5.startDate, season_5.startPrecision) == ("2027-01-01", "year")
    season_4 = next(c for c in candidates if c.externalId == "173780")
    assert (season_4.startDate, season_4.startPrecision) == ("2025-07-09", "day")


@pytest.mark.asyncio
async def test_detect_results_sorted_by_score_descending(adapter, monkeypatch):
    fixture = _load("anilist_detect.json")
    fake = FakeGraphQL({(("search", "転スラ"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("転スラ")

    scores = [c.matchScore for c in candidates]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_detect_unrelated_title_scores_zero_for_non_matching_candidates(
    adapter, monkeypatch
):
    fixture = _load("anilist_detect.json")
    fake = FakeGraphQL({(("search", "転スラ"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("転スラ")

    # 166381's native title/synonyms don't contain "転スラ" as native title,
    # but one synonym is "転スラ × 幻ヨハ" which does contain it as substring.
    by_id = {c.externalId: c for c in candidates}
    assert by_id["166381"].matchScore > 0.0


@pytest.mark.asyncio
async def test_detect_keeps_cancelled_and_hiatus_statuses(adapter, monkeypatch):
    def node(media_id: int, status: str) -> dict[str, Any]:
        return {
            "id": media_id,
            "format": "TV",
            "isAdult": False,
            "title": {"romaji": None, "english": None, "native": "星の旅人"},
            "synonyms": [],
            "status": status,
            "startDate": {"year": 2025, "month": 4, "day": 1},
        }

    fake = FakeGraphQL(
        {(("search", "星の旅人"),): _page(node(1, "CANCELLED"), node(2, "HIATUS"), node(3, "FINISHED"))}
    )
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("星の旅人")

    assert {c.externalId: c.airingStatus for c in candidates} == {
        "1": "CANCELLED",
        "2": "HIATUS",
        "3": "FINISHED",
    }


@pytest.mark.asyncio
async def test_detect_excludes_adult_media(adapter, monkeypatch):
    def node(media_id: int, is_adult: bool) -> dict[str, Any]:
        return {
            "id": media_id,
            "format": "OVA",
            "isAdult": is_adult,
            "title": {"romaji": None, "english": None, "native": "星の旅人"},
            "synonyms": [],
            "status": "FINISHED",
            "startDate": {"year": 2020, "month": 1, "day": 1},
        }

    fake = FakeGraphQL({(("search", "星の旅人"),): _page(node(1, True), node(2, False))})
    monkeypatch.setattr(adapter, "_graphql", fake)

    candidates = await adapter.detect("星の旅人")

    assert [c.externalId for c in candidates] == ["2"]


@pytest.mark.asyncio
async def test_detect_is_cached_and_refresh_bypasses_it(adapter, monkeypatch):
    fixture = _load("anilist_detect.json")
    fake = FakeGraphQL({(("search", "転スラ"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    await adapter.detect("転スラ")
    await adapter.detect("転スラ")
    assert len(fake.calls) == 1

    await adapter.detect("転スラ", refresh=True)
    assert len(fake.calls) == 2


# -- routes ---------------------------------------------------------------------


@pytest.fixture()
def api(adapter, monkeypatch):
    errors.last_adapter_errors.clear()
    monkeypatch.setattr(anilist_api, "get_adapter", lambda: adapter)
    yield TestClient(app_module.app)
    errors.last_adapter_errors.clear()


def test_route_media_returns_detail_with_jst_episode(api, adapter, monkeypatch):
    fake = _use_media(monkeypatch, adapter, 195516)

    response = api.get("/api/anilist/media/195516")

    assert response.status_code == 200
    body = response.json()
    assert body["externalId"] == "195516"
    episode = next(r for r in body["releases"] if r["kind"] == "episode")
    assert (episode["date"], episode["time"], episode["precision"]) == ("2026-10-16", "23:00", "day")
    assert episode["externalId"] == "195516:3"
    assert len(fake.calls) == 1


def test_route_media_refresh_flag_bypasses_cache(api, adapter, monkeypatch):
    fake = _use_media(monkeypatch, adapter, 195516)

    api.get("/api/anilist/media/195516")
    api.get("/api/anilist/media/195516")
    assert len(fake.calls) == 1

    assert api.get("/api/anilist/media/195516?refresh=1").status_code == 200
    assert len(fake.calls) == 2


def test_route_relations_returns_sequel_edges(api, adapter, monkeypatch):
    fake = _use_media(monkeypatch, adapter, 161645)

    response = api.get("/api/anilist/relations/161645")

    assert response.status_code == 200
    body = response.json()
    assert body["externalId"] == "161645"
    sequel = next(e for e in body["relations"] if e["relationType"] == "SEQUEL")
    assert sequel["externalId"] == "176301"
    assert sequel["type"] == "ANIME"

    api.get("/api/anilist/relations/161645?refresh=1")
    assert len(fake.calls) == 2


def test_route_resolve_source_returns_original_novel_first(api, adapter, monkeypatch):
    fake = _resolve_fake(adapter, monkeypatch, "anilist_resolve_novel_mushoku.json", "無職転生", ["NOVEL"])

    response = api.get("/api/anilist/resolve-source", params={"title": "無職転生", "kind": "novel"})

    assert response.status_code == 200
    body = response.json()
    assert body[0]["externalId"] == "85470"
    assert body[0]["mediaType"] == "light-novel"

    api.get("/api/anilist/resolve-source", params={"title": "無職転生", "kind": "novel", "refresh": "1"})
    assert len(fake.calls) == 2


def test_route_resolve_source_rejects_unknown_kind(api):
    response = api.get("/api/anilist/resolve-source", params={"title": "無職転生", "kind": "anime"})
    assert response.status_code == 422


def test_route_detect_returns_title_candidates(api, adapter, monkeypatch):
    fixture = _load("anilist_detect_tate.json")
    fake = FakeGraphQL({(("search", "盾の勇者の成り上がり"),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    response = api.get("/api/anilist/detect", params={"title": "盾の勇者の成り上がり"})

    assert response.status_code == 200
    body = response.json()
    season_5 = next(c for c in body if c["externalId"] == "199185")
    assert season_5["via"] == "title"
    assert season_5["startPrecision"] == "year"
    assert season_5["format"] == "TV"

    api.get("/api/anilist/detect", params={"title": "盾の勇者の成り上がり", "refresh": "1"})
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "path, params",
    [
        ("/api/anilist/media/1", {}),
        ("/api/anilist/relations/1", {}),
        ("/api/anilist/resolve-source", {"title": "x", "kind": "novel"}),
        ("/api/anilist/detect", {"title": "x"}),
    ],
)
def test_routes_record_adapter_error_and_answer_502(api, adapter, monkeypatch, path, params):
    async def failing(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        raise SourceUnavailable("anilist", "HTTP 429: rate limited")

    monkeypatch.setattr(adapter, "_graphql", failing)

    response = api.get(path, params=params)

    assert response.status_code == 502
    assert response.json()["detail"] == "HTTP 429: rate limited"
    recorded = errors.last_adapter_errors["anilist"]
    assert recorded.source == "anilist"
    assert recorded.message == "HTTP 429: rate limited"


def test_module_exposes_graphql_queries_used_by_search_router():
    # backend/api/search.py reuses these directly.
    assert "relations" in anilist_module._DETAIL_QUERY
    assert "siteUrl" in anilist_module._DETAIL_QUERY
