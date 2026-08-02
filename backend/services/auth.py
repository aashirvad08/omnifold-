"""API-key authentication and source-egress policy.

Auth is optional in dev and required in prod (``require_api_key``). Keys
are compared in constant time so a timing side-channel cannot leak them.

Egress policy gates which source kinds a client may cause the backend to
open. ``local`` and ``upload`` are safe; ``zenodo`` reaches a single fixed,
host-pinned API; ``url`` and ``s3`` can reach arbitrary hosts and are
denied unless explicitly enabled *and* the host is on the allowlist —
deny-by-default against SSRF.
"""

from __future__ import annotations

import hmac
from typing import Protocol
from urllib.parse import urlparse


class EgressSettings(Protocol):
    allowed_source_kinds: tuple[str, ...]
    egress_allowlist: tuple[str, ...]


def verify_api_key(provided: str | None, keys: tuple[str, ...]) -> bool:
    """Constant-time membership test of ``provided`` against ``keys``."""

    if provided is None:
        return False
    matched = False
    for key in keys:
        # compare every key (no short-circuit) so timing does not reveal
        # which key, if any, matched
        if hmac.compare_digest(provided, key):
            matched = True
    return matched


class EgressDenied(Exception):
    """A source spec was rejected by the egress policy."""


_NETWORK_KINDS = {"zenodo", "url", "s3"}
_ZENODO_HOST = "zenodo.org"


def parse_source_kind(spec: str) -> str:
    prefix, sep, _ = spec.partition(":")
    if sep and prefix in {"local", "upload", "zenodo", "s3", "url"}:
        return prefix
    return "local"  # a bare path


def _source_host(spec: str, kind: str) -> str | None:
    if kind == "zenodo":
        return _ZENODO_HOST  # zenodo always hits its own fixed API host
    if kind == "url":
        return urlparse(spec.partition(":")[2]).hostname
    if kind == "s3":
        # s3://bucket/key -> the bucket is the effective host
        return urlparse(spec).hostname
    return None


def ensure_source_allowed(spec: str, settings: EgressSettings) -> None:
    """Raise :class:`EgressDenied` unless the spec's kind is permitted and,
    for network kinds, its host is on the allowlist."""

    kind = parse_source_kind(spec)
    if kind not in settings.allowed_source_kinds:
        raise EgressDenied(
            f"source kind {kind!r} is disabled (deny-by-default); enable it "
            "in allowed_source_kinds to use it"
        )
    if kind in _NETWORK_KINDS:
        host = _source_host(spec, kind)
        if host is None or host not in settings.egress_allowlist:
            raise EgressDenied(
                f"host {host!r} for source kind {kind!r} is not on the "
                "egress allowlist"
            )
