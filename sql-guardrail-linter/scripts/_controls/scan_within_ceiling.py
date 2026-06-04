"""SQ-008 scan_within_ceiling (Tier 2, high, conditional).

The dry-run estimate must be within ``max_bytes_ceiling``. ``na`` in static
mode, where no dry-run is available. The byte figure comes from a BigQuery
dry-run (``dry_run: true``); the statement under audit is never executed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta
from _serialize import bytes_human

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-008"
NAME = "scan_within_ceiling"
TIER = 2
SEVERITY = "high"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    if not ctx.bytes_available or ctx.estimated_bytes is None:
        return ControlResult(ID, name, tier, severity, "na",
                             evidence="no dry-run estimate (static mode)")

    estimated = int(ctx.estimated_bytes)
    ceiling = int(ctx.max_bytes_ceiling)
    if estimated <= ceiling:
        return ControlResult(ID, name, tier, severity, "pass",
                             evidence=f"{bytes_human(estimated)} within ceiling {bytes_human(ceiling)}")
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=f"{bytes_human(estimated)} exceeds ceiling {bytes_human(ceiling)}",
        suggestion="Prune the scan: add a partition filter, select fewer columns, or narrow the date range so the dry-run estimate drops below the ceiling.",
        estimated_bytes_saved=max(estimated - ceiling, 0),
    )
