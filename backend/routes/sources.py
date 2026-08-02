"""Data-source discovery and egress policy.

Reports which source kinds a client may open. ``url`` and ``s3`` are
deny-by-default (SSRF); ``zenodo`` is allowed but host-pinned to its fixed
API host. The runtime enforcement point is
:func:`backend.services.auth.ensure_source_allowed`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..config import Settings
from ..dependencies import get_settings

router = APIRouter(tags=["sources"])

_ALL_KINDS = ("local", "upload", "zenodo", "s3", "url")
_NETWORK_KINDS = {"zenodo", "s3", "url"}
_NOTES = {
    "zenodo": "host-pinned to zenodo.org",
    "url": "deny-by-default (SSRF); enable via allowed_source_kinds + egress_allowlist",
    "s3": "deny-by-default (SSRF); enable via allowed_source_kinds + egress_allowlist",
}


@router.get("/sources")
def list_sources(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    kinds = [
        {
            "kind": kind,
            "allowed": kind in settings.allowed_source_kinds,
            "network": kind in _NETWORK_KINDS,
            "note": _NOTES.get(kind),
        }
        for kind in _ALL_KINDS
    ]
    return {"kinds": kinds, "egress_allowlist": list(settings.egress_allowlist)}
