"""Tests for canonical derived final event weights."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from omnifold_publication import (
    PackageReadError,
    load_analysis,
    load_package,
    write_manifest,
    write_package,
)


def _write_analysis(tmp_path, source_hdf):
    nominal = write_package(
        input_path=source_hdf,
        output_dir=tmp_path / "nominal",
        event_count=6,
    )
    manifest_dir = tmp_path / "analysis"
    write_manifest(
        output_dir=manifest_dir,
        nominal_path=nominal,
        variations={},
        analysis_name="final-weight-test",
    )
    return manifest_dir


def test_final_weights_equal_base_times_nominal(tmp_path, source_hdf):
    package_dir = write_package(
        input_path=source_hdf,
        output_dir=tmp_path / "package",
        event_count=6,
    )
    package = load_package(package_dir)
    events = package.load_events(columns=["weight_mc", "weights_nominal"])

    expected = (
        events["weight_mc"].to_numpy()
        * events["weights_nominal"].to_numpy()
    )
    np.testing.assert_allclose(package.get_weights("final"), expected)


def test_nominal_weights_are_unchanged(tmp_path, source_hdf):
    package_dir = write_package(
        input_path=source_hdf,
        output_dir=tmp_path / "package",
        event_count=6,
    )
    package = load_package(package_dir)

    expected = package.load_events(columns=["weights_nominal"])[
        "weights_nominal"
    ].to_numpy()
    np.testing.assert_allclose(package.get_weights("nominal"), expected)


def test_final_weights_missing_base_column_raises(tmp_path, source_hdf):
    package_dir = write_package(
        input_path=source_hdf,
        output_dir=tmp_path / "package",
        event_count=6,
    )
    events_path = package_dir / "events.parquet"
    events = pd.read_parquet(events_path).drop(columns=["weight_mc"])
    events.to_parquet(events_path, index=False)

    package = load_package(package_dir)
    with pytest.raises(PackageReadError, match="required columns"):
        package.get_weights("final")


def test_analysis_final_weights_work(tmp_path, source_hdf):
    analysis = load_analysis(_write_analysis(tmp_path, source_hdf))

    np.testing.assert_allclose(
        analysis.get_weights("final"),
        analysis.nominal_package.get_weights("final"),
    )
