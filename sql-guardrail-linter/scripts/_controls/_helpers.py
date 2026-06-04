"""Shared helpers for control modules. Pure functions, no third-party deps."""

from __future__ import annotations

import re
from typing import Any, Iterable


def compiled_patterns(rubric: dict[str, Any], key: str) -> list[re.Pattern[str]]:
    """Compile a rubric list of regex strings, skipping any that fail to compile."""
    out: list[re.Pattern[str]] = []
    for raw in rubric.get(key, []) or []:
        try:
            out.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    return out


def match_any(text: str, patterns: Iterable[re.Pattern[str]]) -> str:
    """Return the first matched substring (for evidence), or empty string."""
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            return m.group(0)
    return ""


def columns_matching(columns: Iterable[str], patterns: list[re.Pattern[str]]) -> list[str]:
    """Column names matching any of the patterns, de-duplicated, order-stable."""
    seen: set[str] = set()
    out: list[str] = []
    for col in columns:
        low = (col or "").lower()
        if low in seen:
            continue
        if any(p.search(low) for p in patterns):
            seen.add(low)
            out.append(low)
    return out


def join_names(names: Iterable[str], limit: int = 4) -> str:
    """Render a short, comma-joined preview of names with an ellipsis tail."""
    items = list(names)
    head = items[:limit]
    text = ", ".join(head)
    if len(items) > limit:
        text += ", ..."
    return text
