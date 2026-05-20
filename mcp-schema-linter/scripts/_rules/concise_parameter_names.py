"""Tier 1: parameter names within the length budget.

A name is over budget when it exceeds ``parameter_name_chars_max`` OR
``parameter_name_tokens_max``. Pass when none are over; partial at one;
fail at two or more.

``tokens_saved`` is estimated as ``(tokens(name) - 3) * 2`` summed across
the over-budget names. The ×2 reflects that names appear in both the
schema and every tool-call argument list.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import count_in, iter_parameters

NAME = "concise_parameter_names"
TIER = 1
MAX_POINTS = 2

_GOOD_NAME_TOKENS = 3  # heuristic baseline for "concise"


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    t = rubric.get("thresholds", {})
    chars_max = int(t.get("parameter_name_chars_max", 25))
    tokens_max = int(t.get("parameter_name_tokens_max", 6))

    over: list[str] = []
    for name, _prop in iter_parameters(tool.get("inputSchema") or {}):
        if len(name) > chars_max or count_in(name, rubric) > tokens_max:
            over.append(name)

    if not over:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence="All parameter names within the length budget.")

    savings = sum(max(0, count_in(n, rubric) - _GOOD_NAME_TOKENS) * 2 for n in over)
    if len(over) == 1:
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"Over-long parameter name: {over[0]!r}.",
                               tokens_saved=savings)
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"Over-long names: {', '.join(over[:3])}.",
                           tokens_saved=savings)
