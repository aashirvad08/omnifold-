"""Path safety: the registry is the only way to reach disk, and it never
resolves outside the data root."""

from __future__ import annotations

import pytest

from backend.services.registry import (
    Registry,
    ResourceNotFound,
    _is_within,
)


def test_traversal_ids_are_not_registered(settings):
    registry = Registry(settings.data_root)
    ids = {ref.id for ref in registry.list()}
    # only sanitised leaf names; nothing path-like
    assert "zjets_nominal" in ids
    assert all("/" not in i and ".." not in i for i in ids)
    for bogus in ("../secret", "..", "a/b", "zjets_nominal/../x"):
        with pytest.raises(ResourceNotFound):
            registry.get(bogus)


def test_symlink_escaping_root_is_not_classified(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside_pkg"
    outside.mkdir()
    (outside / "metadata.yaml").write_text("format_version: '0.2'\n")
    root.mkdir()
    link = root / "escaped"
    link.symlink_to(outside, target_is_directory=True)

    registry = Registry(root)
    # a directory whose real location is outside the root is not a resource
    assert registry.list() == []
    with pytest.raises(ResourceNotFound):
        registry.get("escaped")


def test_access_time_containment_guard(tmp_path):
    root = (tmp_path / "root").resolve()
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "metadata.yaml").write_text("format_version: '0.2'\n")
    root.mkdir()
    real = root / "pkg"
    real.symlink_to(inside, target_is_directory=True)
    # inside root and resolving inside a sibling that is still under tmp but
    # not under root: _is_within must reject it
    assert _is_within(inside, root) is False
    assert _is_within(root / "pkg", root) is False  # resolves to `inside`


def test_unknown_resource_returns_404(client):
    assert client.get("/packages/does_not_exist/metadata").status_code == 404
    assert (
        client.get(
            "/packages/does_not_exist/histogram?observable=pT_ll"
        ).status_code
        == 404
    )


def test_analysis_only_operation_on_package_is_409(client):
    resp = client.get(
        "/packages/zjets_nominal/comparison?observable=pT_ll"
    )
    assert resp.status_code == 409


def test_bad_observable_is_400(client):
    resp = client.get(
        "/packages/zjets_nominal/histogram?observable=not_an_observable"
    )
    assert resp.status_code == 400
