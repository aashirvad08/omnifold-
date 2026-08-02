"""Job manager: cooperative cancellation actually stops in-flight work,
plus the documented state/status-code semantics."""

from __future__ import annotations

import threading
import time

from backend.services.jobs import JobManager, JobState


def _await_terminal(mgr: JobManager, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = mgr.get(job_id)
        assert job is not None
        if job.state in {
            JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED
        }:
            return job
        time.sleep(0.005)
    raise AssertionError("job did not reach a terminal state")


def test_cancellation_provably_stops_in_flight_work():
    """Barrier proof: a marker set *after* a post-cancel checkpoint must
    never be set — cancellation stops the work, it does not merely flip a
    status flag."""

    mgr = JobManager(retention_seconds=60)
    reached_first = threading.Event()
    release = threading.Event()
    continued_past_cancel = threading.Event()

    def work(token, report):
        token.checkpoint()          # checkpoint 1: not cancelled yet
        reached_first.set()
        release.wait(5.0)           # block until the test cancels + releases
        token.checkpoint()          # checkpoint 2: must raise now
        continued_past_cancel.set()  # <-- must NEVER run
        return {"ok": True}

    job_id = mgr.submit(work)
    assert reached_first.wait(5.0)  # worker is running, past checkpoint 1
    mgr.cancel(job_id)              # request cancel while it is blocked
    release.set()                  # unblock -> worker hits checkpoint 2

    job = _await_terminal(mgr, job_id)
    assert job.state is JobState.CANCELLED
    assert not continued_past_cancel.is_set()  # the proof
    mgr.shutdown()


def test_progress_is_frozen_on_cancel():
    mgr = JobManager(retention_seconds=60)
    at_two = threading.Event()
    release = threading.Event()

    def work(token, report):
        report(2, 10)
        at_two.set()
        release.wait(5.0)
        token.checkpoint()
        report(9, 10)  # must not be reached
        return {}

    job_id = mgr.submit(work)
    assert at_two.wait(5.0)
    mgr.cancel(job_id)
    release.set()
    job = _await_terminal(mgr, job_id)
    assert job.state is JobState.CANCELLED
    assert job.progress_current == 2  # frozen at last reported value
    mgr.shutdown()


def test_success_and_failure_states():
    mgr = JobManager(retention_seconds=60)

    ok = mgr.submit(lambda token, report: {"value": 42})
    job = _await_terminal(mgr, ok)
    assert job.state is JobState.SUCCEEDED
    assert job.result == {"value": 42}

    def boom(token, report):
        raise ValueError("nope")

    bad = mgr.submit(boom)
    job = _await_terminal(mgr, bad)
    assert job.state is JobState.FAILED
    assert "ValueError: nope" in (job.error or "")
    mgr.shutdown()


def test_cancel_is_idempotent_on_terminal_job():
    mgr = JobManager(retention_seconds=60)
    job_id = mgr.submit(lambda token, report: {"done": True})
    _await_terminal(mgr, job_id)
    # cancelling an already-succeeded job does not change its state
    job = mgr.cancel(job_id)
    assert job is not None
    assert job.state is JobState.SUCCEEDED
    mgr.shutdown()


def test_retention_sweep_expires_terminal_jobs():
    mgr = JobManager(retention_seconds=1)
    job_id = mgr.submit(lambda token, report: {"x": 1})
    job = _await_terminal(mgr, job_id)  # observe it succeeded first
    # deterministically age it past retention, then it must be swept on access
    job.finished_at = time.time() - 10.0
    assert mgr.get(job_id) is None
    mgr.shutdown()


# --- API-level status-code semantics -------------------------------------


def test_job_endpoints_status_codes(client):
    assert client.get("/jobs/does_not_exist").status_code == 404
    assert client.get("/jobs/does_not_exist/result").status_code == 404
    assert client.delete("/jobs/does_not_exist").status_code == 404
