"""Tier 3 checks: retrieval quality.

Scored against a list of QueryEvaluation records, one per ground-truth query,
each holding the expected document ids and the retrieved ids in rank order. The
caller (the evaluate wireup) runs the searches and builds these records; this
module only measures.

The eight rubric criteria are covered by the standard ranking metrics (recall,
precision, NDCG, MRR, hit rate at k) plus two catalog-level guards: failures
should not concentrate in one query type, and no single document should be
retrieved for an unusually large share of queries. The metrics are implemented
from scratch in plain Python rather than pulling in pytrec_eval; they are short
and the dependency is not justified.

For the rank metrics the score is the metric value itself (a 0 to 1 quality
measure), and the criterion passes when it meets the rubric minimum. For the two
lower-is-better guards the score is 1.0 on pass and partial on fail.

Note the plan prose says six metrics but lists seven and the rubric defines
eight Tier 3 criteria. All eight are implemented here; the prose count is the
loose one.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable

from rubric import Criterion, Rubric
from scoring import CheckResult

from ._common import finding as _finding
from ._common import ok as _ok
from ._common import unknown as _unknown

logger = logging.getLogger("vector_store_linter")

# A query with no relevant result in the top _FAILURE_K counts as a failure for
# the concentration and (implicitly) hit-rate analyses.
_FAILURE_K = 10
# Below this many failing queries, concentration is not meaningfully assessable.
_MIN_FAILURES_FOR_CONCENTRATION = 3


@dataclass
class QueryEvaluation:
    """One ground-truth query and what the store retrieved for it."""

    query: str
    expected_ids: set[str]
    retrieved_ids: list[str]  # in rank order, best first
    query_type: str | None = None


# ----- per-query metric primitives -----------------------------------------


def recall_at_k(expected: set[str], retrieved: list[str], k: int) -> float:
    """Fraction of expected documents present in the top k."""
    if not expected:
        return 0.0
    top = set(retrieved[:k])
    return len(expected & top) / len(expected)


def precision_at_k(expected: set[str], retrieved: list[str], k: int) -> float:
    """Fraction of the top k that are relevant (denominator is k)."""
    if k <= 0:
        return 0.0
    hits = sum(1 for doc in retrieved[:k] if doc in expected)
    return hits / k


def hit_at_k(expected: set[str], retrieved: list[str], k: int) -> float:
    """1.0 if any relevant document is in the top k, else 0.0."""
    return 1.0 if set(retrieved[:k]) & expected else 0.0


def reciprocal_rank(expected: set[str], retrieved: list[str]) -> float:
    """1 / rank of the first relevant document, or 0.0 if none retrieved."""
    for index, doc in enumerate(retrieved):
        if doc in expected:
            return 1.0 / (index + 1)
    return 0.0


def ndcg_at_k(expected: set[str], retrieved: list[str], k: int) -> float:
    """Normalized discounted cumulative gain at k, binary relevance."""
    if not expected:
        return 0.0
    dcg = sum(1.0 / math.log2(i + 2) for i, doc in enumerate(retrieved[:k]) if doc in expected)
    ideal_hits = min(k, len(expected))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


# ----- the eight checks ----------------------------------------------------


def _rank_metric_check(
    criterion: Criterion,
    evaluations: list[QueryEvaluation],
    per_query: Callable[[QueryEvaluation], float],
) -> CheckResult:
    """Shared body for the higher-is-better rank metrics with a min threshold."""
    if not evaluations:
        return _unknown(criterion, "No ground-truth queries to evaluate.")
    value = _mean(per_query(e) for e in evaluations)
    minimum = float(criterion.threshold.get("min", 0.0))
    evidence = {"value": round(value, 4), "min": minimum, "query_count": len(evaluations)}
    message = f"{criterion.name} is {value:.3f} (minimum {minimum})."
    if value >= minimum:
        return _ok(criterion, message, score=round(value, 4), evidence=evidence)
    return _finding(criterion, message, score=round(value, 4), evidence=evidence)


def check_recall_at_5(criterion, evaluations) -> CheckResult:
    return _rank_metric_check(criterion, evaluations, lambda e: recall_at_k(e.expected_ids, e.retrieved_ids, 5))


def check_recall_at_10(criterion, evaluations) -> CheckResult:
    return _rank_metric_check(criterion, evaluations, lambda e: recall_at_k(e.expected_ids, e.retrieved_ids, 10))


def check_precision_at_5(criterion, evaluations) -> CheckResult:
    return _rank_metric_check(criterion, evaluations, lambda e: precision_at_k(e.expected_ids, e.retrieved_ids, 5))


def check_ndcg_at_10(criterion, evaluations) -> CheckResult:
    return _rank_metric_check(criterion, evaluations, lambda e: ndcg_at_k(e.expected_ids, e.retrieved_ids, 10))


def check_mrr(criterion, evaluations) -> CheckResult:
    return _rank_metric_check(criterion, evaluations, lambda e: reciprocal_rank(e.expected_ids, e.retrieved_ids))


def check_hit_rate_at_10(criterion, evaluations) -> CheckResult:
    return _rank_metric_check(criterion, evaluations, lambda e: hit_at_k(e.expected_ids, e.retrieved_ids, 10))


def check_query_failure_concentration(criterion, evaluations) -> CheckResult:
    """Whether failing queries are spread across query types."""
    if not evaluations:
        return _unknown(criterion, "No ground-truth queries to evaluate.")
    failures = [e for e in evaluations if hit_at_k(e.expected_ids, e.retrieved_ids, _FAILURE_K) == 0.0]
    total = len(failures)
    if total < _MIN_FAILURES_FOR_CONCENTRATION:
        return _ok(
            criterion,
            f"{total} failing query/queries; too few to assess concentration.",
            evidence={"failure_count": total},
        )
    by_type = Counter(e.query_type or "untyped" for e in failures)
    concentration = max(by_type.values()) / total
    maximum = float(criterion.threshold.get("max_failure_concentration_per_type", 0.40))
    evidence = {"failure_count": total, "max_concentration": round(concentration, 4), "by_type": dict(by_type)}
    message = f"Largest share of failures in one type is {concentration:.0%} (limit {maximum:.0%})."
    if concentration <= maximum:
        return _ok(criterion, message, evidence=evidence)
    return _finding(criterion, message, score=round(max(0.0, 1.0 - concentration), 4), evidence=evidence)


def check_no_universal_results(criterion, evaluations) -> CheckResult:
    """Whether any single document is retrieved for too large a share of queries."""
    if not evaluations:
        return _unknown(criterion, "No ground-truth queries to evaluate.")
    appearances: Counter = Counter()
    for e in evaluations:
        for doc in set(e.retrieved_ids[:_FAILURE_K]):
            appearances[doc] += 1
    if not appearances:
        return _unknown(criterion, "No retrieved results to assess universal-result pollution.")
    n = len(evaluations)
    worst_doc, worst_count = appearances.most_common(1)[0]
    rate = worst_count / n
    maximum = float(criterion.threshold.get("max_appearance_rate", 0.15))
    evidence = {"max_appearance_rate": round(rate, 4), "worst_doc": worst_doc, "query_count": n}
    message = f"Most-repeated document appears in {rate:.0%} of query results (limit {maximum:.0%})."
    if rate <= maximum:
        return _ok(criterion, message, evidence=evidence)
    return _finding(criterion, message, score=round(max(0.0, 1.0 - rate), 4), evidence=evidence)


TIER3_CHECKS: dict[str, Callable[..., CheckResult]] = {
    "c_recall_at_5": check_recall_at_5,
    "c_recall_at_10": check_recall_at_10,
    "c_precision_at_5": check_precision_at_5,
    "c_ndcg_at_10": check_ndcg_at_10,
    "c_mrr": check_mrr,
    "c_hit_rate_at_10": check_hit_rate_at_10,
    "c_query_failure_concentration": check_query_failure_concentration,
    "c_no_universal_results": check_no_universal_results,
}


def run_tier3(rubric: Rubric, evaluations: list[QueryEvaluation]) -> list[CheckResult]:
    """Run every registered Tier 3 check over the query evaluations.

    Args:
        rubric: The rubric whose Tier 3 criteria drive the run.
        evaluations: One QueryEvaluation per ground-truth query.

    Returns:
        One CheckResult per Tier 3 criterion that has a registered check.
    """
    results: list[CheckResult] = []
    for criterion in rubric.by_tier("tier_3_retrieval"):
        check = TIER3_CHECKS.get(criterion.id)
        if check is None:
            logger.warning("No Tier 3 check registered for %r; skipping.", criterion.id)
            continue
        results.append(check(criterion, evaluations))
    return results
