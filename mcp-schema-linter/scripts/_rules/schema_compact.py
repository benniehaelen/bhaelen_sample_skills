"""Tier 1: input schema compactness band.

Pass at ≤200 schema tokens; partial 200-500; fail above 500. ``tokens_saved``
is the measured excess over the upper bound.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult

NAME = "schema_compact"
TIER = 1
MAX_POINTS = 2


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    t = rubric.get("thresholds", {})
    good_max = int(t.get("schema_tokens_good_max", 200))
    bloated = int(t.get("schema_tokens_bloated", 500))
    schema_tokens = int((tool.get("tokens") or {}).get("schema", 0))

    if schema_tokens <= good_max:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"{schema_tokens} schema tokens; under {good_max}.")
    if schema_tokens <= bloated:
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"{schema_tokens} schema tokens; over the {good_max}-token target.",
                               tokens_saved=schema_tokens - good_max)
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"{schema_tokens} schema tokens; bloated (over {bloated}).",
                           tokens_saved=schema_tokens - good_max)
