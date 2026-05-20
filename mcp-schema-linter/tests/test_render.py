"""Smoke tests for the Markdown and HTML renderer.

The HTML test asserts the document parses and contains the token-cost
badge text and the top-refactor table, per the renderer's contract. The
renderer is fed a hand-built report so these tests do not depend on
scoring or tiktoken.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

import render_scorecard as rs


def sample_report() -> dict:
    """A minimal but complete report dict matching SKILL.md Step 6 shape."""
    return {
        "rubric_version": "1.0",
        "rubric_config": {"source": "builtin", "name": "token-economy-default", "version": "1.0", "sha256": ""},
        "tokenizer": "cl100k_base",
        "serialization": "raw",
        "scored_at": "2026-05-20T00:00:00+00:00",
        "server": {"name": "billing-api", "version": "2.1.0", "source": "https://billing.example.com/mcp"},
        "catalog": {
            "tool_count": 2,
            "total_tokens": 315,
            "addressable_savings_measured": 56,
            "addressable_savings_estimated": 350,
            "catalog_score": 73,
            "catalog_grade": "C",
            "top_offenders": [
                {"tool": "create_invoice", "tokens": 226, "pct_of_catalog": 71.7},
                {"tool": "search_orders", "tokens": 89, "pct_of_catalog": 28.3},
            ],
            "top_refactors": [
                {"tool": "create_invoice", "criterion": "description_length_appropriate", "tier": 1,
                 "tokens_saved": 51, "estimated": False, "message": "201 tokens; over the sweet spot.",
                 "suggestion": "Trim the description."},
                {"tool": "create_invoice", "criterion": "param_format_specified", "tier": 2,
                 "tokens_saved": 350, "estimated": True, "message": "No formats stated.",
                 "suggestion": "State the UUID format."},
            ],
        },
        "tools": [
            {
                "name": "create_invoice", "score": 62, "grade": "D",
                "tokens": {"name": 2, "description": 201, "schema": 23, "annotations": 0, "total": 226},
                "annotations": {"destructiveHint": True},
                "criteria": [
                    {"name": "description_length_appropriate", "tier": 1, "points": 0, "max": 2,
                     "passed": False, "evidence": "201 tokens", "tokens_saved": 51, "estimated": False},
                    {"name": "name_is_verb_oriented", "tier": 3, "points": 2, "max": 2,
                     "passed": True, "evidence": "starts with create", "tokens_saved": 0, "estimated": False},
                ],
                "issues": [
                    {"criterion": "description_length_appropriate", "tier": 1,
                     "message": "201 tokens", "suggestion": "Trim the description."},
                ],
            },
            {
                "name": "search_orders", "score": 100, "grade": "A",
                "tokens": {"name": 2, "description": 60, "schema": 27, "annotations": 0, "total": 89},
                "annotations": {"readOnlyHint": True},
                "criteria": [
                    {"name": "has_when_to_use", "tier": 2, "points": 2, "max": 2,
                     "passed": True, "evidence": "Use this when", "tokens_saved": 0, "estimated": False},
                ],
                "issues": [],
            },
        ],
        "expectations": [],
        "warnings": ["Tokenizer is cl100k_base; absolute counts vary ~10-20% for Claude. Relative ranking is stable."],
    }


def _assert_parses(html_text: str) -> None:
    """Feed the HTML through the stdlib parser; raises on malformed markup."""
    class _Strict(HTMLParser):
        def error(self, message):  # pragma: no cover - only fires on bad markup
            raise AssertionError(message)
    _Strict().feed(html_text)


# ----- HTML ----------------------------------------------------------------


def test_html_parses_and_has_token_badge():
    out = rs.make_html(sample_report(), theme="auto")
    _assert_parses(out)
    assert "token-badge" in out
    assert "tokens / turn" in out          # the badge label
    assert ">315<" in out                  # the headline token count


def test_html_has_top_refactor_table_with_savings_column():
    out = rs.make_html(sample_report())
    assert "Top refactor opportunities" in out
    assert "Tokens saved" in out
    assert "Top offenders by token cost" in out
    # Estimated savings carry the inline "est." marker.
    assert "savings-est" in out


def test_html_has_grade_pills_and_tool_anchors():
    out = rs.make_html(sample_report())
    assert "grade-pill" in out
    assert 'id="tool-create-invoice"' in out
    assert 'id="tool-search-orders"' in out
    assert "tier-badge" in out


def test_html_has_methodology_footnote():
    out = rs.make_html(sample_report())
    assert "Methodology" in out
    assert "cl100k_base" in out


@pytest.mark.parametrize("theme", ["auto", "light", "dark"])
def test_html_themes_parse(theme):
    out = rs.make_html(sample_report(), theme=theme)
    _assert_parses(out)
    if theme == "dark":
        assert "prefers-color-scheme" not in out  # dark bakes a single palette
    if theme == "auto":
        assert "prefers-color-scheme: dark" in out


def test_html_unknown_theme_raises():
    with pytest.raises(ValueError, match="Unknown theme"):
        rs.make_html(sample_report(), theme="neon")


def test_html_empty_catalog():
    report = sample_report()
    report["tools"] = []
    out = rs.make_html(report)
    _assert_parses(out)
    assert "No tools scored." in out


# ----- Markdown ------------------------------------------------------------


def test_markdown_has_sections():
    out = rs.make_markdown(sample_report())
    assert out.startswith("# MCP Schema Scorecard")
    assert "## Catalog" in out
    assert "Total tokens:** 315" in out
    assert "### Top refactor opportunities" in out
    assert "Tokens saved" in out


def test_markdown_lists_tools_worst_first():
    out = rs.make_markdown(sample_report())
    # create_invoice (62) appears before search_orders (100).
    assert out.index("`create_invoice`") < out.index("`search_orders`")


def test_markdown_no_em_dashes():
    out = rs.make_markdown(sample_report())
    assert "—" not in out and "–" not in out


def test_markdown_accepts_theme_kwarg():
    # theme is accepted for API symmetry with make_html and ignored.
    a = rs.make_markdown(sample_report(), theme="dark")
    b = rs.make_markdown(sample_report(), theme="light")
    assert a == b


# ----- gate evaluation -----------------------------------------------------


def test_evaluate_gates_max_tokens_fail():
    gates = rs._evaluate_gates(sample_report(), expect_max_tokens=100, expect_min_score=None)
    g = next(x for x in gates if x["name"] == "expect_max_tokens")
    assert not g["passed"] and g["actual"] == 315


def test_evaluate_gates_min_score_fail_lists_offenders():
    gates = rs._evaluate_gates(sample_report(), expect_max_tokens=None, expect_min_score=90)
    g = next(x for x in gates if x["name"] == "expect_min_score")
    assert not g["passed"]
    assert "create_invoice" in g["offenders"]
    assert "search_orders" not in g["offenders"]


def test_evaluate_gates_both_pass():
    gates = rs._evaluate_gates(sample_report(), expect_max_tokens=1000, expect_min_score=50)
    assert all(g["passed"] for g in gates)
