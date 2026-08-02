"""Export routes: HEPData submission bundle and package download.

Both are registered under ``/resources``, ``/analyses`` and ``/packages``.
Responses stream the produced tar from a temp directory that is removed by
a background task once the response is sent.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from omnifold_publication import OmniFoldAnalysis
from starlette.background import BackgroundTask

from ..dependencies import get_registry
from ..services.export import hepdata_submission_tar, package_download_tar
from ..services.registry import (
    PathSafetyError,
    Registry,
    ResourceNotFound,
    ResourceRef,
)

router = APIRouter(tags=["export"])

_PREFIXES = ("/resources", "/analyses", "/packages")


def _resolve(registry: Registry, resource_id: str) -> ResourceRef:
    try:
        return registry.get(resource_id)
    except ResourceNotFound as exc:
        raise HTTPException(404, f"unknown resource {resource_id!r}") from exc
    except PathSafetyError as exc:  # pragma: no cover - defensive
        raise HTTPException(404, "resource unavailable") from exc


def _streamed(path: Path, filename: str, work_dir: Path) -> FileResponse:
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=filename,
        background=BackgroundTask(shutil.rmtree, work_dir, ignore_errors=True),
    )


def _hepdata(resource_id: str, registry: Registry) -> FileResponse:
    ref = _resolve(registry, resource_id)
    loaded = registry.load(resource_id)
    package = (
        loaded.nominal_package
        if isinstance(loaded, OmniFoldAnalysis)
        else loaded
    )
    work_dir = Path(tempfile.mkdtemp(prefix="omnifold_hepdata_"))
    try:
        tar = hepdata_submission_tar(package, work_dir, ref.id)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    return _streamed(tar, f"{ref.id}_hepdata.tar.gz", work_dir)


def _download(resource_id: str, registry: Registry) -> FileResponse:
    ref = _resolve(registry, resource_id)
    work_dir = Path(tempfile.mkdtemp(prefix="omnifold_download_"))
    dest = work_dir / f"{ref.id}.tar.gz"
    try:
        package_download_tar(ref.path, dest)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    return _streamed(dest, f"{ref.id}.tar.gz", work_dir)


def _make(
    handler: Callable[[str, Registry], FileResponse],
) -> Callable[..., FileResponse]:
    def route(
        resource_id: str, registry: Registry = Depends(get_registry)
    ) -> FileResponse:
        return handler(resource_id, registry)

    return route


for _prefix in _PREFIXES:
    router.add_api_route(
        f"{_prefix}/{{resource_id}}/hepdata-yaml",
        _make(_hepdata),
        methods=["GET"],
        name=f"hepdata_yaml_{_prefix.strip('/')}",
    )
    router.add_api_route(
        f"{_prefix}/{{resource_id}}/download-package",
        _make(_download),
        methods=["GET"],
        name=f"download_package_{_prefix.strip('/')}",
    )
