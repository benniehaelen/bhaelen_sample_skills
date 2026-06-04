"""JSON-safe serialization and human-readable formatting helpers.

Pure functions, no third-party dependencies. Duplicated across skills in this
repo by the self-containment convention (see CLAUDE.md) so the renderer and
scoring modules can run without the others installed.
"""

from __future__ import annotations

import datetime as dt
import decimal
from typing import Any


def serialize(value: Any) -> Any:
    """Convert arbitrary Python values into JSON-safe structures."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, set):
        return sorted(serialize(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}
    return value


def bytes_human(value: int | None) -> str:
    """Render a byte count as a human string (``"38.37 GiB"``).

    Uses binary units (KiB / MiB / ...) since BigQuery bills in binary bytes.
    Returns ``"n/a"`` for ``None``.
    """
    if value is None:
        return "n/a"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(value)
    for unit in units:
        if abs(size) < 1024.0:
            return f"{size:,.2f} {unit}"
        size /= 1024.0
    return f"{size:,.2f} EiB"
