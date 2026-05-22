"""Unit tests for the heuristic failure classifier.

Each test builds a FailureCase that triggers one mode while leaving the others
without their required data, so the detectors are checked in isolation. The
confidence-threshold filtering and score-direction normalization are tested too.
"""

from __future__ import annotations

import datetime as dt

from adapters.base import RetrievalResult
from failure_classifier import ClassificationContext, FailureCase, classify
from failure_classifier.heuristic import _detect_semantic_distance


def rr(doc_id, score=0.9, metadata=None, content=None):
    return RetrievalResult(doc_id=doc_id, score=score, metadata=metadata or {}, content=content)


def modes(results):
    return {c.mode for c in results}


# ----- one mode at a time --------------------------------------------------


def test_vocabulary_mismatch():
    case = FailureCase(
        query="readmission penalty methodology",
        expected_ids={"d1"},
        retrieved=[],
        expected_docs={"d1": {"content": "patients returning to hospital within thirty days", "metadata": {}}},
    )
    results = classify(case, ClassificationContext())
    assert "vocabulary_mismatch" in modes(results)


def test_semantic_distance_similarity_metric():
    case = FailureCase(query="some query terms", expected_ids={"d1"}, retrieved=[rr("d2", score=0.30)])
    ctx = ClassificationContext(metric="cosine", score_is_distance=False)
    assert "semantic_distance" in modes(classify(case, ctx))


def test_semantic_distance_distance_metric_normalized():
    # Distance of 0.8 under cosine -> similarity 0.2, which is below 0.5.
    case = FailureCase(query="some query terms", expected_ids={"d1"}, retrieved=[rr("d2", score=0.80)])
    ctx = ClassificationContext(metric="cosine", score_is_distance=True)
    assert "semantic_distance" in modes(classify(case, ctx))


def test_semantic_distance_skipped_for_l2():
    case = FailureCase(query="some query terms", expected_ids={"d1"}, retrieved=[rr("d2", score=42.0)])
    ctx = ClassificationContext(metric="l2", score_is_distance=True)
    assert "semantic_distance" not in modes(classify(case, ctx))


def test_semantic_distance_at_threshold_not_reported():
    case = FailureCase(query="q terms here", expected_ids={"d1"}, retrieved=[rr("d2", score=0.50)])
    ctx = ClassificationContext(metric="cosine", score_is_distance=False)
    assert "semantic_distance" not in modes(classify(case, ctx))  # 0.50 is not below 0.50


def test_missing_metadata_filter():
    case = FailureCase(
        query="filter adult patients by age",
        expected_ids={"d1"},  # absent from results
        retrieved=[rr("d2", content="how to filter adult patients by age in sql")],
    )
    ctx = ClassificationContext(metric=None)  # keep semantic_distance inert
    assert "missing_metadata_filter" in modes(classify(case, ctx))


def test_chunking_artifact():
    case = FailureCase(
        query="readmission rule details and handling",
        expected_ids={"d1"},
        retrieved=[rr("d2", metadata={"source": "docA"}, content="unrelated header")],
        expected_docs={"d1": {"content": "readmission rule details and handling guidance", "metadata": {"source": "docA"}}},
    )
    assert "chunking_artifact" in modes(classify(case, ClassificationContext()))


def test_boilerplate_pollution():
    case = FailureCase(
        query="a very specific narrow question",
        expected_ids={"d1"},
        retrieved=[rr("boiler", content="common page header")],
    )
    ctx = ClassificationContext(appearance_counts={"boiler": 9}, query_count=10)
    assert "boilerplate_pollution" in modes(classify(case, ctx))


def test_stale_content():
    case = FailureCase(
        query="some specific narrow question",
        expected_ids={"d1"},
        retrieved=[rr("d2", metadata={"timestamp": "2020-01-01"}, content="old content")],
    )
    ctx = ClassificationContext(newest_timestamp=dt.datetime(2026, 1, 1))
    assert "stale_content" in modes(classify(case, ctx))


def test_model_drift():
    case = FailureCase(
        query="readmission rule details handling guidance",
        expected_ids={"d1"},
        retrieved=[rr("d2", metadata={"model": "model-a"})],
        expected_docs={"d1": {"content": "readmission rule details handling guidance", "metadata": {"model": "model-b"}}},
    )
    assert "model_drift" in modes(classify(case, ClassificationContext()))


# ----- negative and ordering -----------------------------------------------


def test_nothing_detected_returns_empty():
    # Expected doc is retrieved at the top with strong score and shared terms.
    case = FailureCase(
        query="readmission rule details handling guidance",
        expected_ids={"d1"},
        retrieved=[rr("d1", score=0.95, content="readmission rule details handling guidance")],
        expected_docs={"d1": {"content": "readmission rule details handling guidance", "metadata": {}}},
    )
    ctx = ClassificationContext(metric="cosine", score_is_distance=False, query_count=10)
    assert classify(case, ctx) == []


def test_results_sorted_by_confidence():
    # Both vocabulary_mismatch and boilerplate fire; the result is confidence-sorted.
    case = FailureCase(
        query="penalty methodology specifics",
        expected_ids={"d1"},
        retrieved=[rr("boiler", content="header")],
        expected_docs={"d1": {"content": "completely different words about apples", "metadata": {}}},
    )
    ctx = ClassificationContext(appearance_counts={"boiler": 10}, query_count=10)
    results = classify(case, ctx)
    confidences = [c.confidence for c in results]
    assert confidences == sorted(confidences, reverse=True)
    assert len(results) >= 2


def test_detector_returns_zero_without_scores():
    case = FailureCase(query="q", expected_ids={"d1"}, retrieved=[rr("d2", score=0.1)])
    confidence, _ = _detect_semantic_distance(case, ClassificationContext(metric=None))
    assert confidence == 0.0
