"""Computation routes: histogram, comparison, replica-envelope,
uncertainty-breakdown, covariance-matrix.

Each is registered under ``/resources/{id}``, ``/analyses/{id}`` and
``/packages/{id}`` (URL sugar only — the registry determines the real
kind, so an alias never lets an operation run on the wrong resource type).
Every handler delegates to :func:`compute`, which makes exactly one
package call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_cache, get_registry
from ..models.schemas import ComputedResponse, Provenance
from ..services.cache import ResultCache
from ..services.compute import ComputeError, build_provenance, compute
from ..services.registry import Registry

router = APIRouter(tags=["computation"])

_PREFIXES = ("/resources", "/analyses", "/packages")
_CODE_TO_STATUS = {"not_found": 404, "bad_request": 400, "conflict": 409}


def _resolve_bins(
    bins: list[float] | None, nbins: int | None
) -> list[float] | int | None:
    if bins and nbins is not None:
        raise HTTPException(
            400, "pass either bins (explicit edges) or nbins, not both"
        )
    if bins:
        if len(bins) < 2:
            raise HTTPException(400, "bins needs at least two edges")
        return [float(edge) for edge in bins]
    if nbins is not None:
        if nbins < 1:
            raise HTTPException(400, "nbins must be >= 1")
        return int(nbins)
    return None


def _run(
    registry: Registry,
    cache: ResultCache,
    resource_id: str,
    operation: str,
    observable: str,
    resolved_bins: list[float] | int | None,
    variation: str,
    parameters: dict[str, Any],
) -> ComputedResponse:
    try:
        out = compute(
            registry,
            cache,
            resource_id,
            operation,
            observable,
            bins=resolved_bins,
            variation=variation,
        )
    except ComputeError as exc:
        raise HTTPException(
            _CODE_TO_STATUS.get(exc.code, 400), str(exc)
        ) from exc
    provenance = build_provenance(
        registry, resource_id, operation, parameters, out.resolved_bins,
        out.cached,
    )
    return ComputedResponse(
        data=out.data, provenance=Provenance(**provenance)
    )


def _histogram_handler(
    resource_id: str,
    observable: str = Query(...),
    bins: list[float] | None = Query(None),
    nbins: int | None = Query(None),
    variation: str = Query("nominal"),
    registry: Registry = Depends(get_registry),
    cache: ResultCache = Depends(get_cache),
) -> ComputedResponse:
    resolved = _resolve_bins(bins, nbins)
    params = {"observable": observable, "bins": bins, "nbins": nbins,
              "variation": variation}
    return _run(registry, cache, resource_id, "histogram", observable,
                resolved, variation, params)


def _make_no_variation_handler(
    operation: str,
) -> Callable[..., ComputedResponse]:
    def handler(
        resource_id: str,
        observable: str = Query(...),
        bins: list[float] | None = Query(None),
        nbins: int | None = Query(None),
        registry: Registry = Depends(get_registry),
        cache: ResultCache = Depends(get_cache),
    ) -> ComputedResponse:
        resolved = _resolve_bins(bins, nbins)
        params = {"observable": observable, "bins": bins, "nbins": nbins}
        return _run(registry, cache, resource_id, operation, observable,
                    resolved, "nominal", params)

    return handler


_OPERATIONS = {
    "histogram": _histogram_handler,
    "comparison": _make_no_variation_handler("comparison"),
    "replica-envelope": _make_no_variation_handler("replica_envelope"),
    "uncertainty-breakdown": _make_no_variation_handler("uncertainty_breakdown"),
    "covariance-matrix": _make_no_variation_handler("covariance_matrix"),
}

for _path_suffix, _handler in _OPERATIONS.items():
    for _prefix in _PREFIXES:
        router.add_api_route(
            f"{_prefix}/{{resource_id}}/{_path_suffix}",
            _handler,
            methods=["GET"],
            response_model=ComputedResponse,
            name=f"{_path_suffix.replace('-', '_')}_{_prefix.strip('/')}",
        )
