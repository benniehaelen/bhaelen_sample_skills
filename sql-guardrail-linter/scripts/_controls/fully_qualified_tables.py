"""SQ-011 fully_qualified_tables (Tier 3, low).

Every base table must be written as ``project.dataset.table``. Unqualified or
two-part names fail, because a missing project/dataset can resolve against the
wrong zone and bypass routing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta
from ._helpers import join_names

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-011"
NAME = "fully_qualified_tables"
TIER = 3
SEVERITY = "low"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if not stmt.tables:
        return ControlResult(ID, name, tier, severity, "na", evidence="no base tables referenced")

    underqualified = [t.written for t in stmt.tables if not t.is_fully_qualified]
    if not underqualified:
        return ControlResult(ID, name, tier, severity, "pass", evidence="all base tables fully qualified")
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=f"under-qualified table(s): {join_names(underqualified)}",
        suggestion="Write every base table as project.dataset.table so zone routing cannot be bypassed.",
    )
