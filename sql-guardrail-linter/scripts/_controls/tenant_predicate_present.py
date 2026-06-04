"""SQ-009 tenant_predicate_present (Tier 3, critical, conditional).

A query reading a tenant-scoped or zoned table must constrain the tenant
column named in ``tenant_registry``. ``na`` for tables not in the registry.
The registry is a static input, so this control resolves in every mode,
including static.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta
from ._helpers import join_names

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-009"
NAME = "tenant_predicate_present"
TIER = 3
SEVERITY = "critical"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    registry = ctx.tenant_registry or {}

    scoped: list[tuple[str, str]] = []  # (written_table, tenant_column)
    for tbl in stmt.tables:
        col = registry.get(tbl.written)
        if col:
            scoped.append((tbl.written, col))

    if not scoped:
        return ControlResult(ID, name, tier, severity, "na",
                             evidence="no referenced table is in the tenant registry")

    unconstrained_cols = sorted({c for _t, c in scoped if not stmt.where_constrains(c)})
    if not unconstrained_cols:
        constrained = sorted({c for _t, c in scoped})
        return ControlResult(ID, name, tier, severity, "pass",
                             evidence=f"tenant predicate present ({', '.join(constrained)})")

    cols = join_names(unconstrained_cols)
    first = unconstrained_cols[0]
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=f"no predicate on tenant column ({cols})",
        suggestion=f"Add a zone filter on the tenant column, for example `AND {first} = @caller_{first}`.",
    )
