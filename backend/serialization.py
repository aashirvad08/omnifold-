"""JSON-safe conversion of package outputs.

The single most important helper in the backend: ``to_jsonable`` is the
*only* transform applied to physics numbers between a package call and the
wire. It converts numpy arrays/scalars to Python lists/floats and maps
non-finite values (``NaN``, ``+/-Inf``) to ``null`` — JSON has no NaN, and
comparison ratios legitimately produce NaN for zero-denominator bins.

Parity tests apply this exact function to the direct package output too,
so "the API never recomputes physics" reduces to a byte/float-exact
equality check after one shared, lossless-per-convention transform.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def to_jsonable(value: Any) -> Any:
    """Recursively convert numpy/scalar/container values to JSON-safe forms.

    - numpy arrays -> nested lists
    - numpy scalars -> Python scalars
    - non-finite floats (NaN, +/-Inf) -> None
    - dict/list/tuple -> same structure, recursively converted
    """

    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, np.generic):
        return to_jsonable(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
