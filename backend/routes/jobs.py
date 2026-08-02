"""Job status / result / cancel.

Status-code semantics (see services/jobs.py for the full contract):

- ``GET /jobs/{id}``          -> 200 status for any known job; 404 unknown.
- ``GET /jobs/{id}/result``   -> 200 payload only when ``succeeded``;
                                 409 while pending or if failed/cancelled;
                                 404 unknown.
- ``DELETE /jobs/{id}``       -> 200 status; requests cancellation, and is
                                 idempotent on an already-terminal job.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_jobs
from ..models.schemas import JobStatus
from ..services.jobs import Job, JobManager

router = APIRouter(tags=["jobs"])


def _require(jobs: JobManager, job_id: str) -> Job:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job {job_id!r}")
    return job


@router.get("/jobs/{job_id}", response_model=JobStatus)
def job_status(
    job_id: str, jobs: JobManager = Depends(get_jobs)
) -> JobStatus:
    return JobStatus(**_require(jobs, job_id).snapshot())


@router.get("/jobs/{job_id}/result")
def job_result(
    job_id: str, jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    job = _require(jobs, job_id)
    if job.state.value != "succeeded":
        raise HTTPException(
            409,
            f"job {job_id!r} is {job.state.value}; result is only available "
            "when succeeded",
        )
    assert job.result is not None
    return job.result


@router.delete("/jobs/{job_id}", response_model=JobStatus)
def job_cancel(
    job_id: str, jobs: JobManager = Depends(get_jobs)
) -> JobStatus:
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job {job_id!r}")
    return JobStatus(**job.snapshot())
