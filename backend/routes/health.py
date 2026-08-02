"""Health, readiness, and version routes."""

from __future__ import annotations

import importlib.metadata

from fastapi import APIRouter, Depends, Request
from prometheus_client import CONTENT_TYPE_LATEST
from starlette.responses import Response

from .. import API_VERSION
from ..config import Settings
from ..dependencies import get_registry, get_settings
from ..models.schemas import HealthResponse, ReadyResponse, VersionResponse
from ..services.registry import Registry

router = APIRouter(tags=["ops"])


@router.get("/metrics")
def metrics(request: Request) -> Response:
    rendered = request.app.state.metrics.render()
    return Response(rendered, media_type=CONTENT_TYPE_LATEST)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse)
def ready(
    registry: Registry = Depends(get_registry),
) -> ReadyResponse:
    data_root_ok = registry.root.is_dir()
    count = len(registry.list())
    return ReadyResponse(
        status="ready" if data_root_ok else "not_ready",
        data_root_ok=data_root_ok,
        resources_discovered=count,
    )


@router.get("/version", response_model=VersionResponse)
def version(
    settings: Settings = Depends(get_settings),
) -> VersionResponse:
    try:
        package_version = importlib.metadata.version("omnifold_publication")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        package_version = "unknown"
    return VersionResponse(
        api_version=API_VERSION,
        package_version=package_version,
        environment=settings.env,
    )
