"""Tier 3: enum parameters list their valid values with semantic glosses.

N/A if no enum parameters. Pass when every enum parameter's values appear
in the description (or its own parameter description) AND at least half
the values carry a gloss (a colon, dash, or parenthesis immediately
following the value). Partial when values are listed without glosses;
fail when the schema constrains values but the description doesn't list
them.
"""

from __future__ import annotations

from typing import Any

from . import CriterionResult
from ._helpers import iter_parameters

NAME = "enum_values_documented"
TIER = 3
MAX_POINTS = 2

_GLOSS_CHARS = (":", "-", "(")


def _values_listed(values: list[Any], haystack: str) -> bool:
    return all(str(v) in haystack for v in values)


def _has_glosses(values: list[Any], haystack: str) -> bool:
    glossed = 0
    for v in values:
        token = str(v)
        idx = haystack.find(token)
        if idx == -1:
            continue
        tail = haystack[idx + len(token): idx + len(token) + 3]
        if any(ch in tail for ch in _GLOSS_CHARS):
            glossed += 1
    return glossed >= max(1, len(values) // 2)


def check(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> CriterionResult:
    enum_params = [
        (n, p) for n, p in iter_parameters(tool.get("inputSchema") or {})
        if isinstance(p.get("enum"), list) and p["enum"]
    ]
    if not enum_params:
        return CriterionResult(NAME, TIER, 0, 0, True, evidence="No enum parameters.", n_a=True)

    desc = tool.get("description") or ""
    full = desc + "\n" + "\n".join((p.get("description") or "") for _n, p in enum_params)

    glossed_all = all(_has_glosses(p["enum"], full) for _n, p in enum_params)
    listed_all = all(_values_listed(p["enum"], full) for _n, p in enum_params)

    if listed_all and glossed_all:
        return CriterionResult(NAME, TIER, MAX_POINTS, MAX_POINTS, True,
                               evidence=f"All {len(enum_params)} enum parameter(s) documented with glosses.")
    if listed_all:
        return CriterionResult(NAME, TIER, 1, MAX_POINTS, False,
                               evidence=f"{len(enum_params)} enum(s) list values; glosses missing.")
    return CriterionResult(NAME, TIER, 0, MAX_POINTS, False,
                           evidence=f"{len(enum_params)} enum parameter(s) with values not documented.")
