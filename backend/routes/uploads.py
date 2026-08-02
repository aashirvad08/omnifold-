"""Upload-driven routes: file inspect, ad-hoc histogram, upload-histogram
job.

Uploads are streamed to disk in chunks, size-capped (checked against
Content-Length up front and enforced byte-for-byte while staging),
format-validated by magic bytes, and deleted as soon as the work using
them finishes. The upload kind must be permitted by
``allowed_source_kinds``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from omnifold_publication.sources.upload import UploadDataSource

from .. import API_VERSION
from ..config import Settings
from ..dependencies import get_cache, get_jobs, get_settings
from ..models.schemas import (
    ComputedResponse,
    FileInspectResponse,
    JobSubmitResponse,
    Provenance,
)
from ..services.cache import CacheTier, ResultCache, make_cache_key
from ..services.compute import _package_version
from ..services.jobs import CancelToken, JobManager
from ..services.uploads import (
    UploadInvalidFormat,
    UploadTooLarge,
    adhoc_histogram,
    inspect_upload,
    stage_upload,
)

router = APIRouter(tags=["uploads"])


def _require_upload_enabled(settings: Settings) -> None:
    if "upload" not in settings.allowed_source_kinds:
        raise HTTPException(403, "upload source kind is disabled")


def _check_content_length(request: Request, settings: Settings) -> None:
    length = request.headers.get("content-length")
    if length is not None and int(length) > settings.max_upload_bytes:
        raise HTTPException(
            413, f"upload exceeds the {settings.max_upload_bytes}-byte limit"
        )


def _stage(
    file: UploadFile, settings: Settings, token: CancelToken | None
) -> UploadDataSource:
    if not file.filename:
        raise HTTPException(400, "a filename is required")
    try:
        return stage_upload(
            file.file,
            settings.upload_dir,
            file.filename,
            settings.max_upload_bytes,
            token=token,
        )
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except UploadInvalidFormat as exc:
        raise HTTPException(415, str(exc)) from exc


@router.post("/files/inspect", response_model=FileInspectResponse)
def inspect_file(
    request: Request,
    file: UploadFile,
    settings: Settings = Depends(get_settings),
) -> FileInspectResponse:
    _require_upload_enabled(settings)
    _check_content_length(request, settings)
    source = _stage(file, settings, token=None)
    try:
        return FileInspectResponse(**inspect_upload(source))
    finally:
        source.cleanup()


@router.post("/histograms/adhoc", response_model=ComputedResponse)
def adhoc(
    request: Request,
    file: UploadFile,
    observable: str = Query(...),
    bins: list[float] | None = Query(None),
    nbins: int | None = Query(None),
    weight_column: str | None = Query(None),
    settings: Settings = Depends(get_settings),
    cache: ResultCache = Depends(get_cache),
) -> ComputedResponse:
    _require_upload_enabled(settings)
    _check_content_length(request, settings)
    if bins and nbins is not None:
        raise HTTPException(400, "pass either bins or nbins, not both")
    resolved: list[float] | int | None = (
        [float(b) for b in bins] if bins else nbins
    )

    source = _stage(file, settings, token=None)
    try:
        checksum = source.checksum()
        key = make_cache_key(
            "adhoc_histogram", checksum, observable, resolved,
            variation=weight_column,
        )
        data = cache.get(CacheTier.SESSION, key)
        cached = data is not None
        if data is None:
            data = adhoc_histogram(source, observable, resolved, weight_column)
            cache.set(CacheTier.SESSION, key, data)
    finally:
        source.cleanup()

    provenance = Provenance(
        resource_id=file.filename or "upload",
        kind="upload",
        source=f"upload:{file.filename}",
        checksum_sha256=checksum,
        checksum_kind="upload",
        operation="adhoc_histogram",
        parameters={"observable": observable, "bins": bins, "nbins": nbins,
                    "weight_column": weight_column},
        resolved_bins=data.get("edges"),
        format_version=None,
        api_version=API_VERSION,
        package_version=_package_version(),
        computed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        cached=cached,
    )
    return ComputedResponse(data=data, provenance=provenance)


@router.post(
    "/jobs/upload-histogram", response_model=JobSubmitResponse, status_code=202
)
def upload_histogram_job(
    request: Request,
    file: UploadFile,
    observables: list[str] = Query(...),
    bins: list[float] | None = Query(None),
    nbins: int | None = Query(None),
    weight_column: str | None = Query(None),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> JobSubmitResponse:
    _require_upload_enabled(settings)
    _check_content_length(request, settings)
    resolved: list[float] | int | None = (
        [float(b) for b in bins] if bins else nbins
    )
    # stage the upload in-request (the UploadFile is gone after the response)
    source = _stage(file, settings, token=None)

    def work(
        token: CancelToken, report: Any
    ) -> dict[str, Any]:
        try:
            histograms: dict[str, Any] = {}
            total = len(observables)
            for index, observable in enumerate(observables):
                token.checkpoint()  # cancel stops before the next observable
                report(index, total)
                histograms[observable] = adhoc_histogram(
                    source, observable, resolved, weight_column
                )
            report(total, total)
            return {
                "histograms": histograms,
                "checksum_sha256": source.checksum(),
            }
        finally:
            source.cleanup()

    job_id = jobs.submit(work)
    return JobSubmitResponse(job_id=job_id, state="queued")
