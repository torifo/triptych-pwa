"""The one-writer lock of catalog.db: an exclusive flock on `local/catalog-job.lock`.

The collector's job API and the CLI's `build` / `link` both take it, so a build started from the
settings screen and one started in a terminal never write the catalog at the same time. The job API
hands its child the locked descriptor and sets HELD_ENV, so that child does not try to take it again.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:  # no flock on this platform: commands still run, only the cross-process guard is lost
    fcntl = None  # type: ignore[assignment]

LOCK_NAME = "catalog-job.lock"
HELD_ENV = "TRIPTYCH_CATALOG_LOCK_HELD"


def acquire(local_dir: Path) -> Optional[int]:
    """Opens `<local_dir>/catalog-job.lock` and takes an exclusive flock on it.

    Returns the open descriptor, or None when another process holds the lock. Raises OSError when the
    directory or the file cannot be made.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(local_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    if fcntl is None:
        return fd
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError:
        os.close(fd)
        raise
    return fd


def holder(local_dir: Path) -> str:
    """The pid the holder wrote into the lock file ('' when unknown)."""
    try:
        return (local_dir / LOCK_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
