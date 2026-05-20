"""Tests for the scoring formula, catalog roll-up, and top-refactor ranking.

The roll-up math is tested directly against hand-computed values using
synthetic scored-tool dicts, so it does not depend on tiktoken. A separate
integration test exercises the full ``score_catalog`` pipeline and asserts
internal self-consistency.
"""

from __future__ import annotations

import importlib.util

import pytest

import _scoring

_HAS_TIKTOKEN = importlib.util.find_spec("tiktoken") is not None
needs_tiktoken = pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")


# ----- grade_from_score ----------------------------------------------------


@pytest.mark.parametrize("score,grade", [
    (100, "A"), (90, "A"), (89, "B"), (80, "B"), (79, "C"),
    (70, "C"), (69, "D"), (60, "D"), (59, "F"), (0, "F"),
])
def test_grade_from_score_boundaries(score, grade, rubric):
    assert _scoring.grade_from_score(score, rubric["grade_cutoffs"]) == grade


# ----- catalog grade weighting (hand-computed) -----------------------------


def _scored(name, score, total_tokens):
    return {"name": name, "score": score, "tokens": {"total": total_tokens}}


def test_catalog_grade_by_token_cost(rubric):
    # Weighted mean: (90*100 + 50*300) / 400 = 24000/400 = 60 -> grade D.
    tools = [_scored("a", 90, 100), _scored("b", 50, 300)]
    score, grade = _scoring._catalog_grade(tools, rubric)
    assert score == 60
    assert grade == "D"


def test_catalog_grade_equal_weighting(rubric):
    # Equal mean: (90 + 50) / 2 = 70 -> grade C.
    rubric["catalog_grade_weighting"] = "equal"
    tools = [_scored("a", 90, 100), _scored("b", 50, 300)]
    score, grade = _scoring._catalog_grade(tools, rubric)
    assert score == 70
    assert grade == "C"


def test_catalog_grade_empty(rubric):
    assert _scoring._catalog_grade([], rubric) == (0, "F")


# ----- addressable savings split -------------------------------------------


def test_addressable_savings_splits_by_tier():
    tools = [{
        "criteria": [
            {"tier": 1, "passed": False, "tokens_saved": 100},
            {"tier": 1, "passed": True, "tokens_saved": 0},
            {"tier": 2, "passed": False, "tokens_saved": 550},
            {"tier": 3, "passed": False, "tokens_saved": 0},
        ],
    }]
    measured, estimated = _scoring._addressable_savings(tools)
    assert measured == 100   # Tier 1 only
    assert estimated == 550  # Tier 2 only


# ----- top offenders -------------------------------------------------------


def test_top_offenders_orders_and_percents():
    tools = [_scored("small", 80, 100), _scored("big", 40, 300)]
    offenders = _scoring._top_offenders(tools, total_tokens=400)
    assert [o["tool"] for o in offenders] == ["big", "small"]
    assert offenders[0]["pct_of_catalog"] == 75.0
    assert offenders[1]["pct_of_catalog"] == 25.0


# ----- top refactors -------------------------------------------------------


def test_top_refactors_sorted_and_capped():
    def tool_with(name, saved_values):
        return {
            "name": name,
            "criteria": [
                {"name": f"c{i}", "tier": 1, "passed": s == 0, "tokens_saved": s, "estimated": False, "evidence": ""}
                for i, s in enumerate(saved_values)
            ],
            "issues": [],
        }
    tools = [tool_with("t", list(range(0, 130, 10)))]  # savings 0,10,...,120
    refactors = _scoring._top_refactors(tools)
    assert len(refactors) == 10  # capped
    saved = [r["tokens_saved"] for r in refactors]
    assert saved == sorted(saved, reverse=True)  # descending
    assert saved[0] == 120
    assert 0 not in saved  # zero-savings criteria excluded


def test_top_refactors_carries_suggestion():
    tools = [{
        "name": "create_invoice",
        "criteria": [{"name": "schema_compact", "tier": 1, "passed": False,
                      "tokens_saved": 50, "estimated": False, "evidence": "too big"}],
        "issues": [{"criterion": "schema_compact", "tier": 1, "message": "too big", "suggestion": "Trim the schema."}],
    }]
    refactors = _scoring._top_refactors(tools)
    assert refactors[0]["suggestion"] == "Trim the schema."


# ----- warnings ------------------------------------------------------------


def test_warnings_resources_and_tokenizer(rubric):
    cat = {"extras": {"resource_count": 3, "prompt_count": 1}}
    warnings = _scoring._build_warnings(cat, "cl100k_base", rubric)
    assert any("3 resource(s)" in w and "1 prompt(s)" in w for w in warnings)
    assert any("cl100k_base" in w for w in warnings)


def test_warnings_unknown_weighting(rubric):
    rubric["catalog_grade_weighting"] = "bogus"
    warnings = _scoring._build_warnings({"extras": {}}, "claude", rubric)
    assert any("bogus" in w for w in warnings)


# ----- full pipeline integration -------------------------------------------


@needs_tiktoken
def test_score_catalog_self_consistent(rubric):
    catalog = {
        "server": {"name": "demo", "version": "1.0", "source": "test://demo"},
        "tools": [
            {
                "name": "create_invoice",
                "description": "This tool allows you to create an invoice. " * 4,
                "inputSchema": {"type": "object", "properties": {"customer_id": {"type": "string"}}},
                "annotations": {},
            },
            {
                "name": "search_orders",
                "description": "Search orders by status. Use this when you need to find existing orders.",
                "inputSchema": {"type": "object", "properties": {
                    "status": {"type": "string", "description": "Order status."}}},
                "annotations": {"readOnlyHint": True},
            },
        ],
        "extras": {"resource_count": 0, "prompt_count": 0},
    }
    report = _scoring.score_catalog(catalog, rubric, "cl100k_base", "raw")

    # Every tool's score equals round(100 * sum(points) / sum(max)) over its criteria.
    for t in report["tools"]:
        pts = sum(c["points"] for c in t["criteria"])
        mx = sum(c["max"] for c in t["criteria"])
        expected = round(100 * pts / mx) if mx else 0
        assert t["score"] == expected, f"{t['name']}: {t['score']} != {expected}"
        assert t["grade"] == _scoring.grade_from_score(t["score"], rubric["grade_cutoffs"])
        # N/A criteria are excluded from the rendered criteria list.
        assert all(c["max"] > 0 for c in t["criteria"])

    # Tools are sorted worst-first.
    scores = [t["score"] for t in report["tools"]]
    assert scores == sorted(scores)

    # Catalog total equals the sum of per-tool totals.
    assert report["catalog"]["total_tokens"] == sum(t["tokens"]["total"] for t in report["tools"])

    # Provenance and required top-level keys.
    assert report["rubric_config"]["source"] == "builtin"
    for key in ("catalog", "tools", "warnings", "expectations", "scored_at", "tokenizer", "serialization"):
        assert key in report
