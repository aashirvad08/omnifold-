"""Export builders: HEPData submission bundle and package download tar.

Both stream from disk (memory-bounded): the tar is written to a temp file
block-by-block and the response streams that file — no whole file is held
in memory. The download builder reuses the registry's symlink-resolution
+ containment check so a symlink planted inside a package cannot pull an
outside file into the tarball.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path
from typing import Any

from .registry import _is_within


def hepdata_submission_tar(
    package: Any, work_dir: Path, name: str
) -> Path:
    """Write the package's HEPData submission and tar it (gzip).

    Wraps the package's own ``export_hepdata`` (submission.yaml + per-table
    YAMLs); nothing about the HEPData format is reimplemented here.
    """

    submission_dir = work_dir / "hepdata"
    package.export_hepdata(submission_dir)
    dest = work_dir / f"{name}_hepdata.tar.gz"
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(submission_dir, arcname="hepdata")
    return dest


def package_download_tar(root: Path, dest: Path) -> Path:
    """Tar a resource directory, excluding any entry that escapes ``root``.

    ``os.walk(followlinks=False)`` never descends into symlinked
    directories; each file is additionally resolved and containment-checked
    (via the registry's :func:`_is_within`), so a planted symlink to an
    outside file is skipped rather than dereferenced into the archive.
    """

    root = root.resolve()
    with tarfile.open(dest, "w:gz") as tar:
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for filename in filenames:
                path = Path(dirpath) / filename
                if not _is_within(path, root):
                    continue  # symlink (or entry) resolving outside the root
                arcname = path.relative_to(root.parent)
                tar.add(path, arcname=str(arcname), recursive=False)
    return dest
