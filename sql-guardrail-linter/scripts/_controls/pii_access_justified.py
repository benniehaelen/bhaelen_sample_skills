"""SQ-010 pii_access_justified (Tier 3, critical, conditional).

No restricted (PII / PHI) column may be projected unless permitted by
``pii_allowlist``. Conditional on the projection actually touching a
restricted column.

Restricted columns are identified per ``pii_policy``:

- a JSON map of table -> restricted columns (available in every mode),
- ``policy_tags`` or ``labels`` (resolved from BigQuery metadata, so
  cost_aware / usage_log only; pattern names from the rubric act as a
  fallback for columns the metadata misses).

The control is ``na`` when no policy is configured, when the policy cannot be
resolved in the current mode, or when the projection touches no restricted
column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta
from ._helpers import columns_matching, compiled_patterns, join_names

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-010"
NAME = "pii_access_justified"
TIER = 3
SEVERITY = "critical"


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    policy = ctx.pii_policy

    if policy is None:
        return ControlResult(ID, name, tier, severity, "na", evidence="no PII policy configured")

    explicit_map = policy if isinstance(policy, dict) else None
    tag_based = policy in ("policy_tags", "labels")

    resolvable = explicit_map is not None or (tag_based and ctx.cost_aware)
    if not resolvable:
        return ControlResult(ID, name, tier, severity, "na",
                             evidence=f"pii_policy {policy!r} requires cost_aware/metadata to resolve")

    # Build the set of restricted column names relevant to the referenced tables.
    restricted: set[str] = set()
    lower_map = (
        {str(k).lower(): [str(c).lower() for c in (v or [])] for k, v in explicit_map.items()}
        if explicit_map is not None else {}
    )
    for tbl in stmt.tables:
        if lower_map:
            restricted.update(lower_map.get(tbl.written.lower(), []))
        restricted.update(str(c).lower() for c in ctx.meta_for(tbl.written).get("pii_columns", []))

    if tag_based:
        patterns = compiled_patterns(rubric, "pii_column_patterns")
        restricted.update(columns_matching(stmt.projected_columns, patterns))

    if not restricted:
        return ControlResult(ID, name, tier, severity, "na",
                             evidence="no restricted column known for the referenced tables")

    if stmt.select_star:
        touched = set(restricted)
    else:
        touched = restricted & stmt.projected_columns

    if not touched:
        return ControlResult(ID, name, tier, severity, "na",
                             evidence="projection touches no restricted column")

    allow = {a.lower() for a in (ctx.pii_allowlist or set())}
    unjustified = sorted(touched - allow)
    if not unjustified:
        return ControlResult(ID, name, tier, severity, "pass",
                             evidence=f"restricted column access is allowlisted ({join_names(sorted(touched))})")

    via_star = " via SELECT *" if stmt.select_star else ""
    return ControlResult(
        ID, name, tier, severity, "fail",
        evidence=f"restricted column projected{via_star}: {join_names(unjustified)}",
        suggestion=f"Drop the restricted column(s) {join_names(unjustified)}, or add the caller/column to pii_allowlist if the access is justified.",
    )
