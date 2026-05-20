"""Tier 1: no boilerplate opener.

Fail if the description starts with a phrase from ``boilerplate_openers``.
Partial if it starts with a phrase from ``soft_boilerplate_openers``.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import count_in, truncate_evidence

NAME = "no_boilerplate_opener"
TIER = 1
MAX_POINTS = 2


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    desc = (tool.get("description") or "").lstrip()
    desc_lower = desc.lower()
    hard = [p.lower() for p in rubric.get("boilerplate_openers", [])]
    soft = [p.lower() for p in rubric.get("soft_boilerplate_openers", [])]

    for phrase in hard:
        if desc_lower.startswith(phrase):
            matched = desc[: len(phrase)]
            return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                                   evidence=f"Opens with {matched!r}.",
                                   tokens_saved=count_in(matched, rubric))
    for phrase in soft:
        if desc_lower.startswith(phrase):
            matched = desc[: len(phrase)]
            return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                                   evidence=f"Opens with mild boilerplate: {matched!r}.",
                                   tokens_saved=count_in(matched, rubric))
    return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                           evidence=truncate_evidence(desc, rubric))
