"""Tier 1: examples appear in exactly one place.

Walks the schema's ``examples`` and ``default`` keys (top-level and per
property). Fails if any example string longer than 4 chars appears
verbatim in the description.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import count_in, iter_parameters

NAME = "no_redundant_examples"
TIER = 1
MAX_POINTS = 2


def _collect_examples(schema: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if not isinstance(schema, dict):
        return out
    top = schema.get("examples")
    if isinstance(top, list):
        out.extend(str(e) for e in top)
    if "default" in schema:
        out.append(str(schema["default"]))
    for _name, prop in iter_parameters(schema):
        sub = prop.get("examples")
        if isinstance(sub, list):
            out.extend(str(e) for e in sub)
        if "default" in prop:
            out.append(str(prop["default"]))
    return out


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    desc = tool.get("description") or ""
    examples = _collect_examples(tool.get("inputSchema") or {})
    if not examples:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence="No schema examples to duplicate.")
    duplicated = [e for e in examples if e and len(e) > 4 and e in desc]
    if not duplicated:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"{len(examples)} example(s) in schema, not duplicated in description.")
    saved = sum(count_in(e, rubric) for e in duplicated)
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"Example(s) appear in both description and schema: {duplicated[0]!r}.",
                           tokens_saved=saved)
