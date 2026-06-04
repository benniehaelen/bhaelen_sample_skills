"""SQ-002 single_statement (Tier 1, critical).

Exactly one statement. Stacked statements or a trailing separator that
introduces a second statement fail. A single ``--sql`` string and each
usage-log row are treated as one candidate, so this control catches an
attacker stacking ``SELECT 1; DROP TABLE x`` into one field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-002"
NAME = "single_statement"
TIER = 1
SEVERITY = "critical"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if not stmt.ok:
        return ControlResult(ID, name, tier, severity, "fail",
                             evidence=f"statement failed to parse: {stmt.parse_error}",
                             suggestion="Submit exactly one parseable statement.")
    if stmt.statement_count <= 1:
        return ControlResult(ID, name, tier, severity, "pass", evidence="exactly one statement")
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=f"{stmt.statement_count} statements in one input (stacked statements)",
        suggestion="Submit a single statement. Stacked statements separated by ';' are rejected by the control plane.",
    )
