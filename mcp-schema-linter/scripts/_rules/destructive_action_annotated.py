"""Tier 2: mutating tools carry MCP annotations and explicit description language.

N/A for read-only tools. Mutating classification uses the rubric's
``destructive_verbs`` / ``read_only_verbs`` lists. ``tokens_saved`` is 0
because this criterion is graded for catalog correctness, not token
recovery; the rubric tags it ``safety_critical`` so the scoring layer
can surface it regardless of points.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import is_mutating

NAME = "destructive_action_annotated"
TIER = 2
MAX_POINTS = 2


def _has_destructive_annotation(annotations: dict[str, Any]) -> bool:
    if not isinstance(annotations, dict):
        return False
    if annotations.get("destructiveHint") is True:
        return True
    # readOnlyHint=False + an explicit idempotency hint also counts as
    # "the author has thought about state mutation."
    if annotations.get("readOnlyHint") is False and annotations.get("idempotentHint") is not None:
        return True
    return False


def _description_calls_out_mutation(tool: dict[str, Any], rubric: dict[str, Any]) -> bool:
    desc = (tool.get("description") or "").lower()
    head = desc[:200]
    for verb in rubric.get("destructive_verbs", []):
        if verb.lower() in head:
            return True
    return False


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    if not is_mutating(tool, rubric):
        return CriterionResult(NAME, TIER, 0, 0, True, evidence="Read-only tool.", n_a=True)
    has_annot = _has_destructive_annotation(tool.get("annotations") or {})
    has_desc = _description_calls_out_mutation(tool, rubric)
    if has_annot and has_desc:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence="Annotation flagged AND description calls out the mutation.")
    if has_annot or has_desc:
        which = "annotation only" if has_annot else "description only"
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"Mutating tool flagged in {which}.")
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence="Mutating tool with no annotation and no description mention of the mutation.")
