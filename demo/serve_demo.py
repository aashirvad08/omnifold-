#!/usr/bin/env python3
"""Serve the proof-of-concept demo page from the real backend.

The page is served at ``/`` by the same app that serves the API, so the
browser fetches ``/analyses`` and ``/analyses/{id}/histogram`` same-origin
— no CORS, no mock data. Point it at a directory of published packages
with OMNIFOLD_DATA_ROOT (defaults to /tmp/omni_demo).

    OMNIFOLD_DATA_ROOT=/tmp/omni_demo python demo/serve_demo.py
    # then open http://127.0.0.1:8000/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.config import Settings  # noqa: E402

DATA_ROOT = Path(os.environ.get("OMNIFOLD_DATA_ROOT", "/tmp/omni_demo"))
INDEX = Path(__file__).resolve().parent / "index.html"

app = create_app(Settings(env="dev", data_root=DATA_ROOT))


@app.get("/", include_in_schema=False)
def demo_page() -> FileResponse:
    return FileResponse(INDEX)


if __name__ == "__main__":
    print(f"Serving demo at http://127.0.0.1:8000/  (data_root={DATA_ROOT})")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
