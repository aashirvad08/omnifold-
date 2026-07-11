"""Read the OmniFold publication package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .exceptions import PackageReadError, UnsupportedFormatVersion
from .histogram import HistogramResult, compute_weighted_histogram


SUPPORTED_FORMAT_VERSIONS = {"0.1", "0.2"}
DEFAULT_HDF_KEY = "df"


def _resolve_metadata_path(path: str | Path) -> Path:
    path = Path(path)
    return path / "metadata.yaml" if path.is_dir() else path


def _column_from_spec(spec: Any) -> str:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict) and isinstance(spec.get("column"), str):
        return spec["column"]
    raise PackageReadError(f"Weight specification does not define a column: {spec!r}")


def ensure_supported_format_version(metadata: dict[str, Any]) -> None:
    """Raise a clear error when package metadata uses an unsupported version."""

    version = metadata.get("format_version")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_FORMAT_VERSIONS))
        raise UnsupportedFormatVersion(
            f"Unsupported format_version {version!r}; supported versions: {supported}."
        )


def load_metadata(path: str | Path, enforce_version: bool = False) -> dict[str, Any]:
    """Load package metadata from a package directory or metadata file path."""

    metadata_path = _resolve_metadata_path(path)
    with metadata_path.open("r", encoding="utf-8") as stream:
        metadata = yaml.safe_load(stream) or {}
    if not isinstance(metadata, dict):
        raise PackageReadError("Package metadata must be a mapping.")
    if enforce_version:
        ensure_supported_format_version(metadata)
    return metadata


def _resolve_data_path(base_path: Path, data_path: str) -> Path:
    data = Path(data_path)
    if data.is_absolute():
        return data
    return base_path / data


def load_events(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Load event data declared by metadata or directly from an event file."""

    path = Path(path)
    if path.is_dir():
        metadata = load_metadata(path)
        files = metadata.get("files", {})
        nominal_file = files.get("nominal", {}) if isinstance(files, dict) else {}
        if isinstance(nominal_file, dict) and "path" in nominal_file:
            events_path = _resolve_data_path(path, nominal_file["path"])
            return pd.read_hdf(events_path, key=DEFAULT_HDF_KEY, columns=columns)

        publication = metadata.get("publication")
        if not isinstance(publication, dict) or "events_file" not in publication:
            raise PackageReadError(
                "Metadata must define either files.nominal.path or "
                "publication.events_file."
            )
        events_file = publication["events_file"]
        events_path = path / events_file
        return pd.read_parquet(events_path, columns=columns)
    else:
        events_path = path
        if events_path.suffix in {".h5", ".hdf5"}:
            return pd.read_hdf(events_path, key=DEFAULT_HDF_KEY, columns=columns)
        return pd.read_parquet(events_path, columns=columns)


def list_systematics(metadata: dict[str, Any]) -> list[str]:
    """Return systematic variation names declared in package metadata."""

    file_block = metadata.get("files", {})
    files = file_block.get("systematics", []) if isinstance(file_block, dict) else []
    if isinstance(files, list) and files:
        return sorted(
            Path(systematic["path"]).stem
            for systematic in files
            if isinstance(systematic, dict) and "path" in systematic
        )

    systematics = metadata.get("systematics", {})
    if isinstance(systematics, dict) and systematics:
        return sorted(systematics)

    weights = metadata.get("weights", {})
    if isinstance(weights, dict) and "replica" in weights:
        return ["replica"]
    return []


def resolve_weight_column(
    metadata: dict[str, Any],
    variation: str = "nominal",
    iteration: int | None = None,
    step: str | None = None,
) -> str:
    """Resolve a metadata-declared weight selection to a concrete column name."""

    weights = metadata.get("weights", {})
    if not isinstance(weights, dict):
        raise PackageReadError("Metadata key 'weights' must be a mapping.")

    if iteration is not None or step is not None:
        if iteration is None or step is None:
            raise PackageReadError(
                "Both iteration and step are required for iteration weights."
            )
        if step not in {"step1", "step2"}:
            raise PackageReadError("Iteration step must be 'step1' or 'step2'.")
        for entry in weights.get("iterations", []):
            if entry.get("iteration") == iteration and step in entry:
                return _column_from_spec(entry[step])
        raise PackageReadError(
            f"No weight column declared for iteration={iteration}, step={step!r}."
        )

    if variation == "nominal":
        if "nominal" not in weights:
            raise PackageReadError("Metadata does not declare a nominal weight.")
        return _column_from_spec(weights["nominal"])

    if variation in weights and isinstance(weights[variation], str):
        return weights[variation]

    raise PackageReadError(f"Unknown metadata-declared weight variation: {variation}")


def get_weights(
    df: pd.DataFrame,
    metadata: dict[str, Any],
    variation: str = "nominal",
    iteration: int | None = None,
    step: str | None = None,
):
    """Return the requested weight array from the loaded event table."""

    column = resolve_weight_column(
        metadata,
        variation=variation,
        iteration=iteration,
        step=step,
    )
    if column not in df.columns:
        raise PackageReadError(
            f"Weight column {column!r} is not present in the event table."
        )
    return df[column].to_numpy()


def get_uncertainty(
    df: pd.DataFrame,
    metadata: dict[str, Any],
    variation: str,
) -> np.ndarray:
    """Return per-event absolute difference between a variation and nominal weights."""

    nominal = get_weights(df, metadata, variation="nominal")
    varied = get_weights(df, metadata, variation=variation)
    return np.abs(varied - nominal)


class OmniFoldPackage:
    """Thin wrapper matching the proposal-facing package API."""

    def __init__(self, package_dir: str | Path):
        self.package_dir = Path(package_dir)
        self._metadata = load_metadata(self.package_dir, enforce_version=True)

    def load_events(self, columns: list[str] | None = None) -> pd.DataFrame:
        """Load event columns from this package."""

        return load_events(self.package_dir, columns=columns)

    def list_systematics(self) -> list[str]:
        """Return systematic names declared by this package."""

        return list_systematics(self._metadata)

    def list_weights(self) -> list[str]:
        """Return all declared weight variation names."""

        weights = self._metadata.get("weights", {})
        if not isinstance(weights, dict):
            return []
        return [key for key in weights if key != "iterations"]

    def list_observables(self) -> list[str]:
        """Return all declared observable names."""

        observables = self._metadata.get("observables", [])
        if not isinstance(observables, list):
            return []
        return [
            observable["name"]
            for observable in observables
            if isinstance(observable, dict) and "name" in observable
        ]

    def observable_units(self, name: str) -> str:
        """Return the declared units for an observable, or an empty string."""

        for observable in self._metadata.get("observables", []):
            if isinstance(observable, dict) and observable.get("name") == name:
                units = observable.get("units", "")
                return units if isinstance(units, str) else ""
        raise PackageReadError(f"Unknown observable: {name}")

    def observable_bins(self, name: str) -> list[float] | None:
        """Return declared or suggested bin edges for an observable."""

        for observable in self._metadata.get("observables", []):
            if isinstance(observable, dict) and observable.get("name") == name:
                bins = observable.get("bins", observable.get("suggested_bins"))
                if bins is None:
                    return None
                if not isinstance(bins, list):
                    raise PackageReadError(
                        f"Bins for observable {name!r} must be a list."
                    )
                return [float(edge) for edge in bins]
        raise PackageReadError(f"Unknown observable: {name}")

    def summary(self) -> dict[str, Any]:
        """Return a concise summary of the package contents."""

        publication = self._metadata.get("publication", {})
        return {
            "format_version": self._metadata.get("format_version"),
            "event_count": publication.get("event_count")
            if isinstance(publication, dict)
            else None,
            "observables": self.list_observables(),
            "weights": self.list_weights(),
            "systematics": self.list_systematics(),
            "checksum_sha256": publication.get("checksum_sha256", "not recorded")
            if isinstance(publication, dict)
            else "not recorded",
        }

    def get_weights(
        self,
        kind: str = "nominal",
        variation: str | None = None,
        iteration: int | None = None,
        step: str | None = None,
    ) -> np.ndarray:
        """Return a declared weight array or the derived final event weights."""

        selection = variation or kind
        if selection == "final" and iteration is None and step is None:
            weights = self._metadata.get("weights", {})
            if not isinstance(weights, dict):
                raise PackageReadError("Metadata key 'weights' must be a mapping.")

            missing = [
                key for key in ("base_mc_weight", "nominal") if key not in weights
            ]
            if missing:
                missing_names = ", ".join(missing)
                raise PackageReadError(
                    "Cannot compute final weights; metadata is missing: "
                    f"{missing_names}."
                )

            base_column = _column_from_spec(weights["base_mc_weight"])
            nominal_column = _column_from_spec(weights["nominal"])
            try:
                df = self.load_events(columns=[base_column, nominal_column])
            except Exception as exc:
                raise PackageReadError(
                    "Cannot compute final weights; required columns "
                    f"{base_column!r} and {nominal_column!r} must be present."
                ) from exc

            missing_columns = [
                column
                for column in (base_column, nominal_column)
                if column not in df.columns
            ]
            if missing_columns:
                raise PackageReadError(
                    "Cannot compute final weights; missing event columns: "
                    f"{', '.join(missing_columns)}."
                )
            return (
                df[base_column].to_numpy(dtype=float)
                * df[nominal_column].to_numpy(dtype=float)
            )

        column = resolve_weight_column(
            self._metadata,
            variation=selection,
            iteration=iteration,
            step=step,
        )
        df = self.load_events(columns=[column])
        return get_weights(
            df,
            self._metadata,
            variation=selection,
            iteration=iteration,
            step=step,
        )

    def histogram(
        self,
        observable: str,
        variation: str = "nominal",
        bins: list[float] | int | None = None,
    ) -> HistogramResult:
        """Compute a weighted histogram for one observable and variation."""

        selected_bins = bins
        if selected_bins is None:
            selected_bins = self.observable_bins(observable) or 30
        events = self.load_events(columns=[observable])
        weights = self.get_weights(variation)
        result = compute_weighted_histogram(
            events[observable].to_numpy(dtype=float),
            weights,
            bins=selected_bins,
        )
        return HistogramResult(
            hist=np.asarray(result["hist"], dtype=float),
            edges=np.asarray(result["edges"], dtype=float),
            centers=np.asarray(result["centers"], dtype=float),
            stat_uncertainty=np.asarray(result["uncertainty"], dtype=float),
        )

    def get_uncertainty(self, variation: str) -> np.ndarray:
        """Return per-event absolute differences from nominal weights."""

        nominal_column = resolve_weight_column(self._metadata, variation="nominal")
        variation_column = resolve_weight_column(self._metadata, variation=variation)
        columns = list(dict.fromkeys([nominal_column, variation_column]))
        df = self.load_events(columns=columns)
        return get_uncertainty(df, self._metadata, variation=variation)

    def metadata(self) -> dict[str, Any]:
        """Return this package's loaded metadata mapping."""

        return self._metadata

    def validate(self) -> None:
        """Validate package metadata, contents, and integrity."""

        from .validation import ensure_valid_package

        ensure_valid_package(self.package_dir)


def load_package(package_dir: str | Path) -> OmniFoldPackage:
    """Load a publication package and return the proposal-style wrapper."""

    return OmniFoldPackage(package_dir)
