"""Upload handling and ad-hoc computation over uploaded files.

Reuses Phase 1's :class:`UploadDataSource` (chunked receive, magic-byte
validation, basename-only, auto-cleanup) rather than reinventing it, and
adds a cancellable staged receive for the job path. Uploaded files are
private inputs: they are deleted as soon as the work that used them
finishes, and abandoned files are swept by TTL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO

import pandas as pd
from omnifold_publication.histogram import compute_weighted_histogram
from omnifold_publication.sources.upload import (
    RECEIVE_CHUNK_BYTES,
    UploadDataSource,
    _expected_magic,
    _validate_magic,
)
from omnifold_publication.sources.upload import (
    sweep_expired_uploads as _sweep_expired_uploads,
)

from ..serialization import to_jsonable
from .jobs import CancelToken


class UploadTooLarge(Exception):
    """Upload exceeded the configured size cap."""


class UploadInvalidFormat(Exception):
    """Upload suffix or magic bytes did not match a supported format."""


def stage_upload(
    stream: BinaryIO,
    upload_dir: Path,
    filename: str,
    max_bytes: int,
    token: CancelToken | None = None,
) -> UploadDataSource:
    """Stream an upload to disk in chunks, size-capped and format-checked.

    Never buffers the whole file in memory. If ``token`` is given, the
    receive is cancellable between chunks. Returns a Phase 1
    :class:`UploadDataSource` positioned on the staged file.
    """

    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / Path(filename).name  # basename only, never a path
    try:
        _expected_magic(target.suffix.lower())
    except Exception as exc:
        raise UploadInvalidFormat(str(exc)) from exc

    written = 0
    try:
        with target.open("wb") as sink:
            while True:
                if token is not None:
                    token.checkpoint()
                chunk = stream.read(RECEIVE_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise UploadTooLarge(
                        f"upload exceeds the {max_bytes}-byte limit"
                    )
                sink.write(chunk)
        try:
            _validate_magic(target)
        except Exception as exc:
            raise UploadInvalidFormat(str(exc)) from exc
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return UploadDataSource(target)


def inspect_upload(source: UploadDataSource) -> dict[str, Any]:
    """Cheap description of an uploaded file: format, size, checksum,
    columns, row count — without loading the event data."""

    path = source.path
    suffix = path.suffix.lower()
    columns, n_rows = _schema(path, suffix)
    return {
        "format": "parquet" if suffix == ".parquet" else "hdf5",
        "size_bytes": path.stat().st_size,
        "checksum_sha256": source.checksum(),
        "columns": columns,
        "n_rows": n_rows,
    }


def _schema(path: Path, suffix: str) -> tuple[list[str], int | None]:
    if suffix == ".parquet":
        import pyarrow.parquet as pq

        # pyarrow ships no stubs; these attributes are dynamic
        parquet_file: Any = pq.ParquetFile(path)
        return list(parquet_file.schema_arrow.names), parquet_file.metadata.num_rows
    # HDFStore.get_storer / .nrows are dynamic PyTables surfaces
    store: Any = pd.HDFStore(path, mode="r")
    try:
        keys = store.keys()
        if not keys:
            return [], 0
        key = keys[0]
        head = store.select(key, start=0, stop=0)
        storer = store.get_storer(key)
        n_rows = int(storer.nrows) if storer.nrows is not None else None
        return [str(column) for column in head.columns], n_rows
    finally:
        store.close()


def adhoc_histogram(
    source: UploadDataSource,
    observable: str,
    bins: list[float] | int | None,
    weight_column: str | None,
) -> dict[str, Any]:
    """A weighted histogram of one column of an uploaded file.

    Delegates to the package's ``compute_weighted_histogram`` — the same
    function the parity test calls directly — so no physics is recomputed.
    """

    columns = [observable] + ([weight_column] if weight_column else [])
    frame = source.open_events(columns=columns)
    values = frame[observable].to_numpy(dtype=float)
    weights = (
        frame[weight_column].to_numpy(dtype=float) if weight_column else None
    )
    hist_bins: int | list[float] = 50 if bins is None else bins
    result = compute_weighted_histogram(values, weights, bins=hist_bins)
    payload: dict[str, Any] = to_jsonable(result)
    return payload


def sweep_uploads(upload_dir: Path, ttl_seconds: float) -> list[Path]:
    """Delete abandoned uploads older than the TTL (Phase 1 helper)."""

    removed: list[Path] = _sweep_expired_uploads(
        upload_dir, ttl_seconds=ttl_seconds
    )
    return removed
