"""Multi-sample analysis wrapper for OmniFold publication packages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .exceptions import PackageReadError, PackageValidationError
from .histogram import HistogramResult, compute_weighted_histogram
from .manifest import list_manifest_variations, load_manifest
from .reader import OmniFoldPackage, load_package


class OmniFoldAnalysis:
    """Multi-sample analysis wrapper backed by a manifest.yaml."""

    def __init__(self, manifest_dir: str | Path):
        """Load manifest and initialize all package references."""

        self.manifest_dir = Path(manifest_dir)
        self.manifest = load_manifest(self.manifest_dir)
        self.analysis_name = self.manifest.get("analysis", "unnamed")
        self._packages: dict[str, OmniFoldPackage] = {}
        self._load_packages()

    def _resolve_package_path(self, package_path: str | Path) -> Path:
        path = Path(package_path)
        if path.is_absolute():
            return path
        return self.manifest_dir / path

    def _load_packages(self) -> None:
        samples = self.manifest.get("samples", {})
        if not isinstance(samples, dict) or "nominal" not in samples:
            raise PackageReadError("Manifest must declare samples.nominal.")

        for name, sample in samples.items():
            if not isinstance(sample, dict) or "path" not in sample:
                raise PackageReadError(f"Manifest sample {name!r} must define path.")
            self._packages[name] = load_package(self._resolve_package_path(sample["path"]))

    @property
    def nominal_package(self) -> OmniFoldPackage:
        """Return the nominal package."""

        return self._packages["nominal"]

    def load_events(
        self,
        columns: list[str] | None = None,
        variation: str = "nominal",
    ) -> pd.DataFrame:
        """Load events from the appropriate package for a variation."""

        package = self._package_for_variation(variation)
        return package.load_events(columns=columns)

    def get_weights(self, variation: str = "nominal") -> np.ndarray:
        """Get weights for any declared variation."""

        if variation in {"nominal", "final"}:
            return self.nominal_package.get_weights(variation)

        if variation in self._packages:
            return self._packages[variation].get_weights("nominal")

        nominal_weights = self.nominal_package.list_weights()
        if variation in nominal_weights:
            return self.nominal_package.get_weights(variation)

        column_name = f"weights_{variation}"
        if column_name in nominal_weights:
            return self.nominal_package.get_weights(column_name)

        raise PackageReadError(f"Unknown analysis variation: {variation}")

    def list_variations(self) -> list[str]:
        """List all available variations."""

        variations = ["nominal"]
        variations.extend(list_manifest_variations(self.manifest))
        variations.extend(
            weight
            for weight in self.nominal_package.list_weights()
            if "ensemble" in weight and weight not in variations
        )
        return variations

    def get_replica_weights(self) -> np.ndarray:
        """Return all ensemble replica weights as a 2D array."""

        replica_names = [
            weight
            for weight in self.nominal_package.list_weights()
            if "ensemble" in weight
        ]
        if not replica_names:
            return np.empty((0, 0))
        return np.vstack(
            [self.nominal_package.get_weights(replica) for replica in replica_names]
        )

    def validate_all(self) -> None:
        """Validate all packages declared in manifest."""

        errors: list[str] = []
        for name, package in self._packages.items():
            try:
                package.validate()
            except PackageValidationError as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            raise PackageValidationError("\n".join(errors))

    def summary(self) -> dict[str, Any]:
        """Return summary of the full analysis."""

        return {
            "analysis": self.analysis_name,
            "nominal": self.nominal_package.summary(),
            "variations": self.list_variations(),
            "replica_count": len(
                [
                    weight
                    for weight in self.nominal_package.list_weights()
                    if "ensemble" in weight
                ]
            ),
        }

    def histogram(
        self,
        observable: str,
        nominal_variation: str = "nominal",
        systematic_variations: list[str] | None = None,
        bins: list[float] | int | None = None,
    ) -> HistogramResult:
        """Compute a nominal histogram with statistical and uncertainty bands."""

        selected_bins = bins
        if selected_bins is None:
            selected_bins = self.nominal_package.observable_bins(observable) or 30
        nominal_events = self.load_events(
            columns=[observable],
            variation=nominal_variation,
        )
        nominal_weights = self.get_weights(nominal_variation)
        nominal_result = compute_weighted_histogram(
            nominal_events[observable].to_numpy(dtype=float),
            nominal_weights,
            bins=selected_bins,
        )
        common_edges = np.asarray(nominal_result["edges"], dtype=float)

        systematic_differences: list[np.ndarray] = []
        for variation in systematic_variations or []:
            varied_events = self.load_events(
                columns=[observable],
                variation=variation,
            )
            varied_result = compute_weighted_histogram(
                varied_events[observable].to_numpy(dtype=float),
                self.get_weights(variation),
                bins=common_edges,
            )
            systematic_differences.append(
                np.abs(
                    np.asarray(varied_result["hist"], dtype=float)
                    - np.asarray(nominal_result["hist"], dtype=float)
                )
            )
        sys_uncertainty = (
            np.max(np.vstack(systematic_differences), axis=0)
            if systematic_differences
            else None
        )

        replica_uncertainty = None
        replicas = self.get_replica_weights()
        nominal_values = nominal_events[observable].to_numpy(dtype=float)
        if replicas.size and replicas.shape[1] == nominal_values.shape[0]:
            replica_histograms = [
                np.asarray(
                    compute_weighted_histogram(
                        nominal_values,
                        replica,
                        bins=common_edges,
                    )["hist"],
                    dtype=float,
                )
                for replica in replicas
            ]
            replica_uncertainty = np.std(np.vstack(replica_histograms), axis=0)

        return HistogramResult(
            hist=np.asarray(nominal_result["hist"], dtype=float),
            edges=common_edges,
            centers=np.asarray(nominal_result["centers"], dtype=float),
            stat_uncertainty=np.asarray(
                nominal_result["uncertainty"],
                dtype=float,
            ),
            sys_uncertainty=sys_uncertainty,
            replica_uncertainty=replica_uncertainty,
        )

    def compare(
        self,
        observable: str,
        bins: list[float] | None = None,
    ):
        """Return a comparison object for the given observable."""

        from .comparison import HistogramComparison

        if bins is None:
            events = self.load_events(columns=[observable])
            bins = np.histogram_bin_edges(
                events[observable].to_numpy(dtype=float),
                bins=30,
            ).tolist()
        return HistogramComparison(self, observable=observable, bins=bins)

    def _package_for_variation(self, variation: str) -> OmniFoldPackage:
        if variation in {"nominal", "final"}:
            return self.nominal_package
        if variation in self._packages:
            return self._packages[variation]
        if variation in self.nominal_package.list_weights():
            return self.nominal_package
        if f"weights_{variation}" in self.nominal_package.list_weights():
            return self.nominal_package
        raise PackageReadError(f"Unknown analysis variation: {variation}")


def load_analysis(manifest_dir: str | Path) -> OmniFoldAnalysis:
    """Load a multi-sample OmniFold analysis from a manifest directory."""

    return OmniFoldAnalysis(manifest_dir)
