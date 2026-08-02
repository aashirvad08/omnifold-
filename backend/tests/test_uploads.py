"""Uploads: safe receive, ad-hoc parity, and the upload-histogram job."""

from __future__ import annotations

import io
import time

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from omnifold_publication.histogram import compute_weighted_histogram

from backend.app import create_app
from backend.config import Settings
from backend.serialization import to_jsonable


def _parquet_bytes(n: int = 40, seed: int = 3) -> bytes:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {
            "pT_ll": rng.uniform(200.0, 1000.0, n),
            "weights_nominal": rng.uniform(0.5, 1.5, n),
        }
    )
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _upload(content: bytes, name: str = "events.parquet"):
    return {"file": (name, content, "application/octet-stream")}


def test_file_inspect(client, settings):
    content = _parquet_bytes()
    resp = client.post("/files/inspect", files=_upload(content))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format"] == "parquet"
    assert set(body["columns"]) == {"pT_ll", "weights_nominal"}
    assert body["n_rows"] == 40
    assert len(body["checksum_sha256"]) == 64
    # the staged upload must not linger
    assert not (settings.upload_dir / "events.parquet").exists()


def test_adhoc_histogram_parity(client):
    content = _parquet_bytes()
    resp = client.post(
        "/histograms/adhoc"
        "?observable=pT_ll&weight_column=weights_nominal"
        "&bins=200&bins=400&bins=1000",
        files=_upload(content),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    frame = pd.read_parquet(io.BytesIO(content))
    direct = compute_weighted_histogram(
        frame["pT_ll"].to_numpy(dtype=float),
        frame["weights_nominal"].to_numpy(dtype=float),
        bins=[200.0, 400.0, 1000.0],
    )
    assert data == to_jsonable(direct)

    prov = resp.json()["provenance"]
    assert prov["kind"] == "upload"
    assert prov["checksum_kind"] == "upload"
    assert prov["operation"] == "adhoc_histogram"


def test_adhoc_session_cache_hit(client):
    content = _parquet_bytes(seed=9)
    url = "/histograms/adhoc?observable=pT_ll&nbins=4"
    first = client.post(url, files=_upload(content))
    assert first.json()["provenance"]["cached"] is False
    second = client.post(url, files=_upload(content))
    # identical upload (same checksum) + params -> session cache hit
    assert second.json()["provenance"]["cached"] is True


def test_magic_bytes_reject_disguised_file(client):
    resp = client.post(
        "/files/inspect",
        files=_upload(b"#!/bin/sh\necho not parquet\n", "evil.parquet"),
    )
    assert resp.status_code == 415


def test_unsupported_suffix_rejected(client):
    resp = client.post(
        "/files/inspect", files=_upload(b"anything", "notes.txt")
    )
    assert resp.status_code == 415


def test_size_cap_enforced(data_root):
    small = create_app(
        Settings(
            env="dev",
            data_root=data_root,
            upload_dir=data_root / "_uploads_small",
            max_upload_bytes=16,
        )
    )
    client = TestClient(small)
    resp = client.post("/files/inspect", files=_upload(_parquet_bytes()))
    assert resp.status_code == 413


def test_upload_histogram_job_lifecycle(client, settings):
    content = _parquet_bytes()
    submit = client.post(
        "/jobs/upload-histogram?observables=pT_ll&nbins=5",
        files=_upload(content),
    )
    assert submit.status_code == 202
    job_id = submit.json()["job_id"]

    deadline = time.time() + 5.0
    state = None
    while time.time() < deadline:
        status = client.get(f"/jobs/{job_id}").json()
        state = status["state"]
        if state in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.02)
    assert state == "succeeded"

    result = client.get(f"/jobs/{job_id}/result")
    assert result.status_code == 200
    assert "pT_ll" in result.json()["histograms"]
    # staged upload cleaned up by the job's finally
    assert not (settings.upload_dir / "events.parquet").exists()


def test_result_before_success_is_409(data_root):
    # a job that blocks lets us observe the pending-result 409 deterministically
    app = create_app(
        Settings(env="dev", data_root=data_root,
                 upload_dir=data_root / "_uploads_block")
    )
    from backend.services.jobs import JobManager

    mgr: JobManager = app.state.jobs
    import threading

    release = threading.Event()
    job_id = mgr.submit(lambda token, report: release.wait(5.0) or {"ok": 1})
    client = TestClient(app)
    # still running -> result is 409
    assert client.get(f"/jobs/{job_id}/result").status_code == 409
    assert client.get(f"/jobs/{job_id}").json()["state"] in {
        "queued", "running"
    }
    release.set()
    mgr.shutdown()
