"""SQ-006 bounded_result (Tier 2, medium).

A non-aggregating query should carry a LIMIT, or be an aggregation. A pure
scan with no bound is the one ``warn`` case in the catalog: it is not a hard
block, but an unbounded row stream is a cost and exfiltration risk worth
flagging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-006"
NAME = "bounded_result"
TIER = 2
SEVERITY = "medium"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if stmt.has_limit:
        return ControlResult(ID, name, tier, severity, "pass", evidence="result bounded by LIMIT")
    if stmt.is_aggregation:
        return ControlResult(ID, name, tier, severity, "pass", evidence="aggregating query (bounded result shape)")
    return ControlResult(
        ID, name, tier, severity, "warn",
        evidence="non-aggregating query has no LIMIT",
        suggestion="Add a LIMIT to bound the row stream, or aggregate. An unbounded scan returns the whole table.",
    )
