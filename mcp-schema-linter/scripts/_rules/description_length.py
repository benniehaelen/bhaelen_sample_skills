"""Tier 1: description token-length band.

Pass at 20-150 tokens; partial at 10-20 or 150-300; fail outside.
``tokens_saved`` is the measured excess over the upper bound; zero when
the description is under-described (the cost there is router quality,
not catalog size).
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult

NAME = "description_length_appropriate"
TIER = 1
MAX_POINTS = 2


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    t = rubric.get("thresholds", {})
    good_min = int(t.get("description_tokens_min", 20))
    good_max = int(t.get("description_tokens_good_max", 150))
    bloated = int(t.get("description_tokens_bloated", 300))
    desc_tokens = int((tool.get("tokens") or {}).get("description", 0))
    partial_low = good_min // 2  # implied 10 when good_min is 20

    if good_min <= desc_tokens <= good_max:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"{desc_tokens} tokens; in the {good_min}-{good_max} band.")
    if partial_low <= desc_tokens < good_min:
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"{desc_tokens} tokens; under the {good_min}-token sweet spot.")
    if good_max < desc_tokens <= bloated:
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"{desc_tokens} tokens; over the {good_max}-token sweet spot.",
                               tokens_saved=desc_tokens - good_max)
    if desc_tokens > bloated:
        return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                               evidence=f"{desc_tokens} tokens; bloated (over {bloated}).",
                               tokens_saved=desc_tokens - good_max)
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"{desc_tokens} tokens; severely under-described.")
