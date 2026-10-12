"""Tests for `/api/search` (FR-006 / US-104 cross-source search).

No network access: every adapter the router uses is monkeypatched with a fake
(or, for the integration-style cases, a real なろう / カクヨム adapter on top of
httpx.MockTransport replaying the recorded fixtures). Covers the fan-out merge,
skip-on-failure (partial results), `/api/health` error recording, and the
direct resolution of URL / ncode / ISBN queries.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import errors
from backend.adapters import narou as narou_module
from backend.adapters.kakuyomu import KakuyomuAdapter
from backend.api import search as search_module
from backend.cache import TTLCache
from backend.dto import MediaDetailDTO, SearchResult, SourceUnavailable, WebNovelDTO
from backend.ratelimit import TokenBucket

FIXTURES = Path(__file__).parent / "fixtures"

# Captured at import time, before any test monkeypatches the module attribute.
_REAL_NAROU_SEARCH = narou_module.search


@pytest.fixture(autouse=True)
def clean_adapter_errors():
    errors.last_adapter_errors.clear()
    yield
    errors.last_adapter_errors.clear()


@pytest.fixture(autouse=True)
def private_cache(monkeypatch, tmp_path):
    """Keeps the router's shared cache off the real backend/cache.db."""
    monkeypatch.setattr(search_module, "_cache", TTLCache(db_path=tmp_path / "cache.db"))


@pytest.fixture()
def client():
    return TestClient(app_module.app)


# -- fakes --------------------------------------------------------------------


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
        externalId="n6316bn",
        mediaType="web-novel",
        title="転生したらスライムだった件",
        cover=None,
        url="https://ncode.syosetu.com/n6316bn/",
        subtitle="伏瀬",
    )


def _kakuyomu_hit() -> SearchResult:
    return SearchResult(
        source="kakuyomu",
        externalId="1177354054886847987",
        mediaType="web-novel",
        title="陰の実力者になりたくて！",
        url="https://kakuyomu.jp/works/1177354054886847987",
        subtitle="逢沢大介・書籍化",
    )


def _ndl_hit() -> SearchResult:
    return SearchResult(
        source="ndl",
        externalId="9784864723428",
        mediaType="light-novel",
        title="本好きの下剋上",
        url="https://ndlsearch.ndl.go.jp/books/9784864723428",
        subtitle="TOブックス",
    )


class FakeAniListAdapter:
    """`search(q)` only — like the adapter, it takes no `refresh` argument."""

    def __init__(self, results=None, error: Optional[Exception] = None):
        self._results = results or []
        self._error = error
        self.calls: list[str] = []
        self.media_calls: list[str] = []
        self.media_result: Optional[MediaDetailDTO] = None
        self.media_error: Optional[Exception] = None

    async def search(self, q: str):
        self.calls.append(q)
        if self._error is not None:
            raise self._error
        return self._results

    async def media(self, media_id):
        self.media_calls.append(str(media_id))
        if self.media_error is not None:
            raise self.media_error
        return self.media_result


class FakeAniListAdapterWithRefresh(FakeAniListAdapter):
    """Like the current adapter: `search` / `media` also take `refresh`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.refresh_flags: list[bool] = []
        self.media_refresh_flags: list[bool] = []

    async def search(self, q: str, refresh: bool = False):
        self.refresh_flags.append(refresh)
        return await super().search(q)

    async def media(self, media_id, refresh: bool = False):
        self.media_refresh_flags.append(refresh)
        return await super().media(media_id)


class FakeKakuyomuAdapter:
    def __init__(self, results=None, error: Optional[Exception] = None):
        self._results = results or []
        self._error = error
        self.calls: list[tuple[str, bool]] = []
        self.work_calls: list[tuple[str, bool]] = []
        self.work_result: Optional[WebNovelDTO] = None
        self.work_error: Optional[Exception] = None

    async def search(self, q: str, refresh: bool = False):
        self.calls.append((q, refresh))
        if self._error is not None:
            raise self._error
        return self._results

    async def work(self, work_id: str, refresh: bool = False):
        self.work_calls.append((work_id, refresh))
        if self.work_error is not None:
            raise self.work_error
        return self.work_result


class Sources:
    """All four search sources plus the direct-resolution adapters, as fakes."""

    def __init__(self, monkeypatch):
        self.anilist = FakeAniListAdapter()
        self.kakuyomu = FakeKakuyomuAdapter()

        self.narou_results: list[SearchResult] = []
        self.narou_error: Optional[Exception] = None
        self.narou_calls: list[tuple[str, bool]] = []
        self.narou_novel_calls: list[tuple[str, bool]] = []
        self.narou_novel_result: Optional[WebNovelDTO] = None
        self.narou_novel_error: Optional[Exception] = None

        self.ndl_results: list[SearchResult] = []
        self.ndl_error: Optional[Exception] = None
        self.ndl_calls: list[str] = []

        self.openbd_calls: list[tuple[str, bool]] = []
        self.openbd_result = SimpleNamespace(
            title="本好きの下剋上 第1部[1]",
            cover="https://example.invalid/book.jpg",
            publisher="TOブックス",
            url="https://ndlsearch.ndl.go.jp/books/9784864723428",
        )
        self.openbd_error: Optional[Exception] = None

        async def narou_search(q, *, cache=None, client=None, refresh=False):
            self.narou_calls.append((q, refresh))
            if self.narou_error is not None:
                raise self.narou_error
            return self.narou_results

        async def narou_novel(ncode, *, cache=None, client=None, refresh=False):
            self.narou_novel_calls.append((ncode, refresh))
            if self.narou_novel_error is not None:
                raise self.narou_novel_error
            return self.narou_novel_result

        async def ndl_search_books(q):
            self.ndl_calls.append(q)
            if self.ndl_error is not None:
                raise self.ndl_error
            return self.ndl_results

        async def openbd_book(isbn, *, client=None, cache=None, refresh=False):
            self.openbd_calls.append((isbn, refresh))
            if self.openbd_error is not None:
                raise self.openbd_error
            return self.openbd_result

        monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: self.anilist)
        monkeypatch.setattr(search_module, "get_kakuyomu_adapter", lambda: self.kakuyomu)
        monkeypatch.setattr(search_module.narou_adapter, "search", narou_search)
        monkeypatch.setattr(search_module.narou_adapter, "novel", narou_novel)
        # `search_books` is added to backend.adapters.ndl by a parallel task.
        monkeypatch.setattr(
            search_module.ndl_adapter, "search_books", ndl_search_books, raising=False
        )
        monkeypatch.setattr(search_module.openbd_adapter, "book", openbd_book)

    def all_search_calls(self) -> int:
        return (
            len(self.anilist.calls)
            + len(self.kakuyomu.calls)
            + len(self.narou_calls)
            + len(self.ndl_calls)
        )


@pytest.fixture()
def sources(monkeypatch):
    return Sources(monkeypatch)


# -- success: merge across sources -------------------------------------------


def test_search_merges_results_from_all_four_sources(sources, client):
    sources.anilist._results = [_anilist_hit()]
    sources.narou_results = [_narou_hit()]
    sources.kakuyomu._results = [_kakuyomu_hit()]
    sources.ndl_results = [_ndl_hit()]

    response = client.get("/api/search", params={"q": "転生したらスライムだった件"})

    assert response.status_code == 200
    body = response.json()
    assert [r["source"] for r in body["results"]] == ["anilist", "narou", "kakuyomu", "ndl"]
    assert body["errors"] == []
    assert sources.anilist.calls == ["転生したらスライムだった件"]
    assert sources.narou_calls == [("転生したらスライムだった件", False)]
    assert sources.kakuyomu.calls == [("転生したらスライムだった件", False)]
    assert sources.ndl_calls == ["転生したらスライムだった件"]
    # Subtitle and direct flag are part of the response shape.
    narou = body["results"][1]
    assert narou["subtitle"] == "伏瀬"
    assert narou["direct"] is False


# -- partial failure: one source down, others still returned ----------------


def test_search_skips_failing_source_and_returns_partial_results(sources, client):
    sources.anilist._error = SourceUnavailable("anilist", "rate limited")
    sources.narou_results = [_narou_hit()]
    sources.kakuyomu._results = [_kakuyomu_hit()]
    sources.ndl_results = [_ndl_hit()]

    response = client.get("/api/search", params={"q": "本好きの下剋上"})

    assert response.status_code == 200
    body = response.json()
    assert [r["source"] for r in body["results"]] == ["narou", "kakuyomu", "ndl"]
    assert len(body["errors"]) == 1
    assert body["errors"][0]["source"] == "anilist"
    assert body["errors"][0]["message"] == "rate limited"


def test_search_one_source_returning_nothing_is_not_an_error(sources, client):
    sources.narou_results = [_narou_hit()]

    body = client.get("/api/search", params={"q": "何か"}).json()

    assert [r["source"] for r in body["results"]] == ["narou"]
    assert body["errors"] == []


def test_search_all_sources_down_returns_empty_results_with_errors(sources, client):
    sources.anilist._error = SourceUnavailable("anilist", "boom")
    sources.narou_error = SourceUnavailable("narou", "timeout")
    sources.kakuyomu._error = SourceUnavailable("kakuyomu", "robots")
    sources.ndl_error = SourceUnavailable("ndl", "503")

    response = client.get("/api/search", params={"q": "何か"})

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert {e["source"] for e in body["errors"]} == {"anilist", "narou", "kakuyomu", "ndl"}


def test_search_unexpected_exception_is_reported_but_not_fatal(sources, client):
    sources.kakuyomu._error = RuntimeError("parser exploded")
    sources.narou_results = [_narou_hit()]

    body = client.get("/api/search", params={"q": "何か"}).json()

    assert [r["source"] for r in body["results"]] == ["narou"]
    assert body["errors"] == [{"source": "kakuyomu", "message": "parser exploded"}]
    # Only SourceUnavailable counts as an adapter outage for /api/health.
    assert client.get("/api/health").json()["errors"] == {}


def test_search_survives_ndl_adapter_without_search_books(monkeypatch, sources, client):
    monkeypatch.delattr(search_module.ndl_adapter, "search_books", raising=False)
    sources.narou_results = [_narou_hit()]

    body = client.get("/api/search", params={"q": "何か"}).json()

    assert [r["source"] for r in body["results"]] == ["narou"]
    assert [e["source"] for e in body["errors"]] == ["ndl"]


# -- /api/health records the failures ---------------------------------------------


def test_search_failures_are_recorded_for_health(sources, client):
    sources.anilist._error = SourceUnavailable("anilist", "HTTP 429: slow down")
    sources.ndl_error = SourceUnavailable("ndl", "invalid XML response")
    sources.narou_results = [_narou_hit()]

    client.get("/api/search", params={"q": "本好き"})
    health = client.get("/api/health").json()

    assert health["status"] == "ok"
    assert set(health["errors"]) == {"anilist", "ndl"}
    assert health["errors"]["anilist"]["message"] == "HTTP 429: slow down"
    assert health["errors"]["anilist"]["source"] == "anilist"
    assert health["errors"]["anilist"]["occurredAt"]
    assert health["errors"]["ndl"]["message"] == "invalid XML response"


def test_health_is_empty_when_every_source_succeeds(sources, client):
    sources.narou_results = [_narou_hit()]

    client.get("/api/search", params={"q": "本好き"})

    assert client.get("/api/health").json()["errors"] == {}


# -- types filter -------------------------------------------------------------


def test_search_types_filters_merged_results(sources, client):
    sources.anilist._results = [_anilist_hit()]
    sources.narou_results = [_narou_hit()]
    sources.kakuyomu._results = [_kakuyomu_hit()]
    sources.ndl_results = [_ndl_hit()]

    response = client.get(
        "/api/search", params={"q": "転生したらスライムだった件", "types": "web-novel"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [r["source"] for r in body["results"]] == ["narou", "kakuyomu"]


def test_search_types_accepts_several_media_types(sources, client):
    sources.anilist._results = [_anilist_hit()]
    sources.narou_results = [_narou_hit()]
    sources.ndl_results = [_ndl_hit()]

    body = client.get(
        "/api/search", params={"q": "何か", "types": "anime, light-novel"}
    ).json()

    assert [r["source"] for r in body["results"]] == ["anilist", "ndl"]


# -- q is required --------------------------------------------------------------


def test_search_requires_q_param(client):
    response = client.get("/api/search")
    assert response.status_code == 422


# -- refresh -----------------------------------------------------------------------


def test_search_refresh_reaches_every_source_that_supports_it(monkeypatch, sources, client):
    anilist = FakeAniListAdapterWithRefresh()
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: anilist)

    async def ndl_with_refresh(q, refresh=False):
        sources.ndl_calls.append(f"{q}|refresh={refresh}")
        return []

    monkeypatch.setattr(
        search_module.ndl_adapter, "search_books", ndl_with_refresh, raising=False
    )

    client.get("/api/search", params={"q": "本好き", "refresh": "1"})
    client.get("/api/search", params={"q": "本好き"})

    assert anilist.refresh_flags == [True, False]
    assert sources.narou_calls == [("本好き", True), ("本好き", False)]
    assert sources.kakuyomu.calls == [("本好き", True), ("本好き", False)]
    assert sources.ndl_calls == ["本好き|refresh=True", "本好き|refresh=False"]


def test_search_refresh_is_harmless_for_sources_without_the_argument(sources, client):
    # FakeAniListAdapter.search(q) and the fake search_books(q) take no refresh.
    sources.anilist._results = [_anilist_hit()]
    sources.ndl_results = [_ndl_hit()]

    body = client.get("/api/search", params={"q": "本好き", "refresh": "1"}).json()

    assert body["errors"] == []
    assert [r["source"] for r in body["results"]] == ["anilist", "ndl"]


# -- real なろう / カクヨム adapters on recorded fixtures --------------------------------


def test_search_ranks_the_real_narou_work_first_and_normalizes_kakuyomu(
    monkeypatch, sources, client, tmp_path
):
    narou_requests: list[httpx.Request] = []

    def narou_handler(request: httpx.Request) -> httpx.Response:
        narou_requests.append(request)
        return httpx.Response(
            200, content=(FIXTURES / "narou_search_slime.json").read_bytes()
        )

    narou_client = httpx.AsyncClient(transport=httpx.MockTransport(narou_handler))
    monkeypatch.setattr(narou_module, "_rate_limiter", TokenBucket(rate=1000, capacity=1000))

    async def narou_via_fixture(q, *, cache=None, client=None, refresh=False):
        return await _REAL_NAROU_SEARCH(
            q, client=narou_client, cache=cache, refresh=refresh
        )

    monkeypatch.setattr(search_module.narou_adapter, "search", narou_via_fixture)

    def kakuyomu_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text=(FIXTURES / "kakuyomu_robots.txt").read_text(encoding="utf-8")
            )
        assert request.url.path == "/search"
        return httpx.Response(
            200,
            text=(FIXTURES / "kakuyomu_search_kage.html").read_text(encoding="utf-8"),
        )

    kakuyomu = KakuyomuAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(kakuyomu_handler)),
        cache=TTLCache(db_path=tmp_path / "kakuyomu.db"),
        bucket=TokenBucket(rate=1000, capacity=1),
    )
    monkeypatch.setattr(search_module, "get_kakuyomu_adapter", lambda: kakuyomu)

    body = client.get("/api/search", params={"q": "転生したらスライムだった件"}).json()

    narou = [r for r in body["results"] if r["source"] == "narou"]
    assert narou[0]["externalId"] == "n6316bn"
    assert narou[0]["subtitle"] == "伏瀬"
    assert len(narou_requests) == 1
    assert dict(narou_requests[0].url.params)["title"] == "1"

    kakuyomu_hits = [r for r in body["results"] if r["source"] == "kakuyomu"]
    assert len(kakuyomu_hits) == 6
    assert kakuyomu_hits[0]["externalId"] == "1177354054886847987"
    assert kakuyomu_hits[0]["subtitle"] == "逢沢大介"
    assert body["errors"] == []


# -- direct resolution ---------------------------------------------------------------


def _narou_dto() -> WebNovelDTO:
    return WebNovelDTO(
        source="narou",
        externalId="n6316bn",
        title="転生したらスライムだった件",
        author="伏瀬",
        url="https://ncode.syosetu.com/n6316bn/",
        episodeCount=304,
        completed=True,
        lastUpdate="2020-07-04",
    )


def _kakuyomu_dto() -> WebNovelDTO:
    return WebNovelDTO(
        source="kakuyomu",
        externalId="1177354054886847987",
        title="陰の実力者になりたくて！",
        author="逢沢大介",
        url="https://kakuyomu.jp/works/1177354054886847987",
        episodeCount=130,
        completed=False,
        lastUpdate="2019-01-02",
    )


def _anilist_media() -> MediaDetailDTO:
    return MediaDetailDTO(
        source="anilist",
        externalId="101280",
        mediaType="anime",
        title="転生したらスライムだった件",
        cover="https://example.invalid/cover.jpg",
        url="https://anilist.co/anime/101280",
    )


def _assert_nothing_was_searched(sources: Sources) -> None:
    assert sources.all_search_calls() == 0


@pytest.mark.parametrize(
    "query",
    [
        "https://ncode.syosetu.com/n6316bn/",
        "https://ncode.syosetu.com/n6316bn",
        "http://ncode.syosetu.com/N6316BN/",
        "ncode.syosetu.com/n6316bn/",
        "https://ncode.syosetu.com/n6316bn/12/",
        "https://ncode.syosetu.com/n6316bn/?p=2",
        "  https://ncode.syosetu.com/n6316bn/  ",
        "n6316bn",
        "N6316BN",
    ],
)
def test_search_resolves_narou_url_or_ncode_directly(sources, client, query):
    sources.narou_novel_result = _narou_dto()

    response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert body["results"] == [
        {
            "source": "narou",
            "externalId": "n6316bn",
            "mediaType": "web-novel",
            "title": "転生したらスライムだった件",
            "cover": None,
            "url": "https://ncode.syosetu.com/n6316bn/",
            "subtitle": "伏瀬",
            "direct": True,
        }
    ]
    assert sources.narou_novel_calls == [("n6316bn", False)]
    _assert_nothing_was_searched(sources)


@pytest.mark.parametrize(
    "query",
    [
        "https://kakuyomu.jp/works/1177354054886847987",
        "https://kakuyomu.jp/works/1177354054886847987/",
        "kakuyomu.jp/works/1177354054886847987",
        "https://kakuyomu.jp/works/1177354054886847987/episodes/1177354054886848000",
    ],
)
def test_search_resolves_kakuyomu_url_directly(sources, client, query):
    sources.kakuyomu.work_result = _kakuyomu_dto()

    body = client.get("/api/search", params={"q": query}).json()

    assert body["errors"] == []
    assert body["results"] == [
        {
            "source": "kakuyomu",
            "externalId": "1177354054886847987",
            "mediaType": "web-novel",
            "title": "陰の実力者になりたくて！",
            "cover": None,
            "url": "https://kakuyomu.jp/works/1177354054886847987",
            "subtitle": "逢沢大介",
            "direct": True,
        }
    ]
    assert sources.kakuyomu.work_calls == [("1177354054886847987", False)]
    _assert_nothing_was_searched(sources)


@pytest.mark.parametrize(
    "query",
    [
        "https://anilist.co/anime/101280",
        "https://anilist.co/anime/101280/Tensei-shitara-Slime-Datta-Ken/",
        "https://anilist.co/manga/101280",
        "anilist.co/anime/101280",
        "https://www.anilist.co/anime/101280/",
    ],
)
def test_search_resolves_anilist_url_directly(sources, client, query):
    sources.anilist.media_result = _anilist_media()

    body = client.get("/api/search", params={"q": query}).json()

    assert body["errors"] == []
    assert body["results"] == [
        {
            "source": "anilist",
            "externalId": "101280",
            "mediaType": "anime",
            "title": "転生したらスライムだった件",
            "cover": "https://example.invalid/cover.jpg",
            "url": "https://anilist.co/anime/101280",
            "subtitle": None,
            "direct": True,
        }
    ]
    assert sources.anilist.media_calls == ["101280"]
    _assert_nothing_was_searched(sources)


@pytest.mark.parametrize(
    "query",
    [
        "9784864723428",
        "978-4-86472-342-8",
        "ISBN978-4-86472-342-8",
        "ISBN: 978-4-86472-342-8",
        "isbn 9784864723428",
        " 978 4 86472 342 8 ",
        "4864723427",  # ISBN-10 of the same book
        "4-86472-342-7",
    ],
)
def test_search_resolves_isbn_directly(sources, client, query):
    response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert body["results"] == [
        {
            "source": "openbd",
            "externalId": "9784864723428",
            "mediaType": "light-novel",
            "title": "本好きの下剋上 第1部[1]",
            "cover": "https://example.invalid/book.jpg",
            "url": "https://ndlsearch.ndl.go.jp/books/9784864723428",
            "subtitle": "TOブックス",  # publisher
            "direct": True,
        }
    ]
    assert sources.openbd_calls == [("9784864723428", False)]
    _assert_nothing_was_searched(sources)


def test_search_converts_an_isbn10_with_x_check_digit(sources, client):
    # The X check digit counts as 10; the converted ISBN-13 gets a fresh,
    # correct check digit (openBD would reject a wrong one).
    client.get("/api/search", params={"q": "0-8044-2957-X"})

    assert sources.openbd_calls == [("9780804429573", False)]


@pytest.mark.parametrize(
    "query",
    [
        "転生したらスライムだった件",
        "本好きの下剋上",
        "1984",  # a number that is not an ISBN
        "9784864723429",  # ISBN-13 with a wrong check digit
        "4864723428",  # ISBN-10 with a wrong check digit
        "978486472342",  # 12 digits
        "n6316",  # not an ncode
        "n6316bnx",
        "https://example.com/n6316bn/",
        "https://kakuyomu.jp/works/abc",
        "https://kakuyomu.jp/users/someone",
        "https://anilist.co/user/someone",
        "https://anilist.co/staff/101280",
        "https://novel18.syosetu.com/n6316bn/",
    ],
)
def test_search_runs_a_normal_search_for_other_queries(sources, client, query):
    response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    assert sources.anilist.calls == [query]
    assert sources.narou_calls == [(query, False)]
    assert sources.narou_novel_calls == []
    assert sources.openbd_calls == []
    assert all(not r["direct"] for r in response.json()["results"])


def test_search_direct_resolution_ignores_the_types_filter(sources, client):
    sources.narou_novel_result = _narou_dto()

    body = client.get(
        "/api/search", params={"q": "n6316bn", "types": "anime"}
    ).json()

    assert [r["externalId"] for r in body["results"]] == ["n6316bn"]


def test_search_direct_resolution_passes_refresh(monkeypatch, sources, client):
    anilist = FakeAniListAdapterWithRefresh()
    anilist.media_result = _anilist_media()
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: anilist)
    sources.narou_novel_result = _narou_dto()
    sources.kakuyomu.work_result = _kakuyomu_dto()

    client.get("/api/search", params={"q": "n6316bn", "refresh": "1"})
    client.get("/api/search", params={"q": "https://anilist.co/anime/101280", "refresh": "1"})
    client.get("/api/search", params={"q": "https://anilist.co/anime/101280"})
    client.get(
        "/api/search",
        params={"q": "https://kakuyomu.jp/works/1177354054886847987", "refresh": "1"},
    )
    client.get("/api/search", params={"q": "9784864723428", "refresh": "1"})

    assert sources.narou_novel_calls == [("n6316bn", True)]
    assert sources.kakuyomu.work_calls == [("1177354054886847987", True)]
    assert sources.openbd_calls == [("9784864723428", True)]
    assert anilist.media_refresh_flags == [True, False]
    assert anilist.media_calls == ["101280", "101280"]


@pytest.mark.parametrize(
    "query,source",
    [
        ("https://ncode.syosetu.com/n9999zz/", "narou"),
        ("https://kakuyomu.jp/works/1", "kakuyomu"),
        ("https://anilist.co/anime/1", "anilist"),
        ("9784864723428", "openbd"),
    ],
)
def test_search_direct_failure_is_reported_and_recorded(sources, client, query, source):
    sources.narou_novel_error = SourceUnavailable("narou", "novel not found: n9999zz")
    sources.kakuyomu.work_error = SourceUnavailable("kakuyomu", "work 1 not found")
    sources.anilist.media_error = SourceUnavailable("anilist", "Not Found")
    sources.openbd_error = SourceUnavailable("openbd", "isbn not found: 9784864723428")

    response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert [e["source"] for e in body["errors"]] == [source]
    # A pasted URL is never turned into a keyword search on failure.
    _assert_nothing_was_searched(sources)
    health = client.get("/api/health").json()
    assert health["errors"][source]["message"] == body["errors"][0]["message"]


# -- parse_direct_query ----------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("https://ncode.syosetu.com/N6316BN/", ("narou", "n6316bn")),
        ("N6316BN", ("narou", "n6316bn")),
        ("https://kakuyomu.jp/works/42", ("kakuyomu", "42")),
        ("https://anilist.co/manga/86355/Slime/", ("anilist", "86355")),
        ("978-4-86472-342-8", ("isbn", "9784864723428")),
        ("4-86472-342-7", ("isbn", "9784864723428")),
        ("979-10-90636-07-1", ("isbn", "9791090636071")),
        ("転生したらスライムだった件", None),
        ("", None),
        ("   ", None),
    ],
)
def test_parse_direct_query(query, expected):
    assert search_module.parse_direct_query(query) == expected


# -- anilist-origins (原作候補) ------------------------------------------------


def test_anilist_origins_returns_source_candidates(monkeypatch, client):
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


def test_anilist_origins_propagates_source_unavailable_as_502(monkeypatch, client):
    from backend.adapters.anilist import AniListAdapter

    real_adapter = AniListAdapter.__new__(AniListAdapter)

    async def fake_graphql(query, variables):
        raise SourceUnavailable("anilist", "down")

    real_adapter._graphql = fake_graphql
    monkeypatch.setattr(search_module, "get_anilist_adapter", lambda: real_adapter)

    response = client.get("/api/search/anilist-origins/101280")

    assert response.status_code == 502
    assert client.get("/api/health").json()["errors"]["anilist"]["message"] == "down"
