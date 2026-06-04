"""SQ-005 no_select_star (Tier 2, medium).

Columns are enumerated; no ``SELECT *`` or ``SELECT t.*``. A star projection
both bloats the result and prevents column-level governance (PII controls
cannot reason about which columns are returned).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-005"
NAME = "no_select_star"
TIER = 2
SEVERITY = "medium"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if stmt.select_star:
        return ControlResult(
            ID, name, tier, severity, "fail",
            evidence="projection uses SELECT * / t.*",
            suggestion="Enumerate the columns the caller needs instead of '*'. This caps result width and lets the PII control see what is returned.",
        )
    return ControlResult(ID, name, tier, severity, "pass", evidence="columns are enumerated")
