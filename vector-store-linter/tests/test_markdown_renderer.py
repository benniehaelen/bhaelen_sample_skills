"""Unit tests for the Markdown renderer."""

from __future__ import annotations

from renderers import markdown_renderer
from rubric import Rubric
from scoring import CheckResult, compute_score


def _rubric() -> Rubric:
    return Rubric.from_dict(
        {
            "tier_weights": {"tier_1_config": 25, "tier_2_content": 30, "tier_3_retrieval": 45},
            "criteria": [
                {"id": "c_dimension_consistency", "name": "Dim", "tier": "tier_1_config", "weight": 100,
                 "severity": "critical", "description": "d", "threshold": {}},
                {"id": "c_no_exact_duplicates", "name": "Dupes", "tier": "tier_2_content", "weight": 100,
                 "severity": "warn", "description": "d", "threshold": {}},
                {"id": "c_recall_at_10", "name": "Recall@10", "tier": "tier_3_retrieval", "weight": 100,
                 "severity": "fail", "description": "d", "threshold": {}},
            ],
        }
    )


def _scorecard(metadata=None):
    rubric = _rubric()
    results = [
        CheckResult("c_dimension_consistency", True, 1.0, "critical", {}, "single dimension"),
        CheckResult("c_no_exact_duplicates", False, 0.5, "warn", {"duplicate_rate": 0.2}, "too many dupes"),
        CheckResult("c_recall_at_10", False, 0.4, "fail", {"value": 0.4}, "recall low"),
    ]
    meta = {
        "store": {"store_type": "pinecone", "index": "kb"},
        "modes": ["evaluate"],
        "rubric": {"source": "builtin"},
        "scored_at": "2026-01-01T00:00:00+00:00",
    }
    meta.update(metadata or {})
    return compute_score(rubric, results, metadata=meta)


def test_has_title_and_summary():
    out = markdown_renderer.render(_scorecard())
    assert out.startswith("# Vector Store Scorecard")
    assert "## Summary" in out
    assert "Overall:" in out
    assert "Tier 1: configuration:" in out


def test_subtitle_has_store_and_modes():
    out = markdown_renderer.render(_scorecard())
    assert "store `pinecone` (kb)" in out
    assert "modes: evaluate" in out


def test_top_fixes_table():
    out = markdown_renderer.render(_scorecard())
    assert "## Top fixes" in out
    assert "`c_recall_at_10`" in out  # a failing criterion appears


def test_per_tier_sections():
    out = markdown_renderer.render(_scorecard())
    assert "## Tier 1: configuration" in out
    assert "## Tier 2: content" in out
    assert "## Tier 3: retrieval" in out
    assert "[pass]" in out and "[fail]" in out


def test_warnings_section():
    out = markdown_renderer.render(_scorecard({"warnings": ["synthetic ground truth in use"]}))
    assert "## Warnings" in out
    assert "synthetic ground truth in use" in out


def test_failing_queries_table():
    failing = [{
        "query": "what is the rate?",
        "query_type": "metric",
        "expected_ids": ["doc_1"],
        "retrieved": [{"id": "wrong_1", "score": 0.5}, {"id": "wrong_2", "score": 0.4}],
        "classifications": [{"mode": "vocabulary_mismatch", "confidence": 0.8, "explanation": "x"}],
    }]
    out = markdown_renderer.render(_scorecard({"failing_queries": failing}))
    assert "## Failing queries" in out
    assert "what is the rate?" in out
    assert "vocabulary_mismatch (0.80)" in out


def test_no_failing_queries_section_when_absent():
    out = markdown_renderer.render(_scorecard())
    assert "## Failing queries" not in out


def test_no_em_dashes():
    out = markdown_renderer.render(_scorecard({"warnings": ["w"], "failing_queries": []}))
    assert "—" not in out and "–" not in out


def test_pipe_in_message_escaped():
    rubric = _rubric()
    results = [CheckResult("c_dimension_consistency", False, 0.0, "critical", {}, "a | b problem")]
    card = compute_score(rubric, results, metadata={})
    out = markdown_renderer.render(card)
    assert "a \\| b problem" in out
