"""Tests for the catalog CLI (backend/catalog/__main__.py): `build` and its flags.

No network and no real harvesters: each source module is replaced by a fake that
records how it was called, the catalog DB is a temporary file (TRIPTYCH_CATALOG_DB),
and `link` is a stub.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from backend.catalog import __main__ as cli
from backend.catalog import db, runtime


class Harvesters:
    """Fake `backend.catalog.harvest.<source>` modules and a stub `backend.catalog.link`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        self.linked: list[str] = []
        self.fail: set[str] = set()
        self.fetched = 3
        for source in cli.SOURCES:
            self._install(monkeypatch, source)
        link = types.ModuleType("backend.catalog.link")
        link.run_link = self._run_link  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, link.__name__, link)

    def _install(self, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
        module = types.ModuleType(f"backend.catalog.harvest.{source}")

        async def harvest(conn, client, *, clock, limit=None, log=print, **extra):
            self.calls.append({"source": source, "limit": limit, **extra})
            if source in self.fail:
                raise RuntimeError("boom")
            return runtime.HarvestResult(source=source, fetched=self.fetched, slices_done=2)

        module.harvest = harvest  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module.__name__, module)

    def _run_link(self, conn, *, today) -> dict[str, Any]:
        self.linked.append(str(today))
        return {"entries": conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0], "links": 0}

    @property
    def sources(self) -> list[str]:
        return [call["source"] for call in self.calls]


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "catalog.db"
    monkeypatch.setenv(db.DB_ENV_VAR, str(path))
    return path


@pytest.fixture()
def harvesters(monkeypatch, catalog) -> Harvesters:
    return Harvesters(monkeypatch)


def meta(catalog, key: str) -> Any:
    conn = db.connect(catalog)
    try:
        return db.get_meta(conn, key)
    finally:
        conn.close()


def test_plain_build_runs_every_source_without_any_recent_flag(harvesters, catalog):
    assert cli.main(["build"]) == 0

    assert harvesters.sources == ["anilist", "syoboi", "narou", "kakuyomu", "ndl", "jpro"]
    assert all(call == {"source": call["source"], "limit": None} for call in harvesters.calls)  # no `recent` kwarg
    assert len(harvesters.linked) == 1
    assert meta(catalog, "harvest:ndl:result")["fetched"] == 3
    assert meta(catalog, "harvest:ndl:at")
    assert meta(catalog, "harvest:ndl:recent_at") is None


def test_recent_build_passes_recent_to_every_source_and_then_links(harvesters, catalog, capsys):
    assert cli.main(["build", "--recent"]) == 0

    assert harvesters.sources == ["anilist", "syoboi", "narou", "kakuyomu", "ndl", "jpro"]
    assert all(call["recent"] is True and call["limit"] is None for call in harvesters.calls)
    assert len(harvesters.linked) == 1
    out = capsys.readouterr().out
    assert "recent" in out and "== anilist ==" in out and "-- ndl: fetched=3" in out


def test_recent_build_records_its_own_meta_and_keeps_the_full_builds_result(harvesters, catalog):
    conn = db.connect(catalog)
    full = {"source": "ndl", "fetched": 24729, "slices_done": 40, "slices_skipped": 0, "slices_error": 0, "errors": []}
    db.set_meta(conn, "harvest:ndl:result", full)
    db.set_meta(conn, "harvest:ndl:at", "2026-10-14T10:24:27+00:00")
    conn.close()

    cli.main(["build", "--recent", "--source", "ndl", "--no-link"])

    assert meta(catalog, "harvest:ndl:result") == full  # the full build's result is not overwritten
    recent = meta(catalog, "harvest:ndl:recent_result")
    assert (recent["fetched"], recent["slices_done"]) == (3, 2)
    stamp = meta(catalog, "harvest:ndl:recent_at")
    assert stamp and stamp > "2026-10-14T10:24:27+00:00"
    assert meta(catalog, "harvest:ndl:at") == stamp  # "last harvested" shown in the app moves forward


def test_recent_build_takes_source_limit_and_no_link(harvesters, catalog):
    cli.main(["build", "--recent", "--source", "ndl,anilist", "--limit", "5", "--no-link"])

    assert harvesters.calls == [
        {"source": "ndl", "limit": 5, "recent": True},
        {"source": "anilist", "limit": 5, "recent": True},
    ]
    assert harvesters.linked == []


def test_recent_and_fresh_are_exclusive(harvesters, catalog, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["build", "--recent", "--fresh"])

    assert stop.value.code == 2
    assert harvesters.calls == []
    assert "--recent" in capsys.readouterr().err


def test_fresh_forgets_the_resume_state_and_recent_keeps_it(harvesters, catalog):
    conn = db.connect(catalog)
    db.mark_slice(conn, "ndl", "913.6:2026-10", "done", fetched=9)
    conn.close()

    cli.main(["build", "--recent", "--source", "ndl", "--no-link"])
    conn = db.connect(catalog)
    assert db.is_slice_done(conn, "ndl", "913.6:2026-10")  # recent: the records stay (the harvester decides)
    conn.close()

    cli.main(["build", "--fresh", "--source", "ndl", "--no-link"])
    conn = db.connect(catalog)
    assert not db.is_slice_done(conn, "ndl", "913.6:2026-10")
    conn.close()


def test_a_source_that_raises_does_not_stop_the_others(harvesters, catalog, capsys):
    harvesters.fail = {"syoboi"}

    cli.main(["build", "--recent", "--no-link"])

    assert harvesters.sources == ["anilist", "syoboi", "narou", "kakuyomu", "ndl", "jpro"]
    failed = meta(catalog, "harvest:syoboi:recent_result")
    assert failed["fetched"] == 0 and "RuntimeError: boom" in failed["errors"][0]
    assert meta(catalog, "harvest:ndl:recent_result")["fetched"] == 3
    assert "! RuntimeError: boom" in capsys.readouterr().out


def test_unknown_source_is_rejected(harvesters, catalog):
    with pytest.raises(SystemExit) as stop:
        cli.main(["build", "--recent", "--source", "anilist,nope"])
    assert stop.value.code == 2
    assert harvesters.calls == []
