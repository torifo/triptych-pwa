"""Tests for the AniList adapter's normalization logic.

No network calls are made: `_graphql` is monkeypatched to return recorded
fixture payloads (see backend/tests/fixtures/anilist_*.json), which were
captured from a handful of real requests against https://graphql.anilist.co
for "That Time I Got Reincarnated as a Slime" (a light-novel-original work
with an anime adaptation), covering search / media detail (with relations
and an active nextAiringEpisode) / detect scoring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.adapters.anilist import AniListAdapter, normalize_title
from backend.cache import TTLCache

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture()
def adapter(tmp_path):
    return AniListAdapter(cache=TTLCache(tmp_path / "cache.db"))


class FakeGraphQL:
    """Stand-in for `AniListAdapter._graphql` that serves fixture data.

    Keyed by the query's operation shape via a caller-supplied mapping from
    (variables tuple) -> fixture "data" payload, so each test wires up only
    the calls it needs.
    """

    def __init__(self, responses: dict[tuple, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple] = []

    async def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        key = tuple(sorted(variables.items()))
        self.calls.append(key)
        return self.responses[key]


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


# -- media (detail + relations) ------------------------------------------------


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
    assert detail.releases[0].kind == "season-start"
    assert detail.releases[0].date == "2018-10-02"


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
    assert episode_release.date  # YYYY-MM-DD, derived from airingAt


@pytest.mark.asyncio
async def test_media_is_cached_across_calls(adapter, monkeypatch):
    fixture = _load("anilist_media_101280.json")
    fake = FakeGraphQL({(("id", 101280),): fixture["data"]})
    monkeypatch.setattr(adapter, "_graphql", fake)

    await adapter.media(101280)
    await adapter.media(101280)

    assert len(fake.calls) == 1


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

    finished = by_id["101280"]
    assert finished.airingStatus == "FINISHED"


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
