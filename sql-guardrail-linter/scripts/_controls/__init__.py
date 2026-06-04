"""Guardrail control modules for the SQL linter.

Each control module exposes:

- ``ID``: the control id (e.g. ``"SQ-001"``), a key in ``rubric.json``'s
  ``controls`` block.
- ``TIER`` / ``NAME`` / ``SEVERITY``: built-in defaults, overridable per
  control via the rubric's ``controls[ID]`` entry.
- ``check(stmt, ctx, rubric) -> ControlResult``.

``stmt`` is a ``ParsedStatement`` from ``_parser``. ``ctx`` is the per-statement
``LintContext`` carrying the mode, the byte ceiling, the tenant registry, the
PII policy, and any metadata resolved for the referenced tables. ``rubric`` is
the merged rubric config.

A control returns ``status="na"`` when it cannot be resolved in the current
mode (operating rule 4): for example SQ-008 (scan ceiling) is ``na`` in static
mode, and SQ-004 (partition filter) is ``na`` when no referenced table's
partition column is known. The scoring layer drops ``na`` controls from the
denominator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ControlResult:
    """One control's verdict on one statement."""

    id: str
    name: str
    tier: int
    severity: str
    status: str  # "pass" | "warn" | "fail" | "na"
    evidence: str = ""
    suggestion: str = ""
    estimated_bytes_saved: int = 0

    @property
    def n_a(self) -> bool:
        return self.status == "na"


@dataclass
class LintContext:
    """Everything a control needs about the environment for one statement.

    ``table_metadata`` maps a lowercased fully qualified table id to a dict
    with ``partition_field`` (str, empty if none/unknown) and ``pii_columns``
    (a set of lowercased column names). It is empty in static mode and
    populated by the metadata resolver in cost_aware / usage_log modes.
    """

    mode: str = "static"
    max_bytes_ceiling: int = 53687091200
    tenant_registry: dict[str, str] = field(default_factory=dict)
    pii_policy: Any = None  # "policy_tags" | "labels" | dict[str, list[str]] | None
    pii_allowlist: set[str] = field(default_factory=set)
    table_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    estimated_bytes: Optional[int] = None
    bytes_available: bool = False

    @property
    def cost_aware(self) -> bool:
        return self.mode in ("cost_aware", "usage_log")

    def meta_for(self, written_table: str) -> dict[str, Any]:
        """Resolved metadata for a written table name (empty dict if none)."""
        return self.table_metadata.get((written_table or "").lower(), {})


def control_meta(cid: str, rubric: dict[str, Any], *, tier: int, name: str, severity: str) -> tuple[int, str, str]:
    """Resolve a control's (tier, name, severity), letting the rubric override."""
    overrides = (rubric.get("controls") or {}).get(cid, {})
    return (
        int(overrides.get("tier", tier)),
        str(overrides.get("name", name)),
        str(overrides.get("severity", severity)),
    )


def all_controls() -> list[tuple[str, "callable"]]:
    """Return ``[(id, check_fn), ...]`` for every control, in tier order."""
    from . import (
        read_only_select,
        single_statement,
        no_comment_injection,
        partition_filter_present,
        no_select_star,
        bounded_result,
        no_cartesian_join,
        scan_within_ceiling,
        tenant_predicate_present,
        pii_access_justified,
        fully_qualified_tables,
        order_by_bounded,
    )

    modules = [
        # Tier 1: statement safety.
        read_only_select,
        single_statement,
        no_comment_injection,
        # Tier 2: cost and scan control.
        partition_filter_present,
        no_select_star,
        bounded_result,
        no_cartesian_join,
        scan_within_ceiling,
        # Tier 3: governance and hygiene.
        tenant_predicate_present,
        pii_access_justified,
        fully_qualified_tables,
        order_by_bounded,
    ]
    return [(m.ID, m.check) for m in modules]


__all__ = ["ControlResult", "LintContext", "control_meta", "all_controls"]
