"""SQ-004 partition_filter_present (Tier 2, high, conditional).

A query reading a partitioned table must constrain the partition column in
WHERE. Conditional on a referenced table being partitioned. ``na`` when no
referenced table is partitioned or the partition column is unknown (for
example in static mode, where no metadata is resolved).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-004"
NAME = "partition_filter_present"
TIER = 2
SEVERITY = "high"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)

    partitioned: list[tuple[str, str]] = []  # (written_table, partition_field)
    for tbl in stmt.tables:
        field = (ctx.meta_for(tbl.written).get("partition_field") or "").strip()
        if field:
            partitioned.append((tbl.written, field))

    if not partitioned:
        return ControlResult(ID, name, tier, severity, "na",
                             evidence="no referenced table is partitioned, or partition column unknown")

    unconstrained = [(t, f) for (t, f) in partitioned if not stmt.where_constrains(f)]
    if not unconstrained:
        fields = ", ".join(sorted({f for _t, f in partitioned}))
        return ControlResult(ID, name, tier, severity, "pass",
                             evidence=f"partition column constrained ({fields})")

    fields = ", ".join(sorted({f for _t, f in unconstrained}))
    saved = int(ctx.estimated_bytes) if (ctx.bytes_available and ctx.estimated_bytes) else 0
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=f"no WHERE predicate on partition column ({fields})",
        suggestion=f"Add a partition filter on {fields} (for example `WHERE {fields.split(',')[0].strip()} >= @from_date`) to prune the scan.",
        estimated_bytes_saved=saved,
    )
