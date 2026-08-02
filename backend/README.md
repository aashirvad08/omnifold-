# OmniFold Publication Explorer — Backend (Phase 2)

A production-shaped FastAPI service over the `omnifold_publication` package
(Phase 0) and its `DataSource` layer (Phase 1). It serves discovery,
histogram/comparison/uncertainty computation, ad-hoc uploads, and background
jobs — **without ever recomputing physics**: every number in a response is
the output of exactly one package call, transformed only by a single
NaN→null JSON conversion. That property is enforced by parity tests, not
just intended.

## Layout

```
backend/
├── app.py               # application factory + middleware wiring
├── config.py            # Pydantic Settings (env-driven; dev/staging/prod)
├── dependencies.py      # shared-service accessors
├── serialization.py     # to_jsonable — the ONLY transform on physics numbers
├── middleware.py        # observability (outer) → rate limit → auth
├── models/schemas.py    # request/response models incl. the provenance envelope
├── routes/              # health, sources, resources, histograms, uploads, jobs
├── services/            # registry, cache, compute, citation, jobs, uploads,
│                        # ratelimit, auth, observability
└── tests/               # parity, cache, path-safety, jobs, uploads, security,
                         # observability, discovery
```

Key design choices (fuller rationale in the module docstrings):

- **Registry is the single path chokepoint.** Every disk path is resolved
  (symlinks included) and checked to live under `data_root`; no route ever
  takes a raw filesystem path. IDs are the (sanitised) directory name, so
  provenance maps back to disk; safety comes from the containment check,
  not id opacity.
- **Provenance envelope** on every computed result: source, content
  checksum + `checksum_kind` (`package` = a real file hash;
  `analysis_composite` = a backend-derived aggregate; `upload` = an
  uploaded file's hash — never conflated), operation, resolved bins,
  `format_version`/`api_version`/`package_version`, `computed_at`, `cached`.
- **Tiered cache** (immutable / session / none) with a key that folds in
  operation type, content checksum, observable, variation, and a
  *type-tagged* bins token so `None` / `nbins:N` / explicit-edges can never
  collide.

## Run it locally

From the **repository root** (so both `backend` and `omnifold_publication`
are importable — the package is used from the source tree, not pip-installed):

```bash
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt

# point the registry at a directory of published packages/analyses
export OMNIFOLD_DATA_ROOT=artifacts
uvicorn backend.app:create_app --factory --reload
```

Then: `http://127.0.0.1:8000/docs` for the OpenAPI UI, `GET /resources` to
list what was discovered under `OMNIFOLD_DATA_ROOT`.

A resource is any directory one level under the root containing either
`manifest.yaml` (→ an **analysis**) or `metadata.yaml` (→ a **package**).
Produce some with the package's own `write_package` / `write_manifest`.

## Configuration (environment variables)

All settings are env-driven with the prefix `OMNIFOLD_` (e.g.
`OMNIFOLD_REQUIRE_API_KEY=true`). Defaults are safe for local dev; prod is
selected with `OMNIFOLD_ENV=prod`, which forces API-key auth on.

| Env var | Default | Meaning |
|---|---|---|
| `OMNIFOLD_ENV` | `dev` | `dev` / `staging` / `prod` |
| `OMNIFOLD_DATA_ROOT` | `artifacts` | root scanned for packages/analyses |
| `OMNIFOLD_UPLOAD_DIR` | `uploads` | where uploads are staged |
| `OMNIFOLD_CACHE_BACKEND` | `memory` | `memory` or `redis` |
| `OMNIFOLD_REDIS_URL` | — | required if cache backend is `redis` |
| `OMNIFOLD_IMMUTABLE_CACHE_TTL_SECONDS` | `604800` | published-data cache TTL |
| `OMNIFOLD_SESSION_CACHE_TTL_SECONDS` | `3600` | upload/session cache TTL |
| `OMNIFOLD_MAX_UPLOAD_BYTES` | `2147483648` | 2 GiB upload cap |
| `OMNIFOLD_JOB_RETENTION_SECONDS` | `3600` | terminal-job retention |
| `OMNIFOLD_REQUIRE_API_KEY` | `false` (dev) / forced `true` (prod) | enable auth |
| `OMNIFOLD_API_KEYS` | `()` | accepted keys (constant-time compared) |
| `OMNIFOLD_TRUSTED_PROXIES` | `()` | peer IPs whose `X-Forwarded-For` is honoured |
| `OMNIFOLD_RATE_LIMIT_PER_MINUTE` | `60` | per-IP request budget |
| `OMNIFOLD_ALLOWED_SOURCE_KINDS` | `local,upload,zenodo` | openable source kinds |
| `OMNIFOLD_EGRESS_ALLOWLIST` | `zenodo.org` | hosts reachable by network kinds |

## Endpoints

- **Discovery**: `GET /sources`, `GET /resources` (`?kind=`), `GET /analyses`,
  `GET /packages`, `GET /{resources|analyses|packages}/{id}/metadata`.
- **Computation** (each under `/resources|/analyses|/packages`):
  `/{id}/histogram` (`?observable=&variation=&bins=…|nbins=`),
  `/{id}/comparison`, `/{id}/replica-envelope`,
  `/{id}/uncertainty-breakdown`, `/{id}/covariance-matrix`.
  Comparison and replica-envelope require an analysis (else 409).
- **Uploads**: `POST /files/inspect`, `POST /histograms/adhoc`,
  `POST /jobs/upload-histogram`.
- **Jobs**: `GET /jobs/{id}`, `GET /jobs/{id}/result`, `DELETE /jobs/{id}`.
- **Export** (each under `/resources|/analyses|/packages`):
  `/{id}/citation` (BibLaTeX; `@dataset` when a DOI is present, else
  `@misc` with the checksum as the reproducibility anchor),
  `/{id}/hepdata-yaml` (streamed `.tar.gz` of the package's HEPData
  submission — `submission.yaml` + per-table YAMLs, produced by the
  package's own `export_hepdata`; for an analysis, its nominal package),
  `/{id}/download-package` (streamed `.tar.gz` of the resource directory,
  with symlink-escape exclusion).
- **Ops**: `GET /health`, `GET /ready`, `GET /version`, `GET /metrics`.

Bins resolve identically to the package: pass explicit `bins=` edges
(repeatable) **or** an integer `nbins=`, never both; omit for the
observable's declared/official binning.

### Job status semantics

| State | `GET /jobs/{id}` | `GET /jobs/{id}/result` |
|---|---|---|
| queued / running | 200 status | 409 |
| succeeded | 200 status | 200 payload |
| failed | 200 status (error in body) | 409 |
| cancelled | 200 status (progress frozen) | 409 |
| unknown / expired | 404 | 404 |

`DELETE /jobs/{id}` requests cooperative cancellation and is idempotent on
an already-terminal job. Cancellation is checkpoint-based: in-flight work
stops at the next checkpoint (proven by a barrier test), it is not merely
flagged done.

## Tests, lint, types

```bash
python -m pytest backend/tests/ -q          # 57 passed
cd backend && ruff check . && mypy --config-file mypy.ini
```

`ruff` is clean; `mypy --strict` is clean on the shipped code (the
`files=` list in `mypy.ini`). Tests are validated by ruff + pytest rather
than strict typing, since numpy-array fixtures fight strict generics
without catching anything. Fixtures are built through the package's real
`write_package` / `write_manifest` path so parity tests would catch an
actual discrepancy.

## Security posture

- **Rate limiting**: per-IP fixed window; `X-Forwarded-For` is trusted
  **only** from configured `trusted_proxies`, walking the chain
  right-to-left. A spoofed XFF from an untrusted peer cannot move or mint a
  bucket (tested with two real distinct peers and a spoof case).
- **Auth**: `X-API-Key` compared in constant time; optional in dev, forced
  in prod. Public ops/docs paths are always reachable.
- **SSRF egress**: `url:` / `s3:` source kinds are deny-by-default;
  `zenodo:` is allowed but host-pinned to `zenodo.org`; enabling `url:`
  still requires an allowlisted host.
- **Uploads**: streamed to disk in chunks, size-capped (Content-Length and
  byte-for-byte), validated by magic bytes (not extension), basename-only,
  deleted as soon as the work finishes, and TTL-swept if abandoned.
- **Observability**: JSON access logs with request IDs; Prometheus
  `/metrics` labelled by route template (not raw path) to bound cardinality.

## Honest limitations

These are real and should inform deployment; none are hidden behind a
passing test.

- **Single-node, in-memory jobs and rate limiter.** The `JobManager` and
  the rate-limit buckets live in one process's memory. They are **not**
  shared across workers or hosts: with multiple uvicorn workers, jobs are
  only visible to the worker that created them, and each worker enforces
  its own rate-limit budget (effective limit ≈ N × configured). Deploy the
  job/limiter tier as a single process, or back them with Redis, before
  horizontal scaling. The cache has a swappable Redis backend already; the
  job manager exposes the same interface a Redis/RQ implementation would
  fill, but only the in-memory one ships.
- **In-request upload receive.** ASGI consumes the request body before the
  handler runs, so an uploaded file is *staged synchronously in the
  request* (chunked and size-capped); only the subsequent computation is
  the cancellable background job. A multi-GB upload therefore occupies the
  request for its transfer time. `stage_upload` carries a cancellable
  chunk loop for completeness, but the in-request receive itself is not a
  pollable job. This is inherent to ASGI file handling, not a shortcut.
- **Cancellation cannot interrupt a single atomic call.** Cooperative
  checkpoints stop work *between* units (upload chunks, per-observable
  loop iterations, replica loops). One in-flight atomic numpy/pandas call
  — e.g. a single large `pd.read_hdf` — runs to completion; interrupting
  that would need process isolation, which was deliberately declined for
  its cost. Accepted tradeoff.
- **Auth is global-per-environment, not per-route** (documented tradeoff).
  When `require_api_key` is on, every non-public route requires a key;
  when off (dev), none do. If you ever want upload/job endpoints to
  require a key *regardless of environment*, special-case them in
  `AuthMiddleware` (check the path prefix before the `require_api_key`
  gate) — a small, localized change. Left as an option rather than a
  silent gap.
- **Rate limiting is blanket over all non-public routes**, not a curated
  expensive-endpoint list (an allowlist that must be updated per new route
  is a liability). Cheap discovery calls share the same generous budget.
- **429 responses log/metric as `route="unmatched"`** because they
  short-circuit before routing resolves the template; the metric does not
  attribute a throttle to a specific route.
- **Prometheus registry is per-app in memory**; scrape it before the
  process restarts (standard for a stateless exporter).
- **Redis cache/job paths ship but are not exercised in CI** (no Redis in
  the test environment); the in-memory paths are fully tested.
- **Export tars buffer to a temp file on disk** (not memory): the archive
  is written block-by-block to a temp directory and the response streams
  that file, then a background task removes it. Memory is bounded by the
  tar block size, not the file size; transient disk equal to the archive
  size is used. `download-package` on an *analysis* tars the manifest
  directory only (its member packages are separate resources).
