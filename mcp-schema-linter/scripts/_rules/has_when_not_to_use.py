"""Tier 2: negative guidance when siblings exist.

N/A if no siblings detected (by name overlap or Jaccard description
similarity). Otherwise look for phrases that steer the caller away from
this tool toward a sibling.
"""

from __future__ import annotations

import re
from typing import Any

from . import CriterionResult
from ._helpers import estimated_midpoint, find_name_siblings, find_overlap_siblings

NAME = "has_when_not_to_use"
TIER = 2
MAX_POINTS = 2

_STRONG = (
    re.compile(r"\bdo not use\b", re.IGNORECASE),
    re.compile(r"\bdon'?t use\b", re.IGNORECASE),
    re.compile(r"\bnot for\b", re.IGNORECASE),
    re.compile(r"\binstead use\b", re.IGNORECASE),
    re.compile(r"\buse \w+ instead\b", re.IGNORECASE),
    re.compile(r"\bif you want .+ use\b", re.IGNORECASE),
)
_WEAK = (
    re.compile(r"\binstead\b", re.IGNORECASE),
    re.compile(r"\brather than\b", re.IGNORECASE),
)


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    siblings = find_name_siblings(tool, catalog, rubric) or find_overlap_siblings(tool, catalog, rubric)
    if not siblings:
        return CriterionResult(NAME, TIER, 0, 0, True,
                               evidence="No siblings detected.", n_a=True)

    desc = tool.get("description") or ""
    for pat in _STRONG:
        m = pat.search(desc)
        if m:
            return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                                   evidence=f"Explicit negative guidance: {m.group(0)!r}.")
    cost = estimated_midpoint(rubric, "routing_miss_cost_low", "routing_miss_cost_high")
    for pat in _WEAK:
        m = pat.search(desc)
        if m:
            return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                                   evidence=f"Implicit negative guidance: {m.group(0)!r}.",
                                   tokens_saved=cost, estimated=True)
    sib_names = ", ".join(s.get("name", "") for s in siblings[:3])
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"No negative guidance; sibling(s) present: {sib_names}.",
                           tokens_saved=cost, estimated=True)
