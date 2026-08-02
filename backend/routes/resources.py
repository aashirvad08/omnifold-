"""Discovery and metadata routes.

Resources are unified (a ``kind`` discriminates package vs analysis); the
``/analyses/{id}`` and ``/packages/{id}`` aliases are kept so existing
clients keep working, all backed by the one registry.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from omnifold_publication import load_metadata

from ..dependencies import get_registry
from ..models.schemas import (
    CitationResponse,
    ResourceKind,
    ResourceListResponse,
    ResourceSummary,
)
from ..services.citation import build_citation
from ..services.registry import (
    PathSafetyError,
    Registry,
    ResourceNotFound,
    _nominal_metadata_path,
)

router = APIRouter(tags=["discovery"])


def _summaries(
    registry: Registry, only: ResourceKind | None
) -> list[ResourceSummary]:
    out: list[ResourceSummary] = []
    for ref in registry.list():
        if only is not None and ref.kind != only:
            continue
        out.append(ResourceSummary(**registry.summary(ref.id)))
    return out


@router.get("/resources", response_model=ResourceListResponse)
def list_resources(
    kind: Literal["package", "analysis"] | None = None,
    registry: Registry = Depends(get_registry),
) -> ResourceListResponse:
    return ResourceListResponse(resources=_summaries(registry, kind))


@router.get("/analyses", response_model=ResourceListResponse)
def list_analyses(
    registry: Registry = Depends(get_registry),
) -> ResourceListResponse:
    return ResourceListResponse(resources=_summaries(registry, "analysis"))


@router.get("/packages", response_model=ResourceListResponse)
def list_packages(
    registry: Registry = Depends(get_registry),
) -> ResourceListResponse:
    return ResourceListResponse(resources=_summaries(registry, "package"))


def _resolve(registry: Registry, resource_id: str) -> None:
    try:
        registry.get(resource_id)
    except ResourceNotFound as exc:
        raise HTTPException(404, f"unknown resource {resource_id!r}") from exc
    except PathSafetyError as exc:  # pragma: no cover - defensive
        raise HTTPException(404, "resource unavailable") from exc


def _metadata_response(registry: Registry, resource_id: str) -> dict[str, Any]:
    _resolve(registry, resource_id)
    ref = registry.get(resource_id)
    if ref.kind == "package":
        return dict(load_metadata(ref.path))
    return dict(load_metadata(_nominal_metadata_path(ref.path)))


@router.get("/resources/{resource_id}/metadata")
def resource_metadata(
    resource_id: str, registry: Registry = Depends(get_registry)
) -> dict[str, Any]:
    return _metadata_response(registry, resource_id)


@router.get("/analyses/{resource_id}/metadata")
def analysis_metadata(
    resource_id: str, registry: Registry = Depends(get_registry)
) -> dict[str, Any]:
    return _metadata_response(registry, resource_id)


@router.get("/packages/{resource_id}/metadata")
def package_metadata(
    resource_id: str, registry: Registry = Depends(get_registry)
) -> dict[str, Any]:
    return _metadata_response(registry, resource_id)


def _citation_response(
    registry: Registry, resource_id: str
) -> CitationResponse:
    _resolve(registry, resource_id)
    ref = registry.get(resource_id)
    metadata = (
        load_metadata(ref.path)
        if ref.kind == "package"
        else load_metadata(_nominal_metadata_path(ref.path))
    )
    payload = build_citation(
        resource_id,
        dict(metadata),
        registry.content_checksum(resource_id),
        ref.source,
        registry.checksum_kind(resource_id),
    )
    return CitationResponse(**payload)


@router.get("/resources/{resource_id}/citation", response_model=CitationResponse)
def resource_citation(
    resource_id: str, registry: Registry = Depends(get_registry)
) -> CitationResponse:
    return _citation_response(registry, resource_id)


@router.get("/analyses/{resource_id}/citation", response_model=CitationResponse)
def analysis_citation(
    resource_id: str, registry: Registry = Depends(get_registry)
) -> CitationResponse:
    return _citation_response(registry, resource_id)


@router.get("/packages/{resource_id}/citation", response_model=CitationResponse)
def package_citation(
    resource_id: str, registry: Registry = Depends(get_registry)
) -> CitationResponse:
    return _citation_response(registry, resource_id)
