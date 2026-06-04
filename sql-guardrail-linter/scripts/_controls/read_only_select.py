"""SQ-001 read_only_select (Tier 1, critical).

The statement must be a single read-only SELECT or WITH ... SELECT. Any
write or DDL/DCL statement (INSERT, UPDATE, DELETE, MERGE, CREATE, DROP,
ALTER, TRUNCATE, GRANT, ...) fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-001"
NAME = "read_only_select"
TIER = 1
SEVERITY = "critical"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if not stmt.ok:
        return ControlResult(ID, name, tier, severity, "fail",
                             evidence=f"statement failed to parse: {stmt.parse_error}",
                             suggestion="Fix the SQL so it parses as a single read-only SELECT.")
    if stmt.is_read_only_select:
        return ControlResult(ID, name, tier, severity, "pass", evidence="single read-only SELECT")
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=f"{stmt.statement_type} statement is not read-only",
        suggestion="Rewrite as a read-only SELECT. Writes and DDL must go through a separate, reviewed path, not the NL2SQL plane.",
    )
