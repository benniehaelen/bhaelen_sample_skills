"""Unit tests for the Tier 3 retrieval metrics and checks.

The metric primitives are checked against hand-computed values. The criterion
checks use the shipped rubric thresholds, with evaluations built to land on
either side of each threshold. run_tier3 is tested end to end.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from checks import tier3_retrieval as t3
from checks.tier3_retrieval import QueryEvaluation
from rubric import Rubric

RUBRIC = Rubric.load(Path(__file__).resolve().parent.parent / "scripts" / "rubric.yaml")


def crit(criterion_id: str):
    return RUBRIC.by_id(criterion_id)


def ev(expected, retrieved, query_type=None):
    return QueryEvaluation(query="q", expected_ids=set(expected), retrieved_ids=list(retrieved), query_type=query_type)


# ----- metric primitives ---------------------------------------------------


def test_recall_at_k():
    # 3 of 5 expected present in the top 5.
    expected = {"a", "b", "c", "d", "e"}
    retrieved = ["a", "x", "b", "y", "c"]
    assert t3.recall_at_k(expected, retrieved, 5) == pytest.approx(0.6)


def test_precision_at_k():
    expected = {"a", "b"}
    retrieved = ["a", "x", "b", "y", "z"]
    assert t3.precision_at_k(expected, retrieved, 5) == pytest.approx(0.4)  # 2 of 5


def test_hit_at_k():
    assert t3.hit_at_k({"a"}, ["x", "a", "y"], 10) == 1.0
    assert t3.hit_at_k({"a"}, ["x", "y"], 10) == 0.0


def test_reciprocal_rank():
    assert t3.reciprocal_rank({"a"}, ["x", "a", "y"]) == pytest.approx(0.5)  # rank 2
    assert t3.reciprocal_rank({"a"}, ["x", "y"]) == 0.0


def test_ndcg_at_k():
    # a at rank 1, b at rank 3. DCG = 1/log2(2) + 1/log2(4) = 1.5.
    # IDCG (2 relevant) = 1/log2(2) + 1/log2(3) = 1 + 0.63093 = 1.63093.
    expected = {"a", "b"}
    retrieved = ["a", "x", "b", "y"]
    idcg = 1.0 + 1.0 / math.log2(3)
    assert t3.ndcg_at_k(expected, retrieved, 4) == pytest.approx(1.5 / idcg)


# ----- rank-metric checks --------------------------------------------------


def test_recall_at_5_pass():
    evals = [ev({"a"}, ["a", "x"]), ev({"b"}, ["b", "y"])]  # recall 1.0 each
    r = t3.check_recall_at_5(crit("c_recall_at_5"), evals)
    assert r.passed and r.score == 1.0


def test_recall_at_5_fail():
    evals = [ev({"a", "b"}, ["a", "x"]), ev({"c", "d"}, ["c", "y"])]  # recall 0.5 each
    r = t3.check_recall_at_5(crit("c_recall_at_5"), evals)
    assert not r.passed and r.score == pytest.approx(0.5)


def test_hit_rate_at_10_pass():
    evals = [ev({"a"}, ["a"]) for _ in range(10)]
    r = t3.check_hit_rate_at_10(crit("c_hit_rate_at_10"), evals)
    assert r.passed and r.score == 1.0


def test_mrr_check():
    evals = [ev({"a"}, ["x", "a"]), ev({"b"}, ["b"])]  # rr 0.5 and 1.0 -> mean 0.75
    r = t3.check_mrr(crit("c_mrr"), evals)
    assert r.score == pytest.approx(0.75) and r.passed  # min 0.60


def test_unknown_when_no_evaluations():
    r = t3.check_recall_at_5(crit("c_recall_at_5"), [])
    assert not r.passed and r.score == 0.5  # downgraded unknown


# ----- failure concentration -----------------------------------------------


def test_failure_concentration_flagged():
    # 5 failing queries, all of one type -> 100 percent concentration.
    fails = [ev({"a"}, ["x"], query_type="metric") for _ in range(5)]
    passes = [ev({"a"}, ["a"], query_type="rule") for _ in range(5)]
    r = t3.check_query_failure_concentration(crit("c_query_failure_concentration"), fails + passes)
    assert not r.passed
    assert r.evidence["max_concentration"] == 1.0


def test_failure_concentration_spread_passes():
    fails = [
        ev({"a"}, ["x"], query_type="metric"),
        ev({"a"}, ["x"], query_type="rule"),
        ev({"a"}, ["x"], query_type="reference"),
        ev({"a"}, ["x"], query_type="governance"),
    ]
    r = t3.check_query_failure_concentration(crit("c_query_failure_concentration"), fails)
    assert r.passed  # 25 percent per type, under the 40 percent limit


def test_failure_concentration_too_few_failures_passes():
    evals = [ev({"a"}, ["x"], query_type="metric")] + [ev({"a"}, ["a"]) for _ in range(5)]
    r = t3.check_query_failure_concentration(crit("c_query_failure_concentration"), evals)
    assert r.passed and r.evidence["failure_count"] == 1


# ----- universal results ---------------------------------------------------


def test_no_universal_results_flagged():
    # doc "boiler" appears in every query's results -> 100 percent appearance.
    evals = [ev({"a"}, ["boiler", f"d{i}"]) for i in range(10)]
    r = t3.check_no_universal_results(crit("c_no_universal_results"), evals)
    assert not r.passed
    assert r.evidence["worst_doc"] == "boiler"
    assert r.evidence["max_appearance_rate"] == 1.0


def test_no_universal_results_pass():
    evals = [ev({"a"}, [f"d{i}", f"e{i}"]) for i in range(20)]  # all distinct docs
    r = t3.check_no_universal_results(crit("c_no_universal_results"), evals)
    assert r.passed


# ----- registry and runner -------------------------------------------------


def test_registry_covers_all_tier3_criteria():
    tier3_ids = {c.id for c in RUBRIC.by_tier("tier_3_retrieval")}
    assert set(t3.TIER3_CHECKS) == tier3_ids


def test_run_tier3_returns_result_per_criterion():
    # Strong retrieval: each query has three relevant docs, all retrieved in the
    # top three (so precision@5 = 0.6, above its 0.5 minimum), all docs distinct.
    evals = [
        ev({f"a{i}", f"b{i}", f"c{i}"}, [f"a{i}", f"b{i}", f"c{i}", f"d{i}", f"e{i}"], query_type="metric")
        for i in range(20)
    ]
    results = t3.run_tier3(RUBRIC, evals)
    assert len(results) == 8
    assert {r.criterion_id for r in results} == {c.id for c in RUBRIC.by_tier("tier_3_retrieval")}
    assert all(r.passed for r in results), {r.criterion_id: r.message for r in results if not r.passed}
