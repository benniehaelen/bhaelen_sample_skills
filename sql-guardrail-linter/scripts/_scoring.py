"""Per-statement scoring, batch roll-up, and top-fixes ranking.

Single source of truth for the scoring formula in SKILL.md. Runs the control
modules from ``_controls`` against one parsed statement plus its context,
computes the weighted score and letter grade, counts violations by severity,
and assembles the report dict that ``render_report.py`` consumes.

The score is a quality lens. The gate is driven by violation severity, so the
two are reported side by side: ``score`` / ``grade`` per statement, and a
``violations`` count that the gate reads.
"""

from __future__ import annotations

from typing import Any

from _controls import ControlResult, LintContext, all_controls
from _serialize import bytes_human

RUBRIC_VERSION = "1.0"

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_IMPACT_TEXT = {
    "critical": "blocks the statement",
    "high": "high-severity violation",
    "medium": "medium-severity violation",
    "low": "hygiene issue",
}
_TOP_FIXES_CAP = 15


# ----- grade helper ---------------------------------------------------------


def grade_from_score(score: int, cutoffs: dict[str, Any]) -> str:
    """Letter grade per the rubric's ``grade_cutoffs``."""
    if score >= int(cutoffs.get("A", 90)):
        return "A"
    if score >= int(cutoffs.get("B", 80)):
        return "B"
    if score >= int(cutoffs.get("C", 70)):
        return "C"
    if score >= int(cutoffs.get("D", 60)):
        return "D"
    return "F"


def _weight(severity: str, rubric: dict[str, Any]) -> float:
    return float((rubric.get("severity_weights") or {}).get(severity, 0))


# ----- per-statement scoring ------------------------------------------------


def score_statement(
    stmt: Any,
    ctx: LintContext,
    rubric: dict[str, Any],
    *,
    statement_id: str,
    source: str,
) -> dict[str, Any]:
    """Run every control against one statement and assemble its report dict."""
    results: list[ControlResult] = [check(stmt, ctx, rubric) for _id, check in all_controls()]

    applicable = [r for r in results if not r.n_a]
    applicable_weight = sum(_weight(r.severity, rubric) for r in applicable)
    earned = 0.0
    for r in applicable:
        w = _weight(r.severity, rubric)
        if r.status == "pass":
            earned += w
        elif r.status == "warn":
            earned += 0.5 * w
    score = round(100 * earned / applicable_weight) if applicable_weight else 0
    grade = grade_from_score(score, rubric.get("grade_cutoffs", {}))

    violations = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in results:
        if r.status == "fail" and r.severity in violations:
            violations[r.severity] += 1

    estimated_bytes = int(ctx.estimated_bytes) if (ctx.bytes_available and ctx.estimated_bytes is not None) else None

    return {
        "statement_id": statement_id,
        "source": source,
        "sql_preview": _preview(stmt.raw),
        "score": score,
        "grade": grade,
        "statement_type": stmt.statement_type,
        "estimated_bytes": estimated_bytes,
        "estimated_bytes_human": bytes_human(estimated_bytes) if estimated_bytes is not None else None,
        "referenced_tables": _referenced_tables(stmt),
        "controls": [_control_dict(r) for r in results],
        "violations": violations,
    }


def _preview(raw: str, limit: int = 160) -> str:
    flat = " ".join((raw or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "..."


def _referenced_tables(stmt: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in getattr(stmt, "tables", []) or []:
        written = t.written
        if written and written.lower() not in seen:
            seen.add(written.lower())
            out.append(written)
    return out


def _control_dict(r: ControlResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "name": r.name,
        "tier": r.tier,
        "severity": r.severity,
        "status": r.status,
        "evidence": r.evidence,
    }
    if r.suggestion and r.status in ("fail", "warn"):
        out["suggestion"] = r.suggestion
    if r.estimated_bytes_saved:
        out["estimated_bytes_saved"] = int(r.estimated_bytes_saved)
    return out


# ----- batch roll-up --------------------------------------------------------


def build_report(
    *,
    statements: list[dict[str, Any]],
    rubric: dict[str, Any],
    mode: str,
    scope: dict[str, Any],
    gate_severity: str,
    linted_at: str,
    rubric_config: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    """Sort statements worst-first and assemble the full report dict."""
    ordered = sorted(
        statements,
        key=lambda s: (s.get("score", 100), -(s.get("estimated_bytes") or 0)),
    )

    return {
        "rubric_version": RUBRIC_VERSION,
        "rubric_config": rubric_config,
        "linted_at": linted_at,
        "mode": mode,
        "scope": scope,
        "summary": _summary(ordered, gate_severity),
        "statements": ordered,
        "top_fixes": _top_fixes(ordered),
        "expectations": [],
        "warnings": list(warnings or []),
    }


def _summary(statements: list[dict[str, Any]], gate_severity: str) -> dict[str, Any]:
    gate_rank = _SEVERITY_RANK.get((gate_severity or "high").lower(), 3)
    with_violations = 0
    total_bytes = 0
    have_bytes = False
    worst = 100
    for s in statements:
        worst = min(worst, int(s.get("score", 100)))
        if _max_violation_rank(s) >= gate_rank:
            with_violations += 1
        eb = s.get("estimated_bytes")
        if eb is not None:
            have_bytes = True
            total_bytes += int(eb)
    return {
        "statement_count": len(statements),
        "gate_severity": (gate_severity or "high").lower(),
        "statements_with_violations": with_violations,
        "worst_score": worst if statements else 0,
        "total_estimated_bytes": total_bytes if have_bytes else None,
        "total_estimated_bytes_human": bytes_human(total_bytes) if have_bytes else None,
    }


def _max_violation_rank(statement: dict[str, Any]) -> int:
    rank = 0
    for sev, count in (statement.get("violations") or {}).items():
        if count and _SEVERITY_RANK.get(sev, 0) > rank:
            rank = _SEVERITY_RANK.get(sev, 0)
    return rank


def _top_fixes(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All failing controls across statements, ranked by severity then bytes saved."""
    fixes: list[dict[str, Any]] = []
    for s in statements:
        for c in s.get("controls", []):
            if c.get("status") != "fail":
                continue
            severity = c.get("severity", "low")
            entry: dict[str, Any] = {
                "statement_id": s.get("statement_id"),
                "control": c.get("id"),
                "severity": severity,
                "fix": c.get("suggestion", ""),
            }
            saved = int(c.get("estimated_bytes_saved", 0) or 0)
            if saved > 0:
                entry["estimated_bytes_saved"] = saved
            else:
                entry["impact"] = _IMPACT_TEXT.get(severity, "violation")
            fixes.append(entry)
    fixes.sort(
        key=lambda f: (
            _SEVERITY_RANK.get(f.get("severity", "low"), 0),
            int(f.get("estimated_bytes_saved", 0) or 0),
        ),
        reverse=True,
    )
    return fixes[:_TOP_FIXES_CAP]
