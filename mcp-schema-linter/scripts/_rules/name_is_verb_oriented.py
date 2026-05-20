"""Tier 3: tool name begins with an action verb.

Full pass when the first underscore-segment is a recognized verb (read-only
or destructive). Partial when the head segment is nominal-but-unambiguous
(ends in -er / -or / -ist, e.g. ``invoice_creator``). Fail otherwise.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import name_segments

NAME = "name_is_verb_oriented"
TIER = 3
MAX_POINTS = 2

_NOMINAL_SUFFIXES = ("er", "or", "ist")


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    name = tool.get("name", "") or ""
    segs = name_segments(name)
    if not segs:
        return CriterionResult(NAME, TIER, 0, MAX_POINTS, False, evidence="Tool name is empty.")
    head = segs[0]
    verbs = {v.lower() for v in rubric.get("destructive_verbs", []) + rubric.get("read_only_verbs", [])}
    if head in verbs:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"Starts with verb {head!r}.")
    # Nominal-but-unambiguous: an agent-noun segment (e.g. invoice_creator,
    # data_processor) reads as an action even without a leading verb.
    nominal = [s for s in segs if len(s) > 4 and any(s.endswith(suf) for suf in _NOMINAL_SUFFIXES)]
    if nominal:
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"Nominal name via {nominal[0]!r}; consider a leading verb.")
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"Name {name!r} is not verb-oriented.")
