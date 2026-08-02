"""Discovery, metadata, citation, and health routes."""

from __future__ import annotations


def test_list_resources_and_filters(client):
    all_resources = client.get("/resources").json()["resources"]
    ids = {r["id"]: r for r in all_resources}
    assert {"zjets_nominal", "zjets_sherpa", "zjets_analysis"} <= set(ids)
    assert ids["zjets_nominal"]["kind"] == "package"
    assert ids["zjets_analysis"]["kind"] == "analysis"
    assert ids["zjets_nominal"]["source"] == "local:zjets_nominal"
    assert "pT_ll" in ids["zjets_nominal"]["observables"]

    packages = client.get("/packages").json()["resources"]
    assert all(r["kind"] == "package" for r in packages)
    analyses = client.get("/analyses").json()["resources"]
    assert all(r["kind"] == "analysis" for r in analyses)


def test_checksum_kind_distinguishes_package_from_analysis(client):
    resources = {
        r["id"]: r for r in client.get("/resources").json()["resources"]
    }
    # a package reports its own event-file hash
    assert resources["zjets_nominal"]["checksum_kind"] == "package"
    # an analysis reports a backend-derived composite, clearly labelled
    assert resources["zjets_analysis"]["checksum_kind"] == "analysis_composite"

    # the label reaches provenance and the citation note, so a citer can
    # tell "hash of real bytes" apart from "synthetic aggregate"
    prov = client.get(
        "/analyses/zjets_analysis/histogram?observable=pT_ll"
    ).json()["provenance"]
    assert prov["checksum_kind"] == "analysis_composite"

    citation = client.get("/analyses/zjets_analysis/citation").json()
    assert "analysis composite" in citation["citation"]


def test_metadata_endpoint(client):
    meta = client.get("/packages/zjets_nominal/metadata").json()
    assert meta["format_version"]
    assert meta["publication"]["checksum_sha256"]


def test_citation_is_total_function_without_doi(client):
    resp = client.get("/packages/zjets_nominal/citation")
    assert resp.status_code == 200
    body = resp.json()
    # no DOI in the fixture metadata -> @misc with checksum anchor
    assert body["has_doi"] is False
    assert body["entry_type"] == "misc"
    assert "@misc{zjets_nominal" in body["citation"]
    assert "sha256:" in body["citation"]


def test_health_ready_version(client):
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready").json()
    assert ready["status"] == "ready"
    assert ready["resources_discovered"] >= 3
    version = client.get("/version").json()
    assert version["api_version"] == "2.0.0"
    assert version["environment"] == "dev"
