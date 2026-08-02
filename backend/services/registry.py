"""Resource registry — the single path-resolution chokepoint.

Every disk path served to a client is resolved here and checked to live
under ``data_root``; no route ever accepts a raw filesystem path. Resources
are discovered by scanning ``data_root`` one level deep:

- a directory with ``manifest.yaml`` is an **analysis** (multi-sample)
- a directory with ``metadata.yaml`` is a **package** (single sample)

**IDs are the (sanitised) directory name, not an opaque hash.** This is a
deliberate choice, matching the sibling publication project: provenance
maps trivially back to disk and shared explorer URLs are self-documenting.
It is safe because safety comes from this registry's symlink-resolution
plus containment check — not from id opacity — and because every computed
response also carries the content SHA-256 as its integrity anchor. The
trade-off (ids change if a directory is renamed, and the leaf name is
visible) is acceptable for a public data explorer whose purpose is to
expose named datasets.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from omnifold_publication import (
    OmniFoldAnalysis,
    OmniFoldPackage,
    load_analysis,
    load_metadata,
    load_package,
)

ResourceKind = Literal["package", "analysis"]

# Directory names allowed as ids: alphanumeric start, then word/.-/ chars.
# The containment check is the real guard; this just keeps ids sane.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ResourceNotFound(KeyError):
    """No resource with the requested id."""


class PathSafetyError(Exception):
    """A resolved path escaped the configured data root."""


def _is_within(child: Path, root: Path) -> bool:
    try:
        return child.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class ResourceRef:
    id: str
    kind: ResourceKind
    path: Path  # resolved, validated to be within the root at scan time
    source: str  # e.g. "local:zjets_nominal"


class Registry:
    """Discovers resources under a root and resolves ids to safe paths."""

    def __init__(self, data_root: Path):
        self._root = data_root.expanduser().resolve()
        self._by_id: dict[str, ResourceRef] = {}
        self._loaded: dict[str, OmniFoldPackage | OmniFoldAnalysis] = {}
        self._checksums: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self.rescan()

    @property
    def root(self) -> Path:
        return self._root

    def rescan(self) -> None:
        """(Re)discover resources. Safe to call at startup or on demand."""

        discovered: dict[str, ResourceRef] = {}
        if self._root.is_dir():
            for entry in sorted(self._root.iterdir()):
                ref = self._classify(entry)
                if ref is not None:
                    discovered[ref.id] = ref
        with self._lock:
            self._by_id = discovered
            self._loaded.clear()
            self._checksums.clear()

    def _classify(self, entry: Path) -> ResourceRef | None:
        if not entry.is_dir():
            return None
        name = entry.name
        if not _ID_PATTERN.match(name):
            return None
        # reject anything whose real location is outside the root
        # (e.g. a symlinked directory pointing elsewhere)
        if not _is_within(entry, self._root):
            return None
        resolved = entry.resolve()
        if (resolved / "manifest.yaml").is_file():
            kind: ResourceKind = "analysis"
        elif (resolved / "metadata.yaml").is_file():
            kind = "package"
        else:
            return None
        return ResourceRef(
            id=name, kind=kind, path=resolved, source=f"local:{name}"
        )

    def list(self) -> list[ResourceRef]:
        with self._lock:
            return list(self._by_id.values())

    def get(self, resource_id: str) -> ResourceRef:
        """Resolve an id to a validated ResourceRef.

        Re-checks containment at access time so a directory swapped for a
        symlink after discovery cannot be used to escape the root.
        """

        with self._lock:
            ref = self._by_id.get(resource_id)
        if ref is None:
            raise ResourceNotFound(resource_id)
        if not _is_within(ref.path, self._root):
            raise PathSafetyError(
                f"resource {resource_id!r} resolves outside the data root"
            )
        return ref

    def load(
        self, resource_id: str
    ) -> OmniFoldPackage | OmniFoldAnalysis:
        """Load (and memoise) the package/analysis for an id."""

        ref = self.get(resource_id)
        with self._lock:
            cached = self._loaded.get(resource_id)
        if cached is not None:
            return cached
        loaded: OmniFoldPackage | OmniFoldAnalysis = (
            load_analysis(ref.path)
            if ref.kind == "analysis"
            else load_package(ref.path)
        )
        with self._lock:
            self._loaded[resource_id] = loaded
        return loaded

    def checksum_kind(
        self, resource_id: str
    ) -> Literal["package", "analysis_composite"]:
        """Distinguish a package's own file checksum from a backend-derived
        analysis aggregate, so a later citation never conflates the two."""

        ref = self.get(resource_id)
        return "analysis_composite" if ref.kind == "analysis" else "package"

    def content_checksum(self, resource_id: str) -> str | None:
        """Immutable content id.

        For a **package** this is the package's own recorded
        ``publication.checksum_sha256`` — the hash of its event file. For
        an **analysis** there is no single file, so this is a
        *backend-derived* SHA-256 over the members' sorted
        ``name:checksum`` pairs (see :meth:`checksum_kind`). The two are
        both legitimate provenance but must not be conflated: a package
        checksum identifies real bytes on disk; an analysis composite is a
        synthetic aggregate this backend computed.
        """

        with self._lock:
            if resource_id in self._checksums:
                return self._checksums[resource_id]
        ref = self.get(resource_id)
        checksum = (
            self._analysis_checksum(ref)
            if ref.kind == "analysis"
            else _package_checksum(ref.path)
        )
        with self._lock:
            self._checksums[resource_id] = checksum
        return checksum

    def _analysis_checksum(self, ref: ResourceRef) -> str | None:
        import hashlib

        manifest = yaml.safe_load(
            (ref.path / "manifest.yaml").read_text(encoding="utf-8")
        )
        samples = (manifest or {}).get("samples", {})
        parts: list[str] = []
        for name in sorted(samples):
            member = samples[name]
            if not isinstance(member, dict) or "path" not in member:
                continue
            member_path = (ref.path / member["path"]).resolve()
            # analysis members must also live under the root
            if not _is_within(member_path, self._root):
                raise PathSafetyError(
                    f"analysis {ref.id!r} references a path outside the root"
                )
            parts.append(f"{name}:{_package_checksum(member_path)}")
        if not parts:
            return None
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        return digest

    def summary(self, resource_id: str) -> dict[str, Any]:
        ref = self.get(resource_id)
        metadata = load_metadata(
            ref.path
            if ref.kind == "package"
            else _nominal_metadata_path(ref.path)
        )
        publication = metadata.get("publication", {})
        observables = [
            obs["name"]
            for obs in metadata.get("observables", [])
            if isinstance(obs, dict) and "name" in obs
        ]
        return {
            "id": ref.id,
            "kind": ref.kind,
            "source": ref.source,
            "checksum_sha256": self.content_checksum(resource_id),
            "checksum_kind": self.checksum_kind(resource_id),
            "format_version": metadata.get("format_version"),
            "event_count": publication.get("event_count")
            if isinstance(publication, dict)
            else None,
            "observables": observables,
        }

    def metadata(self, resource_id: str) -> dict[str, Any]:
        ref = self.get(resource_id)
        if ref.kind == "package":
            package_meta: dict[str, Any] = load_metadata(ref.path)
            return package_meta
        loaded = self.load(resource_id)
        assert isinstance(loaded, OmniFoldAnalysis)
        analysis_meta: dict[str, Any] = loaded.summary()
        return analysis_meta


def _package_checksum(package_path: Path) -> str | None:
    metadata = load_metadata(package_path)
    publication = metadata.get("publication", {})
    if isinstance(publication, dict):
        checksum = publication.get("checksum_sha256")
        return checksum if isinstance(checksum, str) else None
    return None


def _nominal_metadata_path(analysis_path: Path) -> Path:
    manifest = yaml.safe_load(
        (analysis_path / "manifest.yaml").read_text(encoding="utf-8")
    )
    nominal = (manifest or {}).get("samples", {}).get("nominal", {})
    member: Path = (analysis_path / nominal["path"]).resolve()
    return member
