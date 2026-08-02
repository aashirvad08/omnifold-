"""HEPData export (validated against HEPData's own schema) and streamed
package download with symlink-escape exclusion."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from omnifold_publication import write_package

from backend.app import create_app
from backend.config import Settings


def _members(content: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        return tar.getnames()


def test_hepdata_yaml_streams_and_validates(client):
    resp = client.get("/packages/zjets_nominal/hepdata-yaml")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/gzip"
    assert "zjets_nominal_hepdata.tar.gz" in resp.headers.get(
        "content-disposition", ""
    )

    import tempfile

    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        assert "hepdata/submission.yaml" in tar.getnames()
        # extract to a temp dir and run HEPData's own validators
        with tempfile.TemporaryDirectory() as tmp:
            tar.extractall(tmp)
            _validate_hepdata(Path(tmp) / "hepdata")


def _validate_hepdata(submission_dir: Path) -> None:
    from hepdata_validator.data_file_validator import DataFileValidator
    from hepdata_validator.submission_file_validator import (
        SubmissionFileValidator,
    )

    submission = submission_dir / "submission.yaml"
    sub_validator = SubmissionFileValidator()
    assert sub_validator.validate(file_path=str(submission)), (
        sub_validator.get_messages()
    )
    data_validator = DataFileValidator()
    for table in submission_dir.glob("data_*.yaml"):
        assert data_validator.validate(file_path=str(table)), (
            data_validator.get_messages()
        )


def test_hepdata_yaml_for_analysis_uses_nominal(client):
    resp = client.get("/analyses/zjets_analysis/hepdata-yaml")
    assert resp.status_code == 200
    assert "hepdata/submission.yaml" in _members(resp.content)


def test_download_package_streams_contents(client):
    resp = client.get("/packages/zjets_nominal/download-package")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    names = _members(resp.content)
    assert "zjets_nominal/metadata.yaml" in names
    assert "zjets_nominal/events.parquet" in names


def test_download_excludes_symlink_escaping_the_package(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    # a real package under the root
    src = tmp_path / "src.h5"
    pd.DataFrame(
        {
            "pT_ll": np.random.default_rng(0).uniform(200, 1000, 30),
            "pT_l1": np.random.default_rng(1).uniform(25, 800, 30),
            "weight_mc": np.ones(30),
            "weights_nominal": np.ones(30),
        }
    ).to_hdf(src, key="df", mode="w")
    write_package(input_path=src, output_dir=root / "pkg", event_count=30)

    # a secret outside the root, and a symlink inside the package pointing to it
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    (root / "pkg" / "leak").symlink_to(secret)

    app = create_app(Settings(env="dev", data_root=root))
    resp = TestClient(app).get("/packages/pkg/download-package")
    assert resp.status_code == 200

    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = tar.getnames()
        # the legitimate files are present
        assert "pkg/metadata.yaml" in names
        # the escaping symlink is excluded entirely
        assert "pkg/leak" not in names
        # and no member's content is the secret
        for member in tar.getmembers():
            if member.isfile():
                data = tar.extractfile(member).read()
                assert b"TOP SECRET" not in data


def test_export_unknown_resource_404(client):
    assert client.get("/packages/nope/hepdata-yaml").status_code == 404
    assert client.get("/packages/nope/download-package").status_code == 404
