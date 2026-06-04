"""Tests for _scoring.py: the formula, violations, batch roll-up, top-fixes."""

from __future__ import annotations

from _parser import parse_statement
from _controls import LintContext
import _scoring as scoring


def score(sql, rubric, **ctx_kwargs):
    stmt = parse_statement(sql)
    ctx = LintContext(**ctx_kwargs)
    return scoring.score_statement(stmt, ctx, rubric, statement_id="stmt_0001", source="inline")


# ----- formula --------------------------------------------------------------


def test_clean_statement_scores_100(rubric):
    sql = "SELECT coid, COUNT(*) FROM proj.clinical.encounter WHERE admit_date >= '2026-01-01' GROUP BY coid"
    card = score(sql, rubric)
    assert card["score"] == 100
    assert card["grade"] == "A"


def test_warn_earns_half_weight(rubric):
    # Identical to the clean query but non-aggregating and unbounded -> SQ-006 warns.
    sql = "SELECT coid FROM proj.clinical.encounter WHERE admit_date >= '2026-01-01'"
    card = score(sql, rubric)
    # SQ-006 is the only non-pass applicable control (medium weight 2, warn -> 1 of 2).
    sq006 = [c for c in card["controls"] if c["id"] == "SQ-006"][0]
    assert sq006["status"] == "warn"
    assert card["score"] < 100


def test_na_excluded_from_denominator(rubric):
    card = score("SELECT a, b FROM proj.d.t LIMIT 5", rubric)
    statuses = {c["id"]: c["status"] for c in card["controls"]}
    # Conditional controls with no inputs are na, and must not be counted as pass.
    assert statuses["SQ-008"] == "na"
    assert statuses["SQ-009"] == "na"
    assert statuses["SQ-010"] == "na"


def test_violations_count_only_fails(rubric):
    card = score("SELECT * FROM d.t ORDER BY a", rubric)
    v = card["violations"]
    # SQ-005 fail (medium), SQ-011 fail (low), SQ-012 fail (low); SQ-006 warns (not a violation).
    assert v["medium"] == 1
    assert v["low"] == 2
    assert v["critical"] == 0


def test_grade_cutoffs_configurable(rubric):
    rubric["grade_cutoffs"] = {"A": 99, "B": 50, "C": 40, "D": 30}
    card = score("SELECT coid, COUNT(*) FROM proj.clinical.encounter WHERE admit_date >= '2026-01-01' GROUP BY coid", rubric)
    assert card["score"] == 100
    assert card["grade"] == "A"


# ----- report assembly ------------------------------------------------------


def _two_card_report(rubric):
    bad = score("SELECT ssn FROM proj.d.patients", rubric, pii_policy={"proj.d.patients": ["ssn"]})
    bad["statement_id"] = "stmt_0001"
    good = score("SELECT a FROM proj.d.t LIMIT 5", rubric)
    good["statement_id"] = "stmt_0002"
    return scoring.build_report(
        statements=[good, bad],
        rubric=rubric,
        mode="static",
        scope={"source": "sql_file", "statement_count": 2},
        gate_severity="high",
        linted_at="2026-06-03T00:00:00+00:00",
        rubric_config={"source": "builtin", "name": "x", "version": "1.0", "sha256": ""},
        warnings=[],
    )


def test_report_sorts_worst_first(rubric):
    report = _two_card_report(rubric)
    assert report["statements"][0]["statement_id"] == "stmt_0001"  # the PII-failing one
    assert report["statements"][0]["score"] <= report["statements"][1]["score"]


def test_summary_counts_violations_at_gate(rubric):
    report = _two_card_report(rubric)
    # stmt_0001 has a critical PII violation, which is >= high.
    assert report["summary"]["statements_with_violations"] == 1


def test_top_fixes_ranked_by_severity(rubric):
    report = _two_card_report(rubric)
    fixes = report["top_fixes"]
    assert fixes  # at least the PII fix
    # The first fix should be the highest-severity one (critical SQ-010).
    assert fixes[0]["control"] == "SQ-010"
    assert fixes[0]["severity"] == "critical"


def test_top_fix_byte_savings_present(rubric):
    card = score("SELECT a FROM proj.d.t", rubric,
                 mode="cost_aware", max_bytes_ceiling=10_000, estimated_bytes=25_000, bytes_available=True)
    report = scoring.build_report(
        statements=[card], rubric=rubric, mode="cost_aware",
        scope={"source": "sql", "statement_count": 1}, gate_severity="high",
        linted_at="t", rubric_config={}, warnings=[],
    )
    sq008_fix = [f for f in report["top_fixes"] if f["control"] == "SQ-008"][0]
    assert sq008_fix["estimated_bytes_saved"] == 15_000
    assert report["summary"]["total_estimated_bytes"] == 25_000
