"""Compute resolver — the one place the package is called for physics.

Each operation maps to exactly one package method; the *only* transform
applied to the returned numbers is :func:`to_jsonable` (arrays->lists,
NaN/Inf->null). Parity tests assert the wire result equals that same
transform applied to a direct package call, so "the API never recomputes
physics" is enforced, not merely intended.
"""

from __future__ import annotations

import importlib.metadata
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnifold_publication import OmniFoldAnalysis, OmniFoldPackage
from omnifold_publication.exceptions import PackageReadError

from .. import API_VERSION
from ..serialization import to_jsonable
from .cache import CacheTier, ResultCache, make_cache_key
from .registry import Registry, ResourceNotFound

# operations that need multiple variations, i.e. an analysis
_ANALYSIS_ONLY = {"comparison", "replica_envelope"}


class ComputeError(Exception):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code  # "bad_request" | "not_found" | "conflict"


@dataclass
class ComputeOutput:
    data: dict[str, Any]
    resolved_bins: list[float] | None
    cached: bool


def _package_version() -> str:
    try:
        return importlib.metadata.version("omnifold_publication")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "unknown"


def _resolved_bins_from(data: dict[str, Any]) -> list[float] | None:
    edges = data.get("edges")
    if edges is None:
        edges = data.get("bins")
    if isinstance(edges, list):
        return [float(x) for x in edges]
    return None


def _histogram(
    resource: OmniFoldPackage | OmniFoldAnalysis,
    observable: str,
    variation: str,
    bins: list[float] | int | None,
) -> dict[str, Any]:
    if isinstance(resource, OmniFoldAnalysis):
        result = resource.histogram(
            observable, nominal_variation=variation, bins=bins
        )
    else:
        result = resource.histogram(observable, variation=variation, bins=bins)
    payload: dict[str, Any] = to_jsonable(result.to_dict())
    return payload


def _replica_envelope(
    resource: OmniFoldAnalysis,
    observable: str,
    bins: list[float] | int | None,
) -> dict[str, Any]:
    result = resource.histogram(observable, bins=bins)
    payload: dict[str, Any] = to_jsonable(result.to_dict())
    return payload


def _comparison(
    resource: OmniFoldAnalysis,
    observable: str,
    bins: list[float] | int | None,
) -> dict[str, Any]:
    # go through the package's own JSON serializer for byte-fidelity
    comparison = resource.compare(observable, bins=bins)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "comparison.json"
        comparison.export_json(out)
        raw = json.loads(out.read_text(encoding="utf-8"))
    payload: dict[str, Any] = to_jsonable(raw)
    return payload


def _uncertainty_breakdown(
    resource: OmniFoldPackage | OmniFoldAnalysis,
    observable: str,
    bins: list[float] | int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = to_jsonable(
        resource.uncertainty_breakdown(observable, bins=bins)
    )
    return payload


def _covariance_matrix(
    resource: OmniFoldPackage | OmniFoldAnalysis,
    observable: str,
    bins: list[float] | int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = to_jsonable(
        resource.covariance_matrix(observable, bins=bins)
    )
    return payload


def compute(
    registry: Registry,
    cache: ResultCache,
    resource_id: str,
    operation: str,
    observable: str,
    bins: list[float] | int | None = None,
    variation: str = "nominal",
    tier: CacheTier = CacheTier.IMMUTABLE,
) -> ComputeOutput:
    """Dispatch one operation to one package call, with caching."""

    try:
        ref = registry.get(resource_id)
    except ResourceNotFound as exc:
        raise ComputeError(
            f"unknown resource {resource_id!r}", code="not_found"
        ) from exc

    if operation in _ANALYSIS_ONLY and ref.kind != "analysis":
        raise ComputeError(
            f"operation {operation!r} requires an analysis; "
            f"{resource_id!r} is a {ref.kind}",
            code="conflict",
        )

    checksum = registry.content_checksum(resource_id)
    extra = {"variation": variation} if operation == "histogram" else {}
    key = make_cache_key(
        operation,
        checksum,
        observable,
        bins,
        variation if operation == "histogram" else None,
        extra=extra,
    )
    hit = cache.get(tier, key)
    if hit is not None:
        return ComputeOutput(
            data=hit, resolved_bins=_resolved_bins_from(hit), cached=True
        )

    resource = registry.load(resource_id)
    try:
        data = _dispatch(resource, operation, observable, variation, bins)
    except PackageReadError as exc:
        raise ComputeError(str(exc), code="bad_request") from exc

    cache.set(tier, key, data)
    return ComputeOutput(
        data=data, resolved_bins=_resolved_bins_from(data), cached=False
    )


def _dispatch(
    resource: OmniFoldPackage | OmniFoldAnalysis,
    operation: str,
    observable: str,
    variation: str,
    bins: list[float] | int | None,
) -> dict[str, Any]:
    if operation == "histogram":
        return _histogram(resource, observable, variation, bins)
    if operation == "replica_envelope":
        assert isinstance(resource, OmniFoldAnalysis)
        return _replica_envelope(resource, observable, bins)
    if operation == "comparison":
        assert isinstance(resource, OmniFoldAnalysis)
        return _comparison(resource, observable, bins)
    if operation == "uncertainty_breakdown":
        return _uncertainty_breakdown(resource, observable, bins)
    if operation == "covariance_matrix":
        return _covariance_matrix(resource, observable, bins)
    raise ComputeError(f"unknown operation {operation!r}", code="bad_request")


def build_provenance(
    registry: Registry,
    resource_id: str,
    operation: str,
    parameters: dict[str, Any],
    resolved_bins: list[float] | None,
    cached: bool,
) -> dict[str, Any]:
    summary = registry.summary(resource_id)
    return {
        "resource_id": resource_id,
        "kind": summary["kind"],
        "source": summary["source"],
        "checksum_sha256": summary["checksum_sha256"],
        "checksum_kind": summary["checksum_kind"],
        "operation": operation,
        "parameters": parameters,
        "resolved_bins": resolved_bins,
        "format_version": summary["format_version"],
        "api_version": API_VERSION,
        "package_version": _package_version(),
        "computed_at": datetime.now(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "cached": cached,
    }
