"""`/api/catalog/jobs/*` — run the catalog's light update from the settings screen.

`POST /api/catalog/jobs/recent` starts `python -m backend.catalog build --recent` (all sources, then
link; ~15 minutes) as a child process and answers 202 at once. `GET /api/catalog/jobs/current`
reports the latest job: state, exit code and the last lines of its log. The collector keeps no job
history — a restart forgets it (`job: null`); the child's log stays in `local/`.

Only one build runs at a time:
- in this process, the latest job still being `running` answers 409 `already running`;
- across processes, `local/catalog-job.lock` carries an exclusive `flock`. The collector hands the
  locked file descriptor to the child, so the lock lives exactly as long as the child does: it
  survives a collector restart (the child is not stopped with the collector) and can never be stale,
  because the OS drops it when the last holder dies — the file's content (the child's pid) is only
  for people to read. A start refused by the lock answers 409 as well.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal, Optional

try:
    import fcntl
except ImportError:  # no flock on this platform: jobs still run, only the cross-process guard is lost
    fcntl = None  # type: ignore[assignment]

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/catalog/jobs", tags=["catalog"])

REPO_DIR = Path(__file__).resolve().parents[2]
LOCAL_DIR = REPO_DIR / "local"  # logs and the lock file; read at call time so tests can point it elsewhere
LOCK_NAME = "catalog-job.lock"
MAX_LINES = 40
MAX_LINE_CHARS = 400
TAIL_BYTES = 64 * 1024  # how much of the log's end is read to find those lines
ALREADY_RUNNING = "already running"

JobState = Literal["running", "succeeded", "failed"]


class Job(BaseModel):
    """Mirrored in src/lib/catalog/types.ts."""

    id: str
    kind: Literal["recent"] = "recent"
    state: JobState
    startedAt: str
    finishedAt: Optional[str] = None
    exitCode: Optional[int] = None
    lines: list[str] = Field(default_factory=list)


class JobResponse(BaseModel):
    job: Job


class CurrentJobResponse(BaseModel):
    job: Optional[Job] = None


@dataclass
class _Job:
    id: str
    started_at: str
    state: JobState = "running"
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    log_path: Optional[Path] = None
    final_lines: Optional[list[str]] = None  # fixed once the job is over (the log may be deleted later)

    def lines(self) -> list[str]:
        if self.final_lines is not None:
            return self.final_lines
        return _tail_lines(self.log_path) if self.log_path else []

    def dto(self) -> Job:
        return Job(
            id=self.id,
            state=self.state,
            startedAt=self.started_at,
            finishedAt=self.finished_at,
            exitCode=self.exit_code,
            lines=self.lines(),
        )


_current: Optional[_Job] = None  # the latest job; only touched on the event loop
_watchers: set[asyncio.Task] = set()  # keeps the tasks that wait for children from being collected


def recent_command() -> list[str]:
    """The command a job runs. A function (not a constant) so tests can swap it."""
    return [sys.executable, "-m", "backend.catalog", "build", "--recent"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail_lines(path: Path) -> list[str]:
    """The last MAX_LINES non-blank lines of a log, each cut to MAX_LINE_CHARS."""
    try:
        with path.open("rb") as log:
            log.seek(0, os.SEEK_END)
            size = log.tell()
            start = max(size - TAIL_BYTES, 0)
            log.seek(start)
            data = log.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # the read began mid-line
    lines = [line[:MAX_LINE_CHARS] for line in lines if line.strip()]
    return lines[-MAX_LINES:]


def _acquire_lock() -> Optional[int]:
    """Opens `local/catalog-job.lock` and takes an exclusive flock on it.

    Returns the open descriptor, or None when another process holds the lock. Raises OSError
    when the directory or the file cannot be made.
    """
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCAL_DIR / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
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


def _open_log(started_at: datetime) -> tuple[Path, BinaryIO]:
    """Creates `local/catalog-build-recent-<YYYYMMDD-HHMMSS>.log` (never reusing a name)."""
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    for n in itertools.count():
        suffix = f"-{n}" if n else ""
        path = LOCAL_DIR / f"catalog-build-recent-{stamp}{suffix}.log"
        try:
            return path, path.open("xb")
        except FileExistsError:
            continue
    raise AssertionError("unreachable")


def _fail(job: _Job, reason: str) -> None:
    job.state = "failed"
    job.finished_at = _now()
    job.final_lines = [reason[:MAX_LINE_CHARS]]
    if job.log_path is not None:  # keep the reason with the log too (best effort)
        with contextlib.suppress(OSError), job.log_path.open("ab") as log:
            log.write((reason + "\n").encode("utf-8", errors="replace"))


async def _spawn(job: _Job, lock_fd: int) -> None:
    """Starts the child with its output in the log. Never raises: a failure settles the job."""
    log = None
    try:
        job.log_path, log = _open_log(datetime.now())
        proc = await asyncio.create_subprocess_exec(
            *recent_command(),
            cwd=REPO_DIR,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},  # so the log's tail is live
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(lock_fd,),  # the child, not the collector, holds the lock from here on
            start_new_session=True,  # a Ctrl-C or restart of the collector must not stop the build
        )
    except Exception as exc:  # no such executable, no permission, the log cannot be made, ...
        _fail(job, f"failed to start: {type(exc).__name__}: {exc}")
        return
    else:
        with contextlib.suppress(OSError):
            os.ftruncate(lock_fd, 0)
            os.pwrite(lock_fd, f"{proc.pid}\n".encode(), 0)
        watcher = asyncio.create_task(_watch(job, proc))
        _watchers.add(watcher)
        watcher.add_done_callback(_watchers.discard)
    finally:
        if log is not None:
            log.close()  # the child has its own copy
        os.close(lock_fd)


async def _watch(job: _Job, proc: asyncio.subprocess.Process) -> None:
    """Settles the job when the child ends. If the collector stops first, the child just carries on."""
    code = await proc.wait()
    job.final_lines = _tail_lines(job.log_path) if job.log_path else []
    job.exit_code = code
    job.finished_at = _now()
    job.state = "succeeded" if code == 0 else "failed"


@router.post("/recent", status_code=202, response_model=JobResponse)
async def start_recent() -> JobResponse:
    global _current
    # Everything up to `_current = job` runs without awaiting, so two requests cannot both pass.
    if _current is not None and _current.state == "running":
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING)
    job = _Job(id=uuid.uuid4().hex, started_at=_now())
    try:
        lock_fd = _acquire_lock()
    except OSError as exc:
        _current = job
        _fail(job, f"failed to start: {type(exc).__name__}: {exc}")
        return JobResponse(job=job.dto())
    if lock_fd is None:  # a build in another process (another collector, or an orphaned child)
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING)
    _current = job
    await _spawn(job, lock_fd)
    return JobResponse(job=job.dto())


@router.get("/current", response_model=CurrentJobResponse)
async def get_current() -> CurrentJobResponse:
    return CurrentJobResponse(job=_current.dto() if _current is not None else None)
