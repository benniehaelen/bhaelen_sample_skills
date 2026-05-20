"""Tier 3: numeric parameters declare units when their name is ambiguous.

Applies to integer / number parameters whose names contain a keyword from
the ``money`` or ``duration`` groups in ``rubric.format_keywords``. Pass
when every applicable parameter's description includes a unit marker.
"""

from __future__ import annotations

import re
from typing import Any

from . import CriterionResult
from ._helpers import iter_parameters

NAME = "units_specified"
TIER = 3
MAX_POINTS = 2

_UNIT_MARKERS = re.compile(
    r"\b(usd|eur|gbp|jpy|cents|dollars|euros|milliseconds?|seconds?|minutes?|hours?|days?|"
    r"weeks?|months?|years?|bytes?|kb|mb|gb|ms|sec|min)\b",
    re.IGNORECASE,
)


def _is_applicable(name: str, prop: dict[str, Any], fmt_keywords: dict[str, Any]) -> bool:
    if not isinstance(prop, dict):
        return False
    if prop.get("type") not in ("integer", "number"):
        return False
    lower = name.lower()
    for grp in ("money", "duration"):
        for kw in fmt_keywords.get(grp, []):
            if kw in lower:
                return True
    return False


def _states_units(prop: dict[str, Any]) -> bool:
    return bool(_UNIT_MARKERS.search((prop.get("description") if isinstance(prop, dict) else "") or ""))


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    schema = tool.get("inputSchema") or {}
    fmt = rubric.get("format_keywords", {})
    applicable = [(n, p) for n, p in iter_parameters(schema) if _is_applicable(n, p, fmt)]
    if not applicable:
        return CriterionResult(NAME, TIER, 0, 0, True,
                               evidence="No numeric parameters with ambiguous units.", n_a=True)
    stated = [n for n, p in applicable if _states_units(p)]
    if len(stated) == len(applicable):
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"All {len(applicable)} numeric parameter(s) state units.")
    missing = [n for n, p in applicable if not _states_units(p)]
    if not stated:
        return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                               evidence=f"No units stated for: {', '.join(missing[:3])}.")
    return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                           evidence=f"Some parameter(s) missing units: {', '.join(missing[:3])}.")
