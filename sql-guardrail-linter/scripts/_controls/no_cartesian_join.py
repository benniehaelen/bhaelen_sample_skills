"""SQ-007 no_cartesian_join (Tier 2, high).

Every join must have an ON or USING predicate. A CROSS JOIN, or a comma-join
with no compensating predicate, fails. The parser records the offending join
shape in ``cartesian_evidence``; an old-style comma join is tolerated when the
statement carries a column-to-column equality in WHERE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-007"
NAME = "no_cartesian_join"
TIER = 2
SEVERITY = "high"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if stmt.join_count == 0:
        return ControlResult(ID, name, tier, severity, "pass", evidence="no joins")
    if not stmt.cartesian_evidence:
        return ControlResult(ID, name, tier, severity, "pass", evidence="every join has an ON/USING predicate")
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=stmt.cartesian_evidence,
        suggestion="Give every join an explicit ON/USING predicate. Replace CROSS JOIN or comma joins with an equality on the join keys.",
    )
