"""Unit tests for the HTML dashboard renderer.

Builds a scorecard, renders it, and verifies the HTML parses and contains the
header, grade pill, per-tier bars, top fixes, status pills, and the failing-query
drill-down. Theme handling and the no-em-dash constraint are checked too.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from renderers import html_renderer
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
        CheckResult("c_no_exact_duplicates", False, 0.5, "warn", {}, "dupes"),
        CheckResult("c_recall_at_10", False, 0.4, "fail", {"value": 0.4}, "recall low"),
    ]
    meta = {"store": {"store_type": "pinecone", "index": "kb"}, "modes": ["evaluate"], "scored_at": "2026-01-01T00:00:00+00:00"}
    meta.update(metadata or {})
    return compute_score(rubric, results, metadata=meta)


def _parses(html_text: str) -> None:
    class _Strict(HTMLParser):
        def error(self, message):  # pragma: no cover
            raise AssertionError(message)
    _Strict().feed(html_text)


def test_parses_and_has_header():
    out = html_renderer.render(_scorecard(), theme="auto")
    _parses(out)
    assert "Vector Store Scorecard" in out
    assert "grade-pill" in out
    assert "store pinecone (kb)" in out


def test_per_tier_bars_and_pills():
    out = html_renderer.render(_scorecard())
    assert "Per-tier scores" in out
    assert "Tier 1: configuration" in out
    assert 'class="pill pass"' in out
    assert 'class="pill warn"' in out or 'class="pill fail"' in out


def test_top_fixes_present():
    out = html_renderer.render(_scorecard())
    assert "Top fixes" in out
    assert "c_recall_at_10" in out


def test_failing_query_drilldown():
    failing = [{
        "query": "what is the rate?",
        "query_type": "metric",
        "expected_ids": ["doc_1"],
        "retrieved": [{"id": "wrong_1", "score": 0.41}, {"id": "wrong_2", "score": 0.39}],
        "classifications": [{"mode": "vocabulary_mismatch", "confidence": 0.8, "explanation": "different words"}],
    }]
    out = html_renderer.render(_scorecard({"failing_queries": failing}))
    _parses(out)
    assert "Failing queries" in out
    assert "<details" in out and "what is the rate?" in out
    assert "wrong_1" in out and "0.4100" in out  # retrieved id and score
    assert "vocabulary_mismatch" in out
    assert "different words" in out


def test_warnings_callout():
    out = html_renderer.render(_scorecard({"warnings": ["synthetic ground truth in use"]}))
    assert "callout" in out
    assert "synthetic ground truth in use" in out


@pytest.mark.parametrize("theme", ["auto", "light", "dark"])
def test_themes_parse(theme):
    out = html_renderer.render(_scorecard(), theme=theme)
    _parses(out)
    if theme == "dark":
        assert "prefers-color-scheme" not in out
    if theme == "auto":
        assert "prefers-color-scheme: dark" in out


def test_unknown_theme_raises():
    with pytest.raises(ValueError, match="Unknown theme"):
        html_renderer.render(_scorecard(), theme="neon")


def test_no_em_dashes():
    out = html_renderer.render(_scorecard({"warnings": ["w"]}))
    assert "—" not in out and "–" not in out


def test_self_contained_no_external_assets():
    out = html_renderer.render(_scorecard())
    assert "<style>" in out
    assert "http://" not in out and "https://" not in out
    assert "<script" not in out  # no JavaScript
