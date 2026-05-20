"""Tier 2: explicit positive guidance on when to pick this tool.

Strong patterns: "use this when", "choose this if", "prefer this for".
Implicit patterns: "if you need", "when you want" (partial credit).
"""

from __future__ import annotations

import re
from typing import Any

from . import CriterionResult
from ._helpers import estimated_midpoint

NAME = "has_when_to_use"
TIER = 2
MAX_POINTS = 2

_STRONG = (
    re.compile(r"\buse (?:this|it) when\b", re.IGNORECASE),
    re.compile(r"\buse when\b", re.IGNORECASE),
    re.compile(r"\bchoose (?:this|it) (?:when|if)\b", re.IGNORECASE),
    re.compile(r"\bcall this when\b", re.IGNORECASE),
    re.compile(r"\bpick (?:this|it) (?:when|if)\b", re.IGNORECASE),
    re.compile(r"\bprefer (?:this|it) (?:when|if|for)\b", re.IGNORECASE),
)
_WEAK = (
    re.compile(r"\bfor when\b", re.IGNORECASE),
    re.compile(r"\bif you (?:need|want)\b", re.IGNORECASE),
    re.compile(r"\bwhen you (?:need|want)\b", re.IGNORECASE),
)


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    desc = tool.get("description") or ""
    for pat in _STRONG:
        m = pat.search(desc)
        if m:
            return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                                   evidence=f"Explicit guidance: {m.group(0)!r}.")
    cost = estimated_midpoint(rubric, "routing_miss_cost_low", "routing_miss_cost_high")
    for pat in _WEAK:
        m = pat.search(desc)
        if m:
            return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                                   evidence=f"Implicit guidance: {m.group(0)!r}.",
                                   tokens_saved=cost, estimated=True)
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence="No when-to-use guidance found in description.",
                           tokens_saved=cost, estimated=True)
