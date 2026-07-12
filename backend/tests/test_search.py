"""Tests for `/api/search` (FR-006 cross-source search).

No network access: `backend.api.search.get_anilist_adapter` and
`backend.api.search.narou_adapter.search` are monkeypatched with fakes so
both the merge-on-success and skip-on-failure (partial results) paths are
exercised without touching AniList/なろう小説API.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend.api import search as search_module
from backend.dto import SearchResult, SourceUnavailable


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _anilist_hit() -> SearchResult:
    return SearchResult(
        source="anilist",
        externalId="101280",
        mediaType="anime",
        title="転生したらスライムだった件",
        cover="https://example.invalid/cover.jpg",
        url="https://anilist.co/anime/101280",
    )


def _narou_hit() -> SearchResult:
    return SearchResult(
        source="narou",
        externalId="n4830bu",
        mediaType="web-novel",
        title="転生したらスライムだった件",
        cover=None,
        url="https://ncode.syosetu.com/n4830bu/",
    )


class FakeAniListAdapter:
    def __init__(self, results=None, error: Exception | None = None):
        self._results = results or []
        self._error = error
        self.calls: list[str] = []

    async def search(self, q: str):
        self.calls.append(q)
        if self._error is not None:
            raise self._error
        return self._results


# -- success: merge across sources -------------------------------------------


@pytest.mark.asyncio
async def test_search_merges_results_from_both_sources(monkeypatch, client):
    fake_anilist = FakeAniListAdapter(results=[_anilist_hit()])
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: fake_anilist)

    async def fake_narou_search(q, *, cache=None, client=None, refresh=False):
        return [_narou_hit()]

    monkeypatch.setattr(search_module.narou_adapter, "search", fake_narou_search)

    response = client.get("/api/search", params={"q": "転生したらスライムだった件"})

    assert response.status_code == 200
    body = response.json()
    sources = {r["source"] for r in body["results"]}
    assert sources == {"anilist", "narou"}
    assert body["errors"] == []
    assert fake_anilist.calls == ["転生したらスライムだった件"]


# -- partial failure: one source down, other still returned -----------------


@pytest.mark.asyncio
async def test_search_skips_failing_source_and_returns_partial_results(monkeypatch, client):
    fake_anilist = FakeAniListAdapter(error=SourceUnavailable("anilist", "rate limited"))
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: fake_anilist)

    async def fake_narou_search(q, *, cache=None, client=None, refresh=False):
        return [_narou_hit()]

    monkeypatch.setattr(search_module.narou_adapter, "search", fake_narou_search)

    response = client.get("/api/search", params={"q": "本好きの下剋上"})

    assert response.status_code == 200
    body = response.json()
    assert [r["source"] for r in body["results"]] == ["narou"]
    assert len(body["errors"]) == 1
    assert body["errors"][0]["source"] == "anilist"
    assert body["errors"][0]["message"] == "rate limited"


@pytest.mark.asyncio
async def test_search_both_sources_down_returns_empty_results_with_errors(monkeypatch, client):
    fake_anilist = FakeAniListAdapter(error=SourceUnavailable("anilist", "boom"))
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: fake_anilist)

    async def fake_narou_search(q, *, cache=None, client=None, refresh=False):
        raise SourceUnavailable("narou", "timeout")

    monkeypatch.setattr(search_module.narou_adapter, "search", fake_narou_search)

    response = client.get("/api/search", params={"q": "何か"})

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert {e["source"] for e in body["errors"]} == {"anilist", "narou"}


# -- types filter -------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_types_filters_merged_results(monkeypatch, client):
    fake_anilist = FakeAniListAdapter(results=[_anilist_hit()])
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: fake_anilist)

    async def fake_narou_search(q, *, cache=None, client=None, refresh=False):
        return [_narou_hit()]

    monkeypatch.setattr(search_module.narou_adapter, "search", fake_narou_search)

    response = client.get(
        "/api/search", params={"q": "転生したらスライムだった件", "types": "web-novel"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [r["source"] for r in body["results"]] == ["narou"]


# -- q is required --------------------------------------------------------------


def test_search_requires_q_param(client):
    response = client.get("/api/search")
    assert response.status_code == 422


# -- anilist-origins (原作候補) ------------------------------------------------


@pytest.mark.asyncio
async def test_anilist_origins_returns_source_candidates(monkeypatch, client):
    raw_media_node = {
        "id": 101280,
        "type": "ANIME",
        "format": "TV",
        "relations": {
            "edges": [
                {
                    "relationType": "ADAPTATION",
                    "node": {
                        "id": 86355,
                        "type": "MANGA",
                        "format": "NOVEL",
                        "title": {"romaji": None, "english": None, "native": "転生したらスライムだった件"},
                        "siteUrl": "https://anilist.co/manga/86355",
                    },
                },
                {
                    "relationType": "SEQUEL",
                    "node": {
                        "id": 999,
                        "type": "ANIME",
                        "format": "TV",
                        "title": {"romaji": None, "english": None, "native": "sequel"},
                        "siteUrl": "https://anilist.co/anime/999",
                    },
                },
            ]
        },
    }

    from backend.adapters.anilist import AniListAdapter

    real_adapter = AniListAdapter.__new__(AniListAdapter)  # no I/O needed

    async def fake_graphql(query, variables):
        assert variables == {"id": 101280}
        return {"Media": raw_media_node}

    real_adapter._graphql = fake_graphql
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: real_adapter)

    response = client.get("/api/search/anilist-origins/101280")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1  # SEQUEL excluded, only ADAPTATION kept
    assert body[0]["externalId"] == "86355"
    assert body[0]["mediaType"] == "light-novel"


@pytest.mark.asyncio
async def test_anilist_origins_propagates_source_unavailable_as_502(monkeypatch, client):
    from backend.adapters.anilist import AniListAdapter

    real_adapter = AniListAdapter.__new__(AniListAdapter)

    async def fake_graphql(query, variables):
        raise SourceUnavailable("anilist", "down")

    real_adapter._graphql = fake_graphql
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: real_adapter)

    response = client.get("/api/search/anilist-origins/101280")

    assert response.status_code == 502
