"""Catalog CLI.

    python -m backend.catalog build [--source anilist,syoboi,...] [--limit N] [--fresh | --recent] [--no-link]
    python -m backend.catalog link
    python -m backend.catalog export [--format csv|json|both] [--out local/catalog]
    python -m backend.catalog stats

Sources run in order; `jpro` (NDL's 近刊 records from JPRO) comes after `ndl` and writes into
the same `ndl:<isbn>` rows.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from . import db
from .runtime import DEFAULT_TIMEOUT, HarvestResult, RealClock

SOURCES = ("anilist", "syoboi", "narou", "kakuyomu", "ndl", "jpro")
REPO_DIR = Path(__file__).resolve().parents[2]


def _log(message: str) -> None:
    print(message, flush=True)


def _duration(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{rest:02d}s" if hours else f"{minutes}m{rest:02d}s"


async def _build(
    sources: list[str], limit: int | None, fresh: bool, recent: bool = False
) -> list[HarvestResult]:
    """Harvests each source in turn. `recent` runs the light update (each harvester's `recent=True`)."""
    conn = db.connect()
    clock = RealClock()
    results: list[HarvestResult] = []
    extra = {"recent": True} if recent else {}  # a harvester is only told about the flag it is given
    started = time.monotonic()
    if recent:
        _log(f"recent update: {', '.join(sources)}")
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, http2=False) as client:
        for source in sources:
            began = time.monotonic()
            if fresh:
                db.reset_source(conn, source)
            module = importlib.import_module(f"backend.catalog.harvest.{source}")
            _log(f"== {source} ==")
            try:
                result = await module.harvest(conn, client, clock=clock, limit=limit, log=_log, **extra)
            except Exception as exc:  # one source must not stop the others
                result = HarvestResult(source=source, errors=[f"{type(exc).__name__}: {exc}"])
            now = db.utc_now()
            db.set_meta(conn, f"harvest:{source}:at", now)  # "last harvested", shown in the app
            if recent:  # the full build's own result stays as it was
                db.set_meta(conn, f"harvest:{source}:recent_at", now)
                db.set_meta(conn, f"harvest:{source}:recent_result", result.__dict__)
            else:
                db.set_meta(conn, f"harvest:{source}:result", result.__dict__)
            results.append(result)
            _log(
                f"-- {source}: fetched={result.fetched} done={result.slices_done} "
                f"skipped={result.slices_skipped} error={result.slices_error} "
                f"({_duration(time.monotonic() - began)})"
            )
            for err in result.errors[:10]:
                _log(f"   ! {err}")
    conn.close()
    _log(f"harvest finished in {_duration(time.monotonic() - started)}")
    return results


def _link() -> None:
    from .link import run_link

    conn = db.connect()
    summary = run_link(conn, today=date.today())
    db.set_meta(conn, "link:at", db.utc_now())
    db.set_meta(conn, "link:summary", summary)
    conn.close()
    _log(f"link: {json.dumps(summary, ensure_ascii=False)}")


def _export(fmt: str, out: Path) -> None:
    from .export import write_files

    conn = db.connect(create=False)
    paths = write_files(conn, out, formats=("csv", "json") if fmt == "both" else (fmt,))
    conn.close()
    for path in paths:
        _log(f"wrote {path}")


def _stats() -> None:
    try:
        conn = db.connect(create=False)
    except FileNotFoundError:
        _log("catalog not built (run: python -m backend.catalog build)")
        return
    for row in conn.execute("SELECT source, kind, COUNT(*) AS n FROM entries GROUP BY source, kind ORDER BY source, kind"):
        _log(f"{row['source']:9} {row['kind']:10} {row['n']:>7}")
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE source = 'ndl' AND json_extract(extra, '$.via') = 'jpro'"
    ).fetchone()[0]
    _log(f"  (ndl rows from JPRO, not catalogued yet: {upcoming})")
    works = conn.execute("SELECT COUNT(*) FROM cluster_summary").fetchone()[0]
    links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    errors = conn.execute("SELECT source, COUNT(*) FROM harvest_state WHERE status = 'error' GROUP BY source").fetchall()
    _log(f"works={works} links={links} error_slices={dict((r[0], r[1]) for r in errors)}")
    conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.catalog")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="harvest sources into backend/catalog.db")
    build.add_argument("--source", default=",".join(SOURCES), help="comma separated: " + ",".join(SOURCES))
    build.add_argument("--limit", type=int, default=None, help="max rows per source (trial run)")
    build.add_argument("--fresh", action="store_true", help="forget resume state and re-fetch every slice")
    build.add_argument(
        "--recent",
        action="store_true",
        help="light update (~15 min): upcoming books, this year on and airing anime, top web novels",
    )
    build.add_argument("--no-link", action="store_true", help="skip linking after harvest")

    sub.add_parser("link", help="rebuild cross-source links, works and search index")

    export = sub.add_parser("export", help="write the whole catalog as CSV / JSON")
    export.add_argument("--format", choices=("csv", "json", "both"), default="both")
    export.add_argument("--out", type=Path, default=REPO_DIR / "local" / "catalog")

    sub.add_parser("stats", help="print counts")

    args = parser.parse_args(argv)
    if args.command == "build":
        sources = [s.strip() for s in args.source.split(",") if s.strip()]
        unknown = [s for s in sources if s not in SOURCES]
        if unknown:
            parser.error(f"unknown source: {', '.join(unknown)}")
        if args.recent and args.fresh:
            parser.error("--recent and --fresh cannot be combined (--fresh re-fetches everything)")
        asyncio.run(_build(sources, args.limit, args.fresh, args.recent))
        if not args.no_link:
            _link()
    elif args.command == "link":
        _link()
    elif args.command == "export":
        _export(args.format, args.out)
    elif args.command == "stats":
        _stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
