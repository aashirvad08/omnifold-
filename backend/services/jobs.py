"""Background jobs with cooperative cancellation.

Cancellation is **checkpoint-based**, not process-kill: a job function is
handed a :class:`CancelToken` and calls ``token.checkpoint()`` at safe
points (between upload chunks, between pipeline stages). Cancelling sets
the token; the worker raises :class:`JobCancelled` at its next checkpoint,
so in-flight work provably stops rather than merely being marked done.
This is far cheaper than process isolation and, for this package, loses
nothing: the expensive work is loops and streaming, all of which have
natural checkpoints. The one thing a checkpoint cannot interrupt is a
single atomic numpy/pandas call already in flight (e.g. one large
``read_hdf``); that is an accepted, documented limitation.

Job-state -> HTTP semantics (enforced in routes/jobs.py):

- ``queued`` / ``running`` -> 200 with the status body; result is 409.
- ``succeeded``            -> 200; result endpoint returns the payload.
- ``failed``               -> 200 status (error in body); result is 409.
- ``cancelled``            -> 200 status; result is 409. Progress is frozen
                             at its last value (not reset).
- unknown / expired id     -> 404.
- cancelling a terminal job is idempotent (200, no state change).

Retention: terminal jobs expire ``retention_seconds`` after finishing and
are swept lazily on access.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


class JobCancelled(Exception):
    """Raised at a checkpoint after cancellation is requested."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def checkpoint(self) -> None:
        """Raise if cancellation has been requested. Call between units of
        work so cancellation actually stops progress."""

        if self._event.is_set():
            raise JobCancelled()


# A job function receives a cancel token and a progress reporter
# ``report(current, total)``; it returns a JSON-able result.
JobFunc = Callable[[CancelToken, Callable[[int, int], None]], dict[str, Any]]


@dataclass
class Job:
    id: str
    state: JobState = JobState.QUEUED
    progress_current: int = 0
    progress_total: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    _token: CancelToken = field(default_factory=CancelToken)

    def snapshot(self) -> dict[str, Any]:
        fraction = (
            self.progress_current / self.progress_total
            if self.progress_total > 0
            else None
        )
        return {
            "job_id": self.id,
            "state": self.state.value,
            "progress": {
                "current": self.progress_current,
                "total": self.progress_total,
                "fraction": fraction,
            },
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    """In-memory job manager (dev/single-node).

    The interface (submit/get/result/cancel/sweep) is what a Redis/RQ-backed
    production manager would implement; only the storage differs.
    """

    def __init__(self, retention_seconds: int, max_workers: int = 4):
        self._jobs: dict[str, Job] = {}
        self._retention = retention_seconds
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, func: JobFunc) -> str:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job, func)
        return job.id

    def _run(self, job: Job, func: JobFunc) -> None:
        # Always invoke func, even if cancellation arrived while queued: the
        # function's first checkpoint raises immediately, and crucially its
        # own cleanup (e.g. deleting a staged upload) still runs. Skipping
        # func here would leak those resources.
        job.state = JobState.RUNNING
        job.started_at = time.time()

        def report(current: int, total: int) -> None:
            job.progress_current = current
            job.progress_total = total

        try:
            result = func(job._token, report)
        except JobCancelled:
            self._finish(job, JobState.CANCELLED)
        except Exception as exc:  # noqa: BLE001 - surfaced as job error
            job.error = f"{type(exc).__name__}: {exc}"
            self._finish(job, JobState.FAILED)
        else:
            job.result = result
            self._finish(job, JobState.SUCCEEDED)

    def _finish(self, job: Job, state: JobState) -> None:
        job.state = state
        job.finished_at = time.time()

    def get(self, job_id: str) -> Job | None:
        self._sweep()
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        """Request cancellation. Idempotent on terminal jobs."""

        self._sweep()
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.state not in _TERMINAL:
            job._token.cancel()
        return job

    def _sweep(self) -> None:
        cutoff = time.time() - self._retention
        with self._lock:
            expired = [
                jid
                for jid, job in self._jobs.items()
                if job.finished_at is not None and job.finished_at < cutoff
            ]
            for jid in expired:
                del self._jobs[jid]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
