"""Scoring engine for vector-store-linter.

Given a rubric and a set of check results, this module computes per-tier
scores, an overall 0-100 score, and a letter grade, then packages them into a
Scorecard along with the findings. It also handles the rebalance case: when
Tier 3 (retrieval) is not evaluated because no ground truth was supplied, the
tier weights rebalance to the documented Tier 1 / Tier 2 split of 40 / 60.

The engine respects severity as reported on each CheckResult rather than the
rubric default. A check can downgrade its own severity (for example, a critical
criterion whose underlying store field is unknown downgrades to warn); the
scoring engine honors that decision and does not re-derive severity from the
rubric.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rubric import Rubric

logger = logging.getLogger(__name__)

# When Tier 3 is not evaluated, the remaining tiers carry the whole score in
# this fixed split. This is a product decision documented in rubric.yaml, not a
# proportional renormalization of the full weights.
REBALANCED_TIER_WEIGHTS: dict[str, int] = {"tier_1_config": 40, "tier_2_content": 60}

# Lower rank sorts first when ranking fixes by urgency.
_SEVERITY_RANK: dict[str, int] = {"critical": 0, "fail": 1, "warn": 2, "info": 3}

_GRADE_BANDS: tuple[tuple[int, str], ...] = ((90, "A"), (80, "B"), (70, "C"), (60, "D"))


@dataclass
class CheckResult:
    """The outcome of one criterion's check against the store.

    score is fractional credit from 0.0 to 1.0, so a check can report a partial
    pass. severity is the effective severity for this result and may be
    downgraded from the rubric default by the check itself. evidence holds
    criterion-specific detail used by the renderers; message is the
    human-readable one-line finding.
    """

    criterion_id: str
    passed: bool
    score: float
    severity: str
    evidence: dict[str, Any]
    message: str


@dataclass
class Scorecard:
    """The full result of a scoring pass."""

    overall_score: int  # 0 to 100
    grade: str  # one of A, B, C, D, F
    tier_scores: dict[str, int]  # tier id -> 0 to 100, only for evaluated tiers
    results: list[CheckResult]
    rubric: Rubric
    metadata: dict[str, Any] = field(default_factory=dict)

    def critical_findings(self) -> list[CheckResult]:
        """Return failing results whose effective severity is critical."""
        return [r for r in self.results if not r.passed and r.severity == "critical"]

    def top_fixes(self, n: int = 5) -> list[CheckResult]:
        """Return the n most impactful failing results.

        Ranked by severity (critical, then fail, then warn, then info) and,
        within a severity, by weighted score impact: the criterion's weight
        times the missing score (1 - score). A heavy criterion that scored low
        ranks above a light one that scored slightly low.
        """
        weight_by_id = {c.id: c.weight for c in self.rubric.criteria}

        def impact(result: CheckResult) -> float:
            return weight_by_id.get(result.criterion_id, 0) * (1.0 - result.score)

        failing = [r for r in self.results if not r.passed]
        failing.sort(key=lambda r: (_SEVERITY_RANK.get(r.severity, 99), -impact(r)))
        return failing[:n]


def grade_from_score(score: int) -> str:
    """Map a 0-100 score to a letter grade.

    A is 90 and above, B is 80 to 89, C is 70 to 79, D is 60 to 69, F is below
    60.
    """
    for threshold, letter in _GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def compute_score(
    rubric: Rubric,
    results: list[CheckResult],
    metadata: dict[str, Any] | None = None,
) -> Scorecard:
    """Compute a Scorecard from a rubric and a list of check results.

    Per-tier scores are the criterion-weighted average of result scores within
    the tier, normalized over the criteria that actually have results (so a
    tier scores correctly even when some of its checks did not run). The overall
    score combines the per-tier scores using the tier weights, normalized over
    the tiers that were evaluated. When Tier 3 was not evaluated, the rebalanced
    Tier 1 / Tier 2 weights apply.

    Args:
        rubric: The validated rubric the results were produced against.
        results: The check results to score. Results referencing a criterion id
            not in the rubric are ignored with a warning.
        metadata: Optional run metadata (store info, timestamp, modes used) to
            attach to the scorecard. Copied, not referenced.

    Returns:
        A Scorecard with the overall score, grade, per-tier scores, the full
        result list, the rubric, and the metadata.
    """
    weight_by_id = {c.id: c.weight for c in rubric.criteria}
    tier_by_id = {c.id: c.tier for c in rubric.criteria}

    results_by_tier: dict[str, list[CheckResult]] = {}
    for result in results:
        tier = tier_by_id.get(result.criterion_id)
        if tier is None:
            logger.warning(
                "Check result for unknown criterion id %r ignored.", result.criterion_id
            )
            continue
        results_by_tier.setdefault(tier, []).append(result)

    tier_scores: dict[str, int] = {}
    for tier, tier_results in results_by_tier.items():
        total_weight = sum(weight_by_id[r.criterion_id] for r in tier_results)
        if total_weight <= 0:
            tier_scores[tier] = 0
            continue
        weighted = sum(weight_by_id[r.criterion_id] * r.score for r in tier_results)
        tier_scores[tier] = round(100 * weighted / total_weight)

    effective_weights = _effective_tier_weights(rubric, set(tier_scores))
    present_weight = sum(effective_weights.get(tier, 0) for tier in tier_scores)
    if present_weight <= 0:
        overall = 0
    else:
        combined = sum(effective_weights.get(tier, 0) * score for tier, score in tier_scores.items())
        overall = round(combined / present_weight)

    return Scorecard(
        overall_score=overall,
        grade=grade_from_score(overall),
        tier_scores=tier_scores,
        results=list(results),
        rubric=rubric,
        metadata=dict(metadata or {}),
    )


def _effective_tier_weights(rubric: Rubric, evaluated_tiers: set[str]) -> dict[str, int]:
    """Return the tier weights to use, applying the no-ground-truth rebalance.

    When Tier 3 was evaluated, the rubric's own tier weights apply. When Tier 3
    was not evaluated, the rebalanced Tier 1 / Tier 2 split applies. The overall
    score normalizes over evaluated tiers, so a partial set of tiers still
    scores sensibly.
    """
    if "tier_3_retrieval" in evaluated_tiers:
        return rubric.tier_weights
    return dict(REBALANCED_TIER_WEIGHTS)
