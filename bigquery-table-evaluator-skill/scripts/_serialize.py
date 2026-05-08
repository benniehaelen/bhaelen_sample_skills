"""JSON-safe serialization and human-readable formatting helpers.

Pure functions, no third-party dependencies. Imported by ``_render``,
``_expectations``, ``evaluate_bigquery_table``, and ``render_report``.
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
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, tuple):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}
    return value


def bytes_human(value: int | None) -> str:
    """Render a byte count as a human-readable string (``"852.13 MiB"``).

    Uses binary units (KiB / MiB / ...) since BigQuery's billing is in
    binary bytes. Returns ``"unknown"`` for ``None``.
    """
    if value is None:
        return "unknown"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(value)
    for unit in units:
        if abs(size) < 1024.0:
            return f"{size:,.2f} {unit}"
        size /= 1024.0
    return f"{size:,.2f} EiB"


def short_int(n: int) -> str:
    """Format large integers compactly: 1234567 -> '1.2M'."""
    n = int(n)
    if n < 1000:
        return f"{n}"
    for limit, suffix in ((1_000_000, "K"), (1_000_000_000, "M"), (1_000_000_000_000, "B"), (None, "T")):
        if limit is None or n < limit:
            divisor = limit // 1000 if limit else 1_000_000_000
            value = n / divisor
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{n:.0f}"


def humanize_seconds(secs: int) -> str:
    """Render a duration in seconds as a compact ``45s`` / ``2m`` / ``3h`` / ``5d`` string."""
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def format_datetime(value: Any, *, with_relative: bool = True) -> str:
    """Render an ISO-ish timestamp as 'May 3, 2026 11:30 UTC (3h ago)'.

    Returns ``"—"`` for ``None`` / empty input. Falls back to the original string
    if the value cannot be parsed as ISO 8601.
    """
    if value in (None, ""):
        return "—"
    text = str(value)
    parsed: dt.datetime | None = None
    for candidate in (text, text.replace("Z", "+00:00"), f"{text}T00:00:00"):
        try:
            parsed = dt.datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if parsed is None:
        return text
    abs_str = parsed.strftime("%b %d, %Y %H:%M").replace(" 0", " ", 1)
    offset = parsed.utcoffset()
    if offset == dt.timedelta(0):
        abs_str += " UTC"
    elif offset is not None:
        raw = parsed.strftime("%z")  # e.g. "+0200"
        abs_str += f" {raw[:3]}:{raw[3:]}" if len(raw) == 5 else f" {raw}"
    if not with_relative:
        return abs_str
    if parsed.tzinfo:
        now = dt.datetime.now(parsed.tzinfo)
    else:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    delta = now - parsed
    secs = int(delta.total_seconds())
    if abs(secs) < 1:
        rel = "just now"
    elif secs > 0:
        rel = f"{humanize_seconds(secs)} ago"
    else:
        rel = f"in {humanize_seconds(-secs)}"
    return f"{abs_str} ({rel})"
