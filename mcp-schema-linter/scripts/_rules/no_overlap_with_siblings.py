"""Tier 2: overlapping siblings have distinguishing language.

Counts pairs of tools whose descriptions exceed the siblings-similarity
threshold AND whose own description does not name the other tool or
contain explicit contrast vocabulary.
"""

from __future__ import annotations

import re
from typing import Any

from . import CriterionResult
from ._helpers import estimated_midpoint, find_overlap_siblings

NAME = "no_overlap_with_siblings"
TIER = 2
MAX_POINTS = 2

_DISTINGUISH = re.compile(
    r"\b(?:unlike|as opposed to|in contrast to|differs? from|different from)\b",
    re.IGNORECASE,
)


def _names_distinguish(my_desc: str, other_name: str) -> bool:
    if not other_name:
        return False
    return other_name.lower() in my_desc.lower()


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    overlapping = find_overlap_siblings(tool, catalog, rubric)
    if not overlapping:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence="No overlapping siblings detected.")
    my_desc = tool.get("description") or ""
    has_explicit = bool(_DISTINGUISH.search(my_desc))
    undistinguished = [
        o for o in overlapping
        if not has_explicit and not _names_distinguish(my_desc, o.get("name", ""))
    ]
    if not undistinguished:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"{len(overlapping)} sibling(s) referenced or distinguished.")
    sib_names = ", ".join(o.get("name", "") for o in undistinguished[:3])
    points = 1 if len(undistinguished) == 1 else 0
    saved = estimated_midpoint(rubric, "overlap_cost_low", "overlap_cost_high") * len(undistinguished)
    return CriterionResult(NAME, TIER, points, MAX_POINTS, False,
                           evidence=f"Undistinguished overlap with: {sib_names}.",
                           tokens_saved=saved, estimated=True)
