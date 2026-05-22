"""JSON renderer.

Serializes a Scorecard to a stable JSON document. This is the output CI
consumes, so the shape is treated as a contract: additive changes are fine,
removals or renames are a breaking change and require bumping SCHEMA_VERSION.
The companion scripts/output_schema.json documents the shape for consumers.

The top level surfaces the score, grade, per-tier scores, a summary, the
critical findings, the ranked top fixes, and every check result with its full
evidence. Store, run, and rubric provenance flow through the scorecard metadata,
which is passed through under the metadata key and partially surfaced at the top
level for convenience.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np

from rubric import Rubric
from scoring import Scorecard

SCHEMA_VERSION = "1.0"


def render(scorecard: Scorecard, indent: int = 2, theme: str | None = None) -> str:
    """Render a Scorecard to a JSON string with a trailing newline.

    theme is accepted for a uniform renderer signature and ignored.
    """
    del theme
    return json.dumps(to_dict(scorecard), indent=indent, default=_json_default, ensure_ascii=False) + "\n"


def to_dict(scorecard: Scorecard) -> dict[str, Any]:
    """Build the JSON-serializable dictionary for a Scorecard."""
    rubric = scorecard.rubric
    failing = [r for r in scorecard.results if not r.passed]
    failing_by_severity: dict[str, int] = {}
    for result in failing:
        failing_by_severity[result.severity] = failing_by_severity.get(result.severity, 0) + 1

    scored_at = scorecard.metadata.get("scored_at") or _now()

    return {
        "schema_version": SCHEMA_VERSION,
        "scored_at": scored_at,
        "overall_score": scorecard.overall_score,
        "grade": scorecard.grade,
        "tier_scores": dict(scorecard.tier_scores),
        "summary": {
            "criteria_evaluated": len(scorecard.results),
            "passed": sum(1 for r in scorecard.results if r.passed),
            "failing_by_severity": failing_by_severity,
        },
        "critical_findings": [r.criterion_id for r in scorecard.critical_findings()],
        "top_fixes": [
            {
                "criterion_id": r.criterion_id,
                "severity": r.severity,
                "score": round(float(r.score), 4),
                "weight": _weight(rubric, r.criterion_id),
                "message": r.message,
            }
            for r in scorecard.top_fixes()
        ],
        "results": [_result_dict(rubric, r) for r in scorecard.results],
        "metadata": dict(scorecard.metadata),
    }


def _result_dict(rubric: Rubric, result) -> dict[str, Any]:
    name: str | None = None
    tier: str | None = None
    weight: int | None = None
    try:
        criterion = rubric.by_id(result.criterion_id)
        name, tier, weight = criterion.name, criterion.tier, criterion.weight
    except KeyError:
        pass
    return {
        "criterion_id": result.criterion_id,
        "name": name,
        "tier": tier,
        "weight": weight,
        "passed": result.passed,
        "score": round(float(result.score), 4),
        "severity": result.severity,
        "message": result.message,
        "evidence": result.evidence,
    }


def _weight(rubric: Rubric, criterion_id: str) -> int | None:
    try:
        return rubric.by_id(criterion_id).weight
    except KeyError:
        return None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _json_default(obj: Any) -> Any:
    """Coerce values json.dumps cannot handle natively (numpy scalars, sets)."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    return str(obj)
