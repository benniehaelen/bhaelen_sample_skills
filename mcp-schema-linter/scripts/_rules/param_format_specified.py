"""Tier 2: date / timestamp / ID / URL parameters declare their format.

Walks the schema for parameters whose names hint at a formatted type
(per ``rubric.format_keywords``, excluding the ``money`` and ``duration``
groups, which are handled by the units rule). For each, checks the JSON
Schema ``format`` keyword and the parameter's own ``description`` for an
explicit format mention.
"""

from __future__ import annotations

import re
from typing import Any

from . import CriterionResult
from ._helpers import estimated_midpoint, iter_parameters

NAME = "param_format_specified"
TIER = 2
MAX_POINTS = 2

_FORMAT_MARKERS = re.compile(
    r"\b(iso\s?8601|rfc\s?3339|rfc\s?2822|uuid|guid|url|uri|epoch|unix\s?time|"
    r"yyyy-?mm-?dd|hh:mm)\b",
    re.IGNORECASE,
)


def _is_applicable(param_name: str, format_keywords: dict[str, Any]) -> bool:
    lower = param_name.lower()
    for group_name, keywords in format_keywords.items():
        if group_name in ("money", "duration"):
            continue
        for kw in keywords:
            if kw in lower:
                return True
    return False


def _states_format(prop: dict[str, Any]) -> bool:
    if not isinstance(prop, dict):
        return False
    fmt = prop.get("format")
    if isinstance(fmt, str) and fmt.strip():
        return True
    return bool(_FORMAT_MARKERS.search(prop.get("description") or ""))


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    schema = tool.get("inputSchema") or {}
    fmt_keywords = rubric.get("format_keywords", {})
    applicable = [(n, p) for n, p in iter_parameters(schema) if _is_applicable(n, fmt_keywords)]
    if not applicable:
        return CriterionResult(NAME, TIER, 0, 0, True,
                               evidence="No date / ID / URL parameters.", n_a=True)
    with_format = [n for n, p in applicable if _states_format(p)]
    cost = estimated_midpoint(rubric, "format_recovery_cost_low", "format_recovery_cost_high")
    if len(with_format) == len(applicable):
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"All {len(applicable)} applicable parameter(s) state a format.")
    if not with_format:
        missing = ", ".join(n for n, _ in applicable[:3])
        return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                               evidence=f"No formats stated; applicable parameter(s): {missing}.",
                               tokens_saved=cost * len(applicable), estimated=True)
    missing_names = [n for n, p in applicable if not _states_format(p)]
    return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                           evidence=f"Some parameter(s) missing format: {', '.join(missing_names[:3])}.",
                           tokens_saved=cost * len(missing_names), estimated=True)
