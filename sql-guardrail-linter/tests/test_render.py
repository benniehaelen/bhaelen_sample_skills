"""Tests for render_report.py: Markdown, HTML, and the gate-replay CLI."""

from __future__ import annotations

import json

from _parser import parse_statement
from _controls import LintContext
import _scoring as scoring
import render_report as rr


def _report(rubric, **ctx_kwargs):
    stmt = parse_statement("SELECT ssn FROM proj.d.patients ORDER BY ssn")
    ctx = LintContext(pii_policy={"proj.d.patients": ["ssn"]}, **ctx_kwargs)
    card = scoring.score_statement(stmt, ctx, rubric, statement_id="stmt_0001", source="inline")
    return scoring.build_report(
        statements=[card], rubric=rubric, mode="static",
        scope={"source": "sql", "statement_count": 1}, gate_severity="high",
        linted_at="2026-06-03T00:00:00+00:00",
        rubric_config={"source": "builtin", "name": "nl2sql-guardrail-default", "version": "1.0", "sha256": ""},
        warnings=["a warning"],
    )


def test_markdown_has_sections(rubric):
    md = rr.make_markdown(_report(rubric))
    assert "# SQL Guardrail Report" in md
    assert "## Summary" in md
    assert "## Top fixes" in md
    assert "stmt_0001" in md
    assert "SQ-010" in md
    assert "## Warnings" in md


def test_markdown_groups_by_tier(rubric):
    md = rr.make_markdown(_report(rubric))
    assert "**Tier 1**" in md
    assert "**Tier 2**" in md
    assert "**Tier 3**" in md


def test_html_is_self_contained(rubric):
    html = rr.make_html(_report(rubric), theme="auto")
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html
    assert "<script" not in html  # no JavaScript
    assert "http://" not in html and "https://" not in html  # no external assets
    assert "stmt_0001" in html


def test_html_themes():
    for theme in ("light", "dark", "auto"):
        css = rr._theme_css(theme)
        assert "--bg" in css
    # dark palette ships inside auto for prefers-color-scheme.
    assert "prefers-color-scheme: dark" in rr._theme_css("auto")


def test_bytes_bar_only_when_estimate_present(rubric):
    static_html = rr.make_html(_report(rubric))
    assert '<div class="bytes-bar">' not in static_html
    cost_html = rr.make_html(_report(rubric, mode="cost_aware", estimated_bytes=12345, bytes_available=True))
    assert '<div class="bytes-bar">' in cost_html


def test_renderer_replays_min_score_gate(tmp_path, rubric):
    report = _report(rubric)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    out_md = tmp_path / "out.md"
    rc = rr.main(["--input", str(path), "--output-md", str(out_md), "--expect-min-score", "90"])
    assert rc == 3  # the PII-failing statement scores below 90


def test_renderer_clean_gate_passes(tmp_path, rubric):
    report = _report(rubric)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    out_md = tmp_path / "out.md"
    rc = rr.main(["--input", str(path), "--output-md", str(out_md), "--expect-min-score", "0"])
    assert rc == 0
    assert out_md.exists()
