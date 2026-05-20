"""Tier 3: every parameter has a non-empty description.

Pass when all top-level parameters carry a description. Partial when one
is missing; fail when two or more are missing. No-parameter tools pass
vacuously.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import iter_parameters

NAME = "every_param_has_description"
TIER = 3
MAX_POINTS = 2


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    params = list(iter_parameters(tool.get("inputSchema") or {}))
    if not params:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True, evidence="No parameters.")
    missing = [n for n, p in params if not (p.get("description") or "").strip()]
    if not missing:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"All {len(params)} parameter(s) described.")
    if len(missing) == 1:
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"Parameter {missing[0]!r} has no description.")
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"{len(missing)} parameter(s) without description: {', '.join(missing[:3])}.")
