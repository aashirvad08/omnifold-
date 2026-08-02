"""Cache correctness: no key collisions across the inputs that matter,
and real hit/miss behaviour end to end."""

from __future__ import annotations

import pytest

from backend.services.cache import bins_token, make_cache_key


def test_bins_token_distinguishes_none_int_edges():
    assert bins_token(None) == "none"
    assert bins_token(5) == "nbins:5"
    assert bins_token([200.0, 300.0]) == "edges:200.0,300.0"
    # the three forms must never share a token
    tokens = {bins_token(None), bins_token(5), bins_token([200.0, 300.0])}
    assert len(tokens) == 3
    # bool must not masquerade as an int nbins
    with pytest.raises(TypeError):
        bins_token(True)


def test_operation_type_never_collides():
    common = {
        "content_checksum": "abc", "observable": "pT_ll",
        "bins": [200.0, 300.0],
    }
    keys = {
        make_cache_key(op, **common)
        for op in ("histogram", "comparison", "replica_envelope",
                   "uncertainty_breakdown", "covariance_matrix")
    }
    assert len(keys) == 5  # every operation is a distinct key


def test_bins_forms_never_collide_in_key():
    common = {
        "operation": "histogram", "content_checksum": "abc",
        "observable": "pT_ll",
    }
    keys = {
        make_cache_key(bins=None, **common),
        make_cache_key(bins=5, **common),
        make_cache_key(bins=[200.0, 300.0], **common),
        make_cache_key(bins=[200.0, 300.0, 400.0], **common),
    }
    assert len(keys) == 4


def test_variation_and_checksum_change_key():
    base = make_cache_key("histogram", "abc", "pT_ll", None, variation="nominal")
    assert base != make_cache_key(
        "histogram", "abc", "pT_ll", None, variation="weights_pileup"
    )
    assert base != make_cache_key(
        "histogram", "DIFFERENT", "pT_ll", None, variation="nominal"
    )


def test_end_to_end_hit_then_miss_on_different_op(client):
    first = client.get("/packages/zjets_nominal/histogram?observable=pT_ll")
    assert first.json()["provenance"]["cached"] is False
    second = client.get("/packages/zjets_nominal/histogram?observable=pT_ll")
    assert second.json()["provenance"]["cached"] is True
    # same observable/bins, different operation -> must be a miss, not a hit
    other = client.get(
        "/packages/zjets_nominal/uncertainty-breakdown?observable=pT_ll"
    )
    assert other.json()["provenance"]["cached"] is False
    # different bins -> miss
    rebinned = client.get(
        "/packages/zjets_nominal/histogram?observable=pT_ll&nbins=7"
    )
    assert rebinned.json()["provenance"]["cached"] is False
