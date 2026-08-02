"""Citation builder — a total function over package metadata.

Composes a BibLaTeX entry from metadata alone (the package has no citation
facility, and this carries no physics numbers so the parity invariant does
not apply). Following the sibling project's established split:

- a real DOI present -> ``@dataset`` (matching Zenodo's own export format),
  with the DOI as the identifier;
- no DOI -> ``@misc`` with explicit ``howpublished``/``note`` and the
  content SHA-256 as the reproducibility anchor.

"No DOI" is treated as data, not an error: this always returns a citation
(HTTP 200), never a partial or error response.
"""

from __future__ import annotations

from typing import Any


def _find_doi(metadata: dict[str, Any]) -> str | None:
    for location in (
        metadata.get("doi"),
        (metadata.get("dataset") or {}).get("doi")
        if isinstance(metadata.get("dataset"), dict)
        else None,
        (metadata.get("publication") or {}).get("doi")
        if isinstance(metadata.get("publication"), dict)
        else None,
    ):
        if isinstance(location, str) and location.strip():
            return location.strip()
    return None


def _title(metadata: dict[str, Any], resource_id: str) -> str:
    dataset = metadata.get("dataset")
    if isinstance(dataset, dict):
        name = dataset.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return f"OmniFold publication package {resource_id}"


def build_citation(
    resource_id: str,
    metadata: dict[str, Any],
    checksum: str | None,
    source: str,
    checksum_kind: str = "package",
) -> dict[str, Any]:
    doi = _find_doi(metadata)
    title = _title(metadata, resource_id)
    version = metadata.get("format_version")
    fields: list[tuple[str, str]] = [("title", title)]

    if doi is not None:
        entry_type = "dataset"
        fields.append(("doi", doi))
        fields.append(("url", f"https://doi.org/{doi}"))
    else:
        entry_type = "misc"
        fields.append(("howpublished", f"OmniFold publication explorer ({source})"))

    if isinstance(version, str):
        fields.append(("version", version))
    if checksum:
        # the integrity anchor: cite exactly the bytes computed on. A package
        # checksum is a real file hash; an analysis composite is a synthetic
        # backend aggregate and is labelled as such so the two never conflate.
        if checksum_kind == "analysis_composite":
            note = f"sha256 (backend-derived analysis composite):{checksum}"
        else:
            note = f"sha256:{checksum}"
        fields.append(("note", note))

    body = ",\n".join(f"  {key} = {{{value}}}" for key, value in fields)
    citation = f"@{entry_type}{{{resource_id},\n{body}\n}}"
    return {
        "resource_id": resource_id,
        "format": "biblatex",
        "entry_type": entry_type,
        "has_doi": doi is not None,
        "citation": citation,
    }
