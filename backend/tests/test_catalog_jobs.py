"""`/api/catalog/jobs/*` — the settings screen's "update the catalog" job.

The real `build --recent` is never run: `recent_command()` is swapped for a short `python -c`
script, and the lock/log directory is a temp dir.
"""

from __future__ import annotations

import concurrent.futures
import fcntl
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend.api import catalog_jobs

JOBS_RECENT = "/api/catalog/jobs/recent"
JOBS_CURRENT = "/api/catalog/jobs/current"
REPO_DIR = Path(__file__).resolve().parents[2]

# A child that prints, then exits 0.
OK_SCRIPT = "print('harvest start'); print('harvest done')"
# Writes to stderr and exits non-zero.
FAIL_SCRIPT = "import sys; print('boom', file=sys.stderr); sys.exit(3)"
# Prints, then waits (up to ~20 s) for the file named by argv[1] to appear.
GATED_SCRIPT = """
import pathlib, sys, time
print('started', flush=True)
gate = pathlib.Path(sys.argv[1])
for _ in range(400):
    if gate.exists():
        break
    time.sleep(0.05)
print('released', flush=True)
"""
MANY_LINES_SCRIPT = "\n".join(["for i in range(100):", "    print(f'line {i}')"])
INSPECT_SCRIPT = """
import os
print('cwd=' + os.getcwd())
print('db=' + os.environ.get('TRIPTYCH_CATALOG_DB', ''))
print('pgid_is_own=' + str(os.getpgrp() == os.getpid()))
"""


@pytest.fixture(autouse=True)
def jobs_env(tmp_path, monkeypatch):
    """Own lock/log directory, no job left over from another test."""
    monkeypatch.setattr(catalog_jobs, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(catalog_jobs, "_current", None)
    return tmp_path


@pytest.fixture()
def client():
    # The context manager keeps one event loop alive across requests, which is what
    # the background watcher task needs (as under uvicorn).
    with TestClient(app_module.app) as test_client:
        yield test_client


def use_script(monkeypatch, script: str, *args: str) -> None:
    monkeypatch.setattr(
        catalog_jobs, "recent_command", lambda: [sys.executable, "-c", script, *args]
    )


def current(client) -> dict | None:
    return client.get(JOBS_CURRENT).json()["job"]


def wait_for(client, predicate, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = current(client)
        if job is not None and predicate(job):
            return job
        assert time.monotonic() < deadline, f"timed out waiting; last job: {job}"
        time.sleep(0.02)


def wait_finished(client) -> dict:
    return wait_for(client, lambda job: job["state"] != "running")


# -- the command ---------------------------------------------------------------


def test_default_command_is_build_recent_in_the_collectors_python():
    assert catalog_jobs.recent_command() == [
        sys.executable, "-m", "backend.catalog", "build", "--recent",
    ]


# -- start / state ---------------------------------------------------------------


def test_current_is_null_before_any_job(client):
    response = client.get(JOBS_CURRENT)

    assert response.status_code == 200
    assert response.json() == {"job": None}


def test_post_returns_202_with_a_running_job(client, monkeypatch, jobs_env):
    gate = jobs_env / "gate"
    use_script(monkeypatch, GATED_SCRIPT, str(gate))

    response = client.post(JOBS_RECENT)

    assert response.status_code == 202
    job = response.json()["job"]
    assert set(job) == {"id", "kind", "state", "startedAt", "finishedAt", "exitCode", "lines"}
    assert job["id"]
    assert job["kind"] == "recent"
    assert job["state"] == "running"
    assert datetime.fromisoformat(job["startedAt"]).tzinfo is not None
    assert job["finishedAt"] is None
    assert job["exitCode"] is None
    assert isinstance(job["lines"], list)
    gate.touch()
    wait_finished(client)


def test_current_reports_the_running_job_with_the_log_so_far(client, monkeypatch, jobs_env):
    gate = jobs_env / "gate"
    use_script(monkeypatch, GATED_SCRIPT, str(gate))
    started = client.post(JOBS_RECENT).json()["job"]

    job = wait_for(client, lambda j: j["lines"] == ["started"])

    assert job["id"] == started["id"]
    assert job["state"] == "running"
    assert job["finishedAt"] is None
    gate.touch()
    wait_finished(client)


def test_a_successful_run_settles_as_succeeded(client, monkeypatch):
    use_script(monkeypatch, OK_SCRIPT)
    started = client.post(JOBS_RECENT).json()["job"]

    job = wait_finished(client)

    assert job["id"] == started["id"]
    assert job["state"] == "succeeded"
    assert job["exitCode"] == 0
    assert datetime.fromisoformat(job["finishedAt"]) >= datetime.fromisoformat(job["startedAt"])
    assert job["lines"] == ["harvest start", "harvest done"]


def test_a_non_zero_exit_settles_as_failed_with_stderr_in_the_lines(client, monkeypatch):
    use_script(monkeypatch, FAIL_SCRIPT)
    client.post(JOBS_RECENT)

    job = wait_finished(client)

    assert job["state"] == "failed"
    assert job["exitCode"] == 3
    assert job["finishedAt"] is not None
    assert job["lines"] == ["boom"]


def test_a_second_post_while_running_is_409(client, monkeypatch, jobs_env):
    gate = jobs_env / "gate"
    use_script(monkeypatch, GATED_SCRIPT, str(gate))
    first = client.post(JOBS_RECENT).json()["job"]

    second = client.post(JOBS_RECENT)

    assert second.status_code == 409
    assert second.json() == {"detail": "already running"}
    assert current(client)["id"] == first["id"]  # the running job is untouched
    gate.touch()
    wait_finished(client)


def test_a_new_job_can_start_once_the_last_one_is_done(client, monkeypatch):
    use_script(monkeypatch, OK_SCRIPT)
    first = client.post(JOBS_RECENT).json()["job"]
    wait_finished(client)

    second = client.post(JOBS_RECENT)

    assert second.status_code == 202
    assert second.json()["job"]["id"] != first["id"]
    assert wait_finished(client)["id"] == second.json()["job"]["id"]


def test_a_failed_job_does_not_block_the_next_one(client, monkeypatch):
    use_script(monkeypatch, FAIL_SCRIPT)
    client.post(JOBS_RECENT)
    wait_finished(client)
    use_script(monkeypatch, OK_SCRIPT)

    response = client.post(JOBS_RECENT)

    assert response.status_code == 202
    assert wait_finished(client)["state"] == "succeeded"


def test_concurrent_posts_start_only_one_job(monkeypatch, jobs_env):
    gate = jobs_env / "gate"
    use_script(monkeypatch, GATED_SCRIPT, str(gate))

    with TestClient(app_module.app) as test_client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: test_client.post(JOBS_RECENT), range(8)))
        codes = sorted(response.status_code for response in responses)
        gate.touch()
        wait_finished(test_client)

    assert codes == [202] + [409] * 7


# -- log -----------------------------------------------------------------------


def test_lines_are_the_last_40_lines_of_the_log(client, monkeypatch):
    use_script(monkeypatch, MANY_LINES_SCRIPT)
    client.post(JOBS_RECENT)

    job = wait_finished(client)

    assert len(job["lines"]) == 40
    assert job["lines"][0] == "line 60"
    assert job["lines"][-1] == "line 99"


def test_the_whole_output_goes_to_a_timestamped_log_under_local(client, monkeypatch, jobs_env):
    use_script(monkeypatch, MANY_LINES_SCRIPT)
    client.post(JOBS_RECENT)
    wait_finished(client)

    logs = sorted((jobs_env / "local").glob("catalog-build-recent-*.log"))

    assert len(logs) == 1
    assert re.fullmatch(r"catalog-build-recent-\d{8}-\d{6}\.log", logs[0].name)
    assert logs[0].read_text().splitlines() == [f"line {i}" for i in range(100)]


def test_two_jobs_in_the_same_second_keep_separate_logs(client, monkeypatch, jobs_env):
    use_script(monkeypatch, OK_SCRIPT)
    client.post(JOBS_RECENT)
    wait_finished(client)
    client.post(JOBS_RECENT)
    wait_finished(client)

    logs = list((jobs_env / "local").glob("catalog-build-recent-*.log"))

    assert len(logs) == 2


def test_overlong_lines_are_cut(client, monkeypatch):
    use_script(monkeypatch, "print('x' * 5000)")
    client.post(JOBS_RECENT)

    job = wait_finished(client)

    assert len(job["lines"]) == 1
    assert 0 < len(job["lines"][0]) <= catalog_jobs.MAX_LINE_CHARS


def test_the_lines_stay_after_the_log_file_is_gone(client, monkeypatch, jobs_env):
    use_script(monkeypatch, OK_SCRIPT)
    client.post(JOBS_RECENT)
    finished = wait_finished(client)
    for log in (jobs_env / "local").glob("*.log"):
        log.unlink()

    assert current(client)["lines"] == finished["lines"]


# -- the child ---------------------------------------------------------------------


def test_the_child_runs_in_the_repo_root_with_the_collectors_environment(
    client, monkeypatch, tmp_path
):
    db_path = str(tmp_path / "somewhere" / "catalog.db")
    monkeypatch.setenv("TRIPTYCH_CATALOG_DB", db_path)
    use_script(monkeypatch, INSPECT_SCRIPT)
    client.post(JOBS_RECENT)

    job = wait_finished(client)

    assert f"cwd={REPO_DIR}" in job["lines"]
    assert f"db={db_path}" in job["lines"]


def test_the_child_is_detached_from_the_collectors_process_group(client, monkeypatch):
    # So a Ctrl-C (or a restart) of the collector does not take the build down with it.
    use_script(monkeypatch, INSPECT_SCRIPT)
    client.post(JOBS_RECENT)

    job = wait_finished(client)

    assert "pgid_is_own=True" in job["lines"]


def test_a_command_that_cannot_start_is_a_failed_job_with_the_reason(client, monkeypatch, jobs_env):
    missing = str(jobs_env / "no-such-python")
    monkeypatch.setattr(catalog_jobs, "recent_command", lambda: [missing, "-m", "x"])

    response = client.post(JOBS_RECENT)

    assert response.status_code == 202
    job = response.json()["job"]
    assert job["state"] == "failed"
    assert job["exitCode"] is None
    assert job["finishedAt"] is not None
    assert len(job["lines"]) == 1
    assert "no-such-python" in job["lines"][0]
    assert current(client) == job


def test_a_start_failure_releases_the_lock_for_the_next_try(client, monkeypatch, jobs_env):
    monkeypatch.setattr(
        catalog_jobs, "recent_command", lambda: [str(jobs_env / "no-such-python")]
    )
    client.post(JOBS_RECENT)
    use_script(monkeypatch, OK_SCRIPT)

    response = client.post(JOBS_RECENT)

    assert response.status_code == 202
    assert wait_finished(client)["state"] == "succeeded"


def test_a_lock_dir_that_cannot_be_made_is_a_failed_job(client, monkeypatch, jobs_env):
    blocker = jobs_env / "blocker"
    blocker.write_text("a file where the directory should be")
    monkeypatch.setattr(catalog_jobs, "LOCAL_DIR", blocker / "local")
    use_script(monkeypatch, OK_SCRIPT)

    response = client.post(JOBS_RECENT)

    assert response.status_code == 202
    job = response.json()["job"]
    assert job["state"] == "failed"
    assert job["lines"]


# -- lock (other processes) -----------------------------------------------------------


def hold_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_a_build_in_another_process_holding_the_lock_makes_post_409(client, monkeypatch, jobs_env):
    use_script(monkeypatch, OK_SCRIPT)
    fd = hold_lock(jobs_env / "local" / "catalog-job.lock")
    try:
        response = client.post(JOBS_RECENT)

        assert response.status_code == 409
        assert response.json() == {"detail": "already running"}
        assert current(client) is None  # nothing was started
    finally:
        os.close(fd)

    assert client.post(JOBS_RECENT).status_code == 202  # free again
    wait_finished(client)


def test_a_leftover_lock_file_nobody_holds_is_ignored(client, monkeypatch, jobs_env):
    lock = jobs_env / "local" / "catalog-job.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("99999\n")  # e.g. a pid from a collector that crashed
    use_script(monkeypatch, OK_SCRIPT)

    response = client.post(JOBS_RECENT)

    assert response.status_code == 202
    assert wait_finished(client)["state"] == "succeeded"


def test_the_running_child_keeps_the_lock_even_if_the_collector_forgot_the_job(
    client, monkeypatch, jobs_env
):
    # A collector restart loses the in-memory job, but the child lives on and still holds the lock.
    gate = jobs_env / "gate"
    use_script(monkeypatch, GATED_SCRIPT, str(gate))
    client.post(JOBS_RECENT)
    wait_for(client, lambda job: job["lines"] == ["started"])
    monkeypatch.setattr(catalog_jobs, "_current", None)
    assert current(client) is None

    response = client.post(JOBS_RECENT)

    assert response.status_code == 409
    gate.touch()
    deadline = time.monotonic() + 15
    while client.post(JOBS_RECENT).status_code == 409:  # the child finishes, the lock frees
        assert time.monotonic() < deadline, "the lock was never released"
        time.sleep(0.05)
    use_script(monkeypatch, OK_SCRIPT)
    wait_finished(client)


# -- auth ---------------------------------------------------------------------------------


def test_the_jobs_api_needs_the_bearer_token_when_one_is_configured(monkeypatch, jobs_env):
    monkeypatch.setenv("TRIPTYCH_TOKEN", "secret123")
    use_script(monkeypatch, OK_SCRIPT)
    auth = {"Authorization": "Bearer secret123"}

    with TestClient(app_module.app) as test_client:
        assert test_client.post(JOBS_RECENT).status_code == 401
        assert test_client.get(JOBS_CURRENT).status_code == 401
        assert test_client.post(JOBS_RECENT, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert test_client.get(JOBS_CURRENT, headers=auth).json() == {"job": None}  # nothing started

        assert test_client.post(JOBS_RECENT, headers=auth).status_code == 202
        deadline = time.monotonic() + 15
        while test_client.get(JOBS_CURRENT, headers=auth).json()["job"]["state"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.02)
