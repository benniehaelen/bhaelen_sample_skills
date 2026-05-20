"""Tier 3: no near-synonymous sibling tool names in the catalog.

Uses the rubric's ``near_synonym_pairs`` groups: a tool is non-unique if
another tool shares the same non-verb segment with a synonymous verb
(e.g. ``get_user`` and ``fetch_user``).
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import find_name_siblings

NAME = "name_is_unique_in_catalog"
TIER = 3
MAX_POINTS = 2


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    siblings = find_name_siblings(tool, catalog, rubric)
    if not siblings:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence="No near-synonymous tools in catalog.")
    names = ", ".join(s.get("name", "") for s in siblings[:3])
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"Near-synonymous with: {names}.")
