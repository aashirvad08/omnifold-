"""Data-access layer: one interface, many backends.

Every source implements :class:`DataSource`; calling code reads events
through it without knowing where the file lives. Use :func:`get_source`
to build one from a prefixed spec string.
"""

from __future__ import annotations

from pathlib import Path

from .base import DataSource, DataSourceError
from .local import LocalDataSource
from .upload import UploadDataSource, sweep_expired_uploads


_PREFIX_SOURCES: dict[str, type[DataSource]] = {
    "local": LocalDataSource,
    "upload": UploadDataSource,
}


def get_source(spec: str, **kwargs) -> DataSource:
    """Build a data source from a prefixed spec string.

    ``"local:<path>"`` and ``"upload:<stored path>"`` dispatch on the
    prefix; a bare path of an existing file defaults to
    :class:`LocalDataSource`.
    """

    prefix, _, remainder = spec.partition(":")
    if remainder and prefix in _PREFIX_SOURCES:
        return _PREFIX_SOURCES[prefix](remainder, **kwargs)

    if Path(spec).is_file():
        return LocalDataSource(spec, **kwargs)

    known = ", ".join(sorted(_PREFIX_SOURCES))
    raise DataSourceError(
        f"Cannot resolve data source spec {spec!r}: not a known prefix "
        f"({known}) and not an existing file."
    )


__all__ = [
    "DataSource",
    "DataSourceError",
    "LocalDataSource",
    "UploadDataSource",
    "get_source",
    "sweep_expired_uploads",
]
