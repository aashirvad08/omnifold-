"""Compare two *independently published* results against each other.

Unlike :class:`~omnifold_publication.comparison.HistogramComparison`, which
compares variations within a single publication against that publication's
own nominal, this compares two separate packages (different provenance —
different checksums, sources, and possibly unfolding methods) on a shared
observable and shared binning.

Two principles:

- **Each side's uncertainty is computed independently, from its own data.**
  There is no shared nominal across two publications, so each package's
  full uncertainty breakdown (statistical + replica/ensemble + bootstrap +
  systematic families it declares) is computed by that package's own
  machinery and attributed to that side by label. This is the gap the
  cross-publication view fills.
- **Rebinning is an explicit re-histogram of raw events, never
  interpolation.** Both sides are re-filled from their raw event tables
  with the same requested bin edges; nothing is resampled or curve-fit
  after the fact.

The ratio panel compares the two results to each other (side B / side A),
not to a nominal within one of them. By default each side's own relative
uncertainty band is shown *separately* on the ratio — no correlation model
between the two publications is assumed. A combined ratio band would
require knowing whether the two unfold the same underlying events
(correlated) or independent data; that is exposed as an explicit
``correlation`` option rather than silently baked in either way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .exceptions import PackageReadError
from .reader import OmniFoldPackage

# Correlation models for the ratio band. Only "none" is implemented; the
# others are named so the choice is explicit and discoverable, but must not
# be used until the physical relationship between the two results is known.
CORRELATION_MODELS = ("none", "independent", "same_events")


def _describe(package: OmniFoldPackage, fallback_label: str) -> dict[str, Any]:
    metadata = package.metadata()
    dataset = metadata.get("dataset", {})
    dataset = dataset if isinstance(dataset, dict) else {}
    publication = metadata.get("publication", {})
    publication = publication if isinstance(publication, dict) else {}
    method = dataset.get("method")
    return {
        "label": method or dataset.get("name") or fallback_label,
        "method": method,
        "dataset": dataset.get("name"),
        "checksum_sha256": publication.get("checksum_sha256"),
        "checksum_kind": "package",
        "n_events": publication.get("event_count"),
        "assumptions": dataset.get("assumptions"),
    }


def _step(values: np.ndarray) -> np.ndarray:
    """Pad per-bin values to bin-edge length for a ``step="post"`` fill."""

    values = np.asarray(values, dtype=float)
    return np.append(values, values[-1])


def _relative(total: np.ndarray, hist: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(hist != 0.0, total / np.abs(hist), np.nan)


class CrossPublicationComparison:
    """Side-by-side comparison of two published results with independent
    uncertainty bands and a between-results ratio."""

    def __init__(
        self,
        package_a: OmniFoldPackage,
        package_b: OmniFoldPackage,
        observable: str,
        bins: list[float] | None = None,
        labels: tuple[str, str] | None = None,
        correlation: str = "none",
    ):
        if correlation not in CORRELATION_MODELS:
            allowed = ", ".join(CORRELATION_MODELS)
            raise PackageReadError(
                f"Unknown correlation model {correlation!r}; one of: {allowed}."
            )
        if correlation != "none":
            raise NotImplementedError(
                f"correlation={correlation!r} is not implemented. Only "
                "'none' (each side's band shown separately) is supported; a "
                "combined ratio band requires a confirmed correlation model "
                "between the two publications."
            )

        self.observable = observable
        self.correlation = correlation
        fallback_a, fallback_b = labels or ("A", "B")

        # shared bins: explicit, else side A's declared/official binning,
        # applied to BOTH by re-histogramming raw events (no interpolation).
        resolved = bins if bins is not None else package_a.observable_bins(observable)
        if resolved is None:
            raise PackageReadError(
                f"No shared bins given and package A declares none for "
                f"{observable!r}; pass explicit bin edges."
            )
        self.bins = [float(edge) for edge in resolved]

        self._side_a = self._build_side(package_a, fallback_a)
        self._side_b = self._build_side(package_b, fallback_b)

        a_hist = np.asarray(self._side_a["hist"], dtype=float)
        b_hist = np.asarray(self._side_b["hist"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(a_hist != 0.0, b_hist / a_hist, np.nan)
        self.ratio = {
            "reference": self._side_a["label"],
            "comparand": self._side_b["label"],
            "values": ratio.tolist(),
            # each side's own relative band, kept separate (see class doc)
            "reference_rel_band": _relative(
                np.asarray(self._side_a["uncertainty"]["total"]), a_hist
            ).tolist(),
            "comparand_rel_band": _relative(
                np.asarray(self._side_b["uncertainty"]["total"]), b_hist
            ).tolist(),
            "correlation": correlation,
        }

    def _build_side(
        self, package: OmniFoldPackage, fallback_label: str
    ) -> dict[str, Any]:
        breakdown = package.uncertainty_breakdown(self.observable, bins=self.bins)
        info = _describe(package, fallback_label)
        info["hist"] = np.asarray(breakdown["nominal"], dtype=float).tolist()
        info["uncertainty"] = {
            "total": np.asarray(breakdown["total"], dtype=float).tolist(),
            "components": {
                name: np.asarray(values, dtype=float).tolist()
                for name, values in breakdown["components"].items()
            },
        }
        return info

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "cross_publication_comparison",
            "observable": self.observable,
            "bins": self.bins,
            "sides": [self._side_a, self._side_b],
            "ratio": self.ratio,
            "provenance": {
                "correlation_model": self.correlation,
                "correlation_note": (
                    "each publication's uncertainty band is shown "
                    "independently; no correlation between the two results is "
                    "assumed for a combined ratio band"
                ),
                "rebinning": (
                    "both sides re-histogrammed from raw events with the "
                    "shared bin edges; no interpolation"
                ),
            },
        }

    def export_json(self, output_path: str | Path) -> None:
        with Path(output_path).open("w", encoding="utf-8") as stream:
            json.dump(self.to_dict(), stream, indent=2)

    def plot(self, output_path: str | Path | None = None) -> Any:
        """Main panel (both results with their own uncertainty bands) and a
        ratio panel (B/A, each band shown separately)."""

        import matplotlib.pyplot as plt

        edges = np.asarray(self.bins, dtype=float)
        centers = 0.5 * (edges[:-1] + edges[1:])
        fig, (ax, rax) = plt.subplots(
            2, 1, sharex=True, figsize=(7, 6.5),
            gridspec_kw={"height_ratios": [3, 1]},
        )

        for side, color in ((self._side_a, "#1f77b4"), (self._side_b, "#d6336c")):
            hist = np.asarray(side["hist"], dtype=float)
            total = np.asarray(side["uncertainty"]["total"], dtype=float)
            ax.stairs(hist, edges, color=color, linewidth=2, label=side["label"])
            # step band, not a fill across bin centres: the band must follow
            # the histogram it belongs to rather than interpolate between bins
            ax.fill_between(
                edges, _step(hist - total), _step(hist + total), step="post",
                color=color, alpha=0.25, linewidth=0,
            )
        # falling spectra span decades; a linear axis hides the tail bins and
        # their bands entirely, so switch to log once the range warrants it
        positive = np.concatenate([
            np.asarray(s["hist"], dtype=float) for s in (self._side_a, self._side_b)
        ])
        positive = positive[positive > 0.0]
        if positive.size and positive.max() / positive.min() > 20.0:
            ax.set_yscale("log")

        ax.set_ylabel("weighted events (fb)")
        ax.set_title(f"Cross-publication comparison — {self.observable}")
        ax.legend(frameon=False)

        values = np.asarray(self.ratio["values"], dtype=float)
        a_band = np.asarray(self.ratio["reference_rel_band"], dtype=float)
        b_band = np.asarray(self.ratio["comparand_rel_band"], dtype=float)
        rax.axhline(1.0, color="black", linewidth=0.8)
        # reference (A) band around 1, as a step for the same reason
        rax.fill_between(
            edges, _step(1 - a_band), _step(1 + a_band), step="post",
            color="#1f77b4", alpha=0.2, linewidth=0,
            label=f"{self._side_a['label']} unc.",
        )
        # comparand (B) points with its own band
        rax.errorbar(
            centers, values, yerr=np.abs(values) * b_band, fmt="o",
            color="#d6336c", markersize=4,
            label=f"{self._side_b['label']}/{self._side_a['label']}",
        )
        rax.set_ylabel(f"{self._side_b['label']} / {self._side_a['label']}")
        rax.set_xlabel(self.observable)
        rax.legend(frameon=False, fontsize=8)

        fig.tight_layout()
        if output_path is not None:
            fig.savefig(output_path, dpi=160)
        return fig


def compare_publications(
    package_a: OmniFoldPackage,
    package_b: OmniFoldPackage,
    observable: str,
    bins: list[float] | None = None,
    labels: tuple[str, str] | None = None,
    correlation: str = "none",
) -> CrossPublicationComparison:
    """Convenience constructor for :class:`CrossPublicationComparison`."""

    return CrossPublicationComparison(
        package_a, package_b, observable, bins=bins, labels=labels,
        correlation=correlation,
    )
