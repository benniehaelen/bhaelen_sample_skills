"""SQ-012 order_by_bounded (Tier 3, low).

An ORDER BY should be paired with a LIMIT. Sorting an unbounded result set is
wasted work: the caller almost never consumes the full ordered stream, and the
sort forces a full materialization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-012"
NAME = "order_by_bounded"
TIER = 3
SEVERITY = "low"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if not stmt.has_order_by:
        return ControlResult(ID, name, tier, severity, "na", evidence="no ORDER BY")
    if stmt.has_limit:
        return ControlResult(ID, name, tier, severity, "pass", evidence="ORDER BY paired with LIMIT")
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence="ORDER BY without LIMIT",
        suggestion="Pair the ORDER BY with a LIMIT, or drop the ORDER BY if the caller does not need a sorted top-N.",
    )
