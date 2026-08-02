"""Request/response models.

Computed physics lives in ``ComputedResponse.data`` as a passthrough
``dict[str, Any]`` — pydantic does not re-coerce values under ``Any``, so
the package's exact floats reach the wire unchanged. ``provenance`` carries
everything needed to reproduce or cite a result.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Operation = Literal[
    "histogram",
    "comparison",
    "replica_envelope",
    "uncertainty_breakdown",
    "covariance_matrix",
    "adhoc_histogram",
]

ResourceKind = Literal["package", "analysis"]
# Provenance can also originate from an ephemeral upload, which is not a
# registry resource.
ProvenanceKind = Literal["package", "analysis", "upload"]

# How to read ``checksum_sha256``: a package reports the hash of its own
# event file ("package"); an analysis has no single file, so the backend
# derives a composite over its members' checksums ("analysis_composite");
# an upload reports the hash of the uploaded file ("upload").
ChecksumKind = Literal["package", "analysis_composite", "upload"]


class ResourceSummary(BaseModel):
    id: str
    kind: ResourceKind
    source: str
    checksum_sha256: str | None
    checksum_kind: ChecksumKind = Field(
        description="'package' = the package's own event-file hash; "
        "'analysis_composite' = a backend-derived aggregate over members."
    )
    format_version: str | None
    event_count: int | None = None
    observables: list[str] = Field(default_factory=list)


class ResourceListResponse(BaseModel):
    resources: list[ResourceSummary]


class Provenance(BaseModel):
    """Everything needed to reproduce or cite a computed result.

    ``checksum_sha256`` is the integrity anchor; read it together with
    ``checksum_kind`` — a ``package`` checksum is the hash of a real event
    file, an ``analysis_composite`` is a synthetic aggregate the backend
    computed over the analysis members. A citation must not present the
    latter as if it were a file hash.
    """

    resource_id: str
    kind: ProvenanceKind
    source: str
    checksum_sha256: str | None
    checksum_kind: ChecksumKind
    operation: Operation
    parameters: dict[str, Any]
    resolved_bins: list[float] | None = None
    format_version: str | None
    api_version: str
    package_version: str
    computed_at: str
    cached: bool


class ComputedResponse(BaseModel):
    data: dict[str, Any]
    provenance: Provenance


class CitationResponse(BaseModel):
    resource_id: str
    format: Literal["biblatex"]
    entry_type: Literal["dataset", "misc"]
    has_doi: bool
    citation: str


class JobProgress(BaseModel):
    current: int
    total: int
    fraction: float | None


class JobStatus(BaseModel):
    job_id: str
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: JobProgress
    error: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None


class JobSubmitResponse(BaseModel):
    job_id: str
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]


class FileInspectResponse(BaseModel):
    format: Literal["parquet", "hdf5"]
    size_bytes: int
    checksum_sha256: str | None
    columns: list[str]
    n_rows: int | None


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    data_root_ok: bool
    resources_discovered: int


class VersionResponse(BaseModel):
    api_version: str
    package_version: str
    environment: str
