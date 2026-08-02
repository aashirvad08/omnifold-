"""Parity: every physics number in a response equals a direct package call.

The API is only allowed to (a) call one package method and (b) apply
``to_jsonable``. These tests assert the wire ``data`` equals
``to_jsonable(<direct package call>)`` exactly — float-for-float — for
every computation endpoint and across all three bin-spec forms.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from omnifold_publication import load_analysis, load_package

from backend.serialization import to_jsonable


def _data(client, url: str) -> dict:
    resp = client.get(url)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.mark.parametrize(
    "query",
    ["observable=pT_ll", "observable=pT_ll&nbins=5",
     "observable=pT_ll&bins=200&bins=400&bins=1000"],
)
def test_histogram_parity(client, nominal_pkg_dir, query):
    api = _data(client, f"/packages/zjets_nominal/histogram?{query}")

    pkg = load_package(nominal_pkg_dir)
    if "bins=200" in query:
        bins: list[float] | int | None = [200.0, 400.0, 1000.0]
    elif "nbins=5" in query:
        bins = 5
    else:
        bins = None
    direct = pkg.histogram("pT_ll", variation="nominal", bins=bins).to_dict()
    assert api == to_jsonable(direct)


def test_histogram_variation_parity(client, nominal_pkg_dir):
    api = _data(
        client,
        "/packages/zjets_nominal/histogram?observable=pT_ll&variation=weights_pileup",
    )
    pkg = load_package(nominal_pkg_dir)
    direct = pkg.histogram("pT_ll", variation="weights_pileup").to_dict()
    assert api == to_jsonable(direct)


def test_comparison_parity(client, analysis_dir):
    api = _data(
        client,
        "/analyses/zjets_analysis/comparison?observable=pT_ll&bins=200&bins=400&bins=1000",
    )
    analysis = load_analysis(analysis_dir)
    comparison = analysis.compare("pT_ll", bins=[200.0, 400.0, 1000.0])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "c.json"
        comparison.export_json(out)
        direct = json.loads(out.read_text())
    assert api == to_jsonable(direct)


def test_replica_envelope_parity(client, analysis_dir):
    api = _data(
        client,
        "/analyses/zjets_analysis/replica-envelope?observable=pT_ll",
    )
    analysis = load_analysis(analysis_dir)
    direct = analysis.histogram("pT_ll").to_dict()
    assert api == to_jsonable(direct)
    # the whole point of this endpoint: a replica band is present
    assert api.get("replica_uncertainty") is not None


def test_uncertainty_breakdown_parity(client, nominal_pkg_dir):
    api = _data(
        client,
        "/packages/zjets_nominal/uncertainty-breakdown?observable=pT_ll",
    )
    pkg = load_package(nominal_pkg_dir)
    direct = pkg.uncertainty_breakdown("pT_ll")
    assert api == to_jsonable(direct)


def test_covariance_matrix_parity(client, nominal_pkg_dir):
    api = _data(
        client,
        "/packages/zjets_nominal/covariance-matrix?observable=pT_ll",
    )
    pkg = load_package(nominal_pkg_dir)
    direct = pkg.covariance_matrix("pT_ll")
    assert api == to_jsonable(direct)


def test_provenance_traces_to_source(client):
    resp = client.get("/packages/zjets_nominal/histogram?observable=pT_ll")
    prov = resp.json()["provenance"]
    assert prov["resource_id"] == "zjets_nominal"
    assert prov["kind"] == "package"
    assert prov["source"] == "local:zjets_nominal"
    assert len(prov["checksum_sha256"]) == 64
    assert prov["operation"] == "histogram"
    assert prov["cached"] is False
    assert prov["resolved_bins"][0] == 200.0  # official binning resolved
