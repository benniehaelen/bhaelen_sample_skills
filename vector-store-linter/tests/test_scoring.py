"""Unit tests for the scoring engine.

Covers the documented cases: a full three-tier evaluation, the no-ground-truth
rebalance, a single-tier failure, mixed severities with top_fixes ordering and
critical_findings, grade boundaries, the empty-results case, severity-downgrade
respect, and ignoring results for unknown criteria.
"""

from __future__ import annotations

import pytest

from rubric import Rubric
from scoring import (
    CheckResult,
    Scorecard,
    compute_score,
    grade_from_score,
)


def _rubric() -> Rubric:
    """A small three-tier rubric with a two-criterion Tier 1 for weighting tests."""
    return Rubric.from_dict(
        {
            "tier_weights": {"tier_1_config": 25, "tier_2_content": 30, "tier_3_retrieval": 45},
            "criteria": [
                {"id": "c1a", "name": "1a", "tier": "tier_1_config", "weight": 60,
                 "severity": "critical", "description": "d", "threshold": {}},
                {"id": "c1b", "name": "1b", "tier": "tier_1_config", "weight": 40,
                 "severity": "warn", "description": "d", "threshold": {}},
                {"id": "c2", "name": "2", "tier": "tier_2_content", "weight": 100,
                 "severity": "fail", "description": "d", "threshold": {}},
                {"id": "c3", "name": "3", "tier": "tier_3_retrieval", "weight": 100,
                 "severity": "fail", "description": "d", "threshold": {}},
            ],
        }
    )


def _result(cid: str, *, passed: bool, score: float, severity: str) -> CheckResult:
    return CheckResult(
        criterion_id=cid, passed=passed, score=score, severity=severity, evidence={}, message="m"
    )


# ----- grade boundaries ----------------------------------------------------


@pytest.mark.parametrize(
    "score,grade",
    [(100, "A"), (90, "A"), (89, "B"), (80, "B"), (79, "C"), (70, "C"),
     (69, "D"), (60, "D"), (59, "F"), (0, "F")],
)
def test_grade_from_score_boundaries(score, grade):
    assert grade_from_score(score) == grade


# ----- full evaluation -----------------------------------------------------


def test_full_evaluation():
    rubric = _rubric()
    results = [
        _result("c1a", passed=True, score=1.0, severity="critical"),
        _result("c1b", passed=False, score=0.5, severity="warn"),
        _result("c2", passed=True, score=1.0, severity="fail"),
        _result("c3", passed=False, score=0.0, severity="fail"),
    ]
    card = compute_score(rubric, results)
    # tier_1 = (60*1.0 + 40*0.5) / 100 * 100 = 80
    assert card.tier_scores["tier_1_config"] == 80
    assert card.tier_scores["tier_2_content"] == 100
    assert card.tier_scores["tier_3_retrieval"] == 0
    # overall = (25*80 + 30*100 + 45*0) / 100 = 50
    assert card.overall_score == 50
    assert card.grade == "F"


# ----- no ground truth (rebalance) -----------------------------------------


def test_no_ground_truth_rebalances():
    rubric = _rubric()
    results = [
        _result("c1a", passed=True, score=1.0, severity="critical"),
        _result("c1b", passed=False, score=0.5, severity="warn"),
        _result("c2", passed=True, score=1.0, severity="fail"),
    ]
    card = compute_score(rubric, results)
    assert "tier_3_retrieval" not in card.tier_scores
    # tier_1 = 80, tier_2 = 100; rebalanced weights 40/60.
    # overall = (40*80 + 60*100) / 100 = 92
    assert card.overall_score == 92
    assert card.grade == "A"


# ----- single tier failure -------------------------------------------------


def test_single_tier_failure():
    rubric = _rubric()
    results = [
        _result("c1a", passed=False, score=0.0, severity="critical"),
        _result("c1b", passed=False, score=0.0, severity="warn"),
        _result("c2", passed=True, score=1.0, severity="fail"),
        _result("c3", passed=True, score=1.0, severity="fail"),
    ]
    card = compute_score(rubric, results)
    assert card.tier_scores["tier_1_config"] == 0
    # overall = (25*0 + 30*100 + 45*100) / 100 = 75
    assert card.overall_score == 75
    assert card.grade == "C"


# ----- mixed severities, top_fixes, critical_findings ----------------------


def test_top_fixes_ranks_by_severity_then_impact():
    rubric = _rubric()
    results = [
        _result("c1a", passed=False, score=0.0, severity="critical"),  # impact 60
        _result("c1b", passed=False, score=0.5, severity="warn"),      # impact 20
        _result("c2", passed=False, score=0.0, severity="fail"),       # impact 100
        _result("c3", passed=True, score=1.0, severity="fail"),        # passing, excluded
    ]
    card = compute_score(rubric, results)
    fixes = card.top_fixes()
    # critical first, then fail, then warn; passing result excluded.
    assert [r.criterion_id for r in fixes] == ["c1a", "c2", "c1b"]


def test_top_fixes_n_limit():
    rubric = _rubric()
    results = [
        _result("c1a", passed=False, score=0.0, severity="fail"),
        _result("c1b", passed=False, score=0.0, severity="fail"),
        _result("c2", passed=False, score=0.0, severity="fail"),
    ]
    card = compute_score(rubric, results)
    assert len(card.top_fixes(n=2)) == 2


def test_critical_findings_only_failing_critical():
    rubric = _rubric()
    results = [
        _result("c1a", passed=False, score=0.0, severity="critical"),  # included
        _result("c1b", passed=True, score=1.0, severity="critical"),   # passing, excluded
        _result("c2", passed=False, score=0.0, severity="fail"),       # not critical, excluded
    ]
    card = compute_score(rubric, results)
    findings = card.critical_findings()
    assert [r.criterion_id for r in findings] == ["c1a"]


# ----- severity downgrade respected ----------------------------------------


def test_uses_result_severity_not_rubric_default():
    # c1a is "critical" in the rubric, but the check downgraded it to "warn"
    # (for example because the underlying field was unknown).
    rubric = _rubric()
    results = [_result("c1a", passed=False, score=0.0, severity="warn")]
    card = compute_score(rubric, results)
    assert card.critical_findings() == []
    assert card.top_fixes()[0].severity == "warn"


# ----- edge cases ----------------------------------------------------------


def test_empty_results():
    rubric = _rubric()
    card = compute_score(rubric, [])
    assert card.overall_score == 0
    assert card.grade == "F"
    assert card.tier_scores == {}


def test_unknown_criterion_ignored():
    rubric = _rubric()
    results = [
        _result("c2", passed=True, score=1.0, severity="fail"),
        _result("c_not_in_rubric", passed=False, score=0.0, severity="fail"),
    ]
    card = compute_score(rubric, results)
    # Only tier_2 evaluated; the unknown criterion is dropped.
    assert card.tier_scores == {"tier_2_content": 100}
    assert len(card.results) == 2  # full list retained for transparency


def test_partial_tier_normalizes():
    # Only one of Tier 1's two criteria has a result; the tier score normalizes
    # over the present weight rather than treating the missing one as zero.
    rubric = _rubric()
    results = [_result("c1a", passed=True, score=1.0, severity="critical")]
    card = compute_score(rubric, results)
    assert card.tier_scores["tier_1_config"] == 100


def test_metadata_attached_and_copied():
    rubric = _rubric()
    meta = {"store_type": "pinecone", "modes": ["audit-config"]}
    card = compute_score(rubric, [], metadata=meta)
    assert card.metadata == meta
    meta["mutated"] = True
    assert "mutated" not in card.metadata  # copied, not referenced
