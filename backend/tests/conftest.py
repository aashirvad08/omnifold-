"""Backend test fixtures.

Fixtures are built through the package's own real publication path
(``write_package`` / ``write_manifest``) — not hand-crafted JSON — so
parity tests compare the API against genuinely-constructed packages and
would catch a real discrepancy.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def _quiet_access_log() -> None:
    # keep the JSON access log out of test output; tests that assert on
    # logging attach their own capturing handler
    logger = logging.getLogger("omnifold.access")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from omnifold_publication import write_manifest, write_package  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.config import Settings  # noqa: E402


def _atlas_like_frame(n: int = 500, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = rng.uniform(0.5, 1.5, n)
    nominal = base * rng.normal(1.0, 0.1, n)

    def varied(scale: float = 0.05) -> np.ndarray:
        return nominal * rng.normal(1.0, scale, n)

    data: dict[str, np.ndarray] = {
        "event_id": np.arange(n),
        # values drawn within the official binning ranges so bins populate
        "pT_ll": rng.uniform(200.0, 1000.0, n),
        "pT_l1": rng.uniform(25.0, 800.0, n),
        "weight_mc": base,
        "weights_nominal": nominal,
        "weights_pileup": varied(),
        "weights_muEffReco": varied(),
        "weights_theoryPDF": varied(),
        "weights_theoryQCD": varied(),
        "weights_trackFake": varied(),
        "weights_muCalID": varied(),
        "weights_lumi": varied(0.017),
        "weights_topBackground": varied(),
        "weights_dd": varied(),
        "target_dd": varied(),
    }
    for i in range(4):
        data[f"weights_bootstrap_mc_{i}"] = varied()
        data[f"weights_bootstrap_data_{i}"] = varied()
    for i in range(6):
        data[f"weights_ensemble_{i}"] = varied()
    return pd.DataFrame(data)


def _write_source(path: Path, seed: int) -> Path:
    _atlas_like_frame(seed=seed).to_hdf(path, key="df", mode="w")
    return path


@pytest.fixture(scope="session")
def data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("data_root")
    src_nominal = _write_source(root / "_src_nominal.h5", seed=1)
    src_sherpa = _write_source(root / "_src_sherpa.h5", seed=2)

    nominal_pkg = write_package(
        input_path=src_nominal,
        output_dir=root / "zjets_nominal",
        event_count=500,
        include_all_replicas=True,
    )
    sherpa_pkg = write_package(
        input_path=src_sherpa,
        output_dir=root / "zjets_sherpa",
        event_count=500,
    )
    write_manifest(
        output_dir=root / "zjets_analysis",
        nominal_path=nominal_pkg,
        variations={
            "sherpa": {
                "path": sherpa_pkg,
                "type": "alternative_generator",
                "combination": "two_point_difference",
            }
        },
        analysis_name="zjets-closure",
    )
    return root


@pytest.fixture(scope="session")
def settings(data_root: Path) -> Settings:
    return Settings(
        env="dev",
        data_root=data_root,
        upload_dir=data_root / "_uploads",
        cache_backend="memory",
    )


@pytest.fixture()
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def nominal_pkg_dir(data_root: Path) -> Path:
    return data_root / "zjets_nominal"


@pytest.fixture()
def analysis_dir(data_root: Path) -> Path:
    return data_root / "zjets_analysis"
