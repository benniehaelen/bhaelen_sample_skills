#!/usr/bin/env python3
"""Render an MCP schema linter scorecard from a JSON report.

This module is the single source of truth for both:

- **Path A** (agent-driven): the CLI ``python render_scorecard.py --input
  scorecard.json --output-md scorecard.md --output-html scorecard.html``.
  The renderer has no MCP dependency, so an agent can assemble the report
  dict, dump it as JSON, and pipe it through this script regardless of
  what environment scored the tools.
- **Path B** (bundled CLI): ``lint_mcp_schema.py`` imports ``make_markdown``
  and ``make_html`` from this module so both paths produce identical output.

HTML output is a single self-contained file with inline CSS. No external
assets, no JavaScript. Theming uses CSS custom properties only.

Layout (HTML):

1. Catalog summary card: token-cost badge, catalog score + grade, savings,
   server provenance, methodology footnote anchor.
2. Expectations and warnings strips when present.
3. Top offenders table (5 highest-token tools).
4. Top refactor opportunities table (10 highest-tokens-saved fails).
5. Per-tool cards, worst-first: score + grade pill, token breakdown line,
   MCP annotation chips, collapsible description, criteria grouped by
   tier, issues list.
6. Methodology footnote.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# CSS tokens and base styles
# ---------------------------------------------------------------------------

_HTML_LIGHT_TOKENS = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --border: #e5e7eb; --card: #f9fafb;
  --accent: #2563eb;
  --grade-a-bg: #ecfdf5; --grade-a-fg: #065f46;
  --grade-b-bg: #ecfeff; --grade-b-fg: #155e75;
  --grade-c-bg: #fffbeb; --grade-c-fg: #92400e;
  --grade-d-bg: #ffedd5; --grade-d-fg: #9a3412;
  --grade-f-bg: #fef2f2; --grade-f-fg: #991b1b;
  --pill-pass-bg: #ecfdf5; --pill-pass-fg: #065f46; --pill-pass-dot: #16a34a;
  --pill-partial-bg: #fffbeb; --pill-partial-fg: #92400e; --pill-partial-dot: #d97706;
  --pill-fail-bg: #fef2f2; --pill-fail-fg: #991b1b; --pill-fail-dot: #dc2626;
  --pill-na-bg: #f3f4f6; --pill-na-fg: #6b7280; --pill-na-dot: #9ca3af;
  --warn-bg: #fffbeb; --warn-border: #fde68a;
  --bar-track: #e5e7eb; --bar-fill: #2563eb;
  --token-badge-bg: #eff6ff; --token-badge-fg: #1e40af; --token-badge-border: #bfdbfe;
  --measured-fg: #065f46; --estimated-fg: #92400e;
  --tier-1-bg: #fef2f2; --tier-1-fg: #991b1b;
  --tier-2-bg: #fffbeb; --tier-2-fg: #92400e;
  --tier-3-bg: #f3f4f6; --tier-3-fg: #4b5563;
}
""".strip()

_HTML_DARK_TOKENS = """
:root {
  --bg: #0f172a; --fg: #e2e8f0; --muted: #94a3b8; --border: #334155; --card: #1e293b;
  --accent: #60a5fa;
  --grade-a-bg: #064e3b; --grade-a-fg: #a7f3d0;
  --grade-b-bg: #0e3a4a; --grade-b-fg: #a5f3fc;
  --grade-c-bg: #422006; --grade-c-fg: #fde68a;
  --grade-d-bg: #431407; --grade-d-fg: #fed7aa;
  --grade-f-bg: #7f1d1d; --grade-f-fg: #fecaca;
  --pill-pass-bg: #064e3b; --pill-pass-fg: #a7f3d0; --pill-pass-dot: #34d399;
  --pill-partial-bg: #422006; --pill-partial-fg: #fde68a; --pill-partial-dot: #fbbf24;
  --pill-fail-bg: #7f1d1d; --pill-fail-fg: #fecaca; --pill-fail-dot: #f87171;
  --pill-na-bg: #1e293b; --pill-na-fg: #94a3b8; --pill-na-dot: #64748b;
  --warn-bg: #422006; --warn-border: #92400e;
  --bar-track: #334155; --bar-fill: #60a5fa;
  --token-badge-bg: #0c2849; --token-badge-fg: #93c5fd; --token-badge-border: #1e3a8a;
  --measured-fg: #6ee7b7; --estimated-fg: #fcd34d;
  --tier-1-bg: #3f1212; --tier-1-fg: #fca5a5;
  --tier-2-bg: #3b2106; --tier-2-fg: #fcd34d;
  --tier-3-bg: #1e293b; --tier-3-fg: #cbd5e1;
}
""".strip()

_HTML_BASE_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 0; }
h3 { font-size: 13px; margin: 14px 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.subtitle { color: var(--muted); margin: 0 0 24px; font-size: 13px; }

/* Catalog summary card with the prominent token-cost badge. */
.catalog-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 20px 24px; margin-bottom: 20px;
}
.catalog-head {
  display: flex; justify-content: space-between; align-items: center; gap: 20px;
  flex-wrap: wrap; padding-bottom: 14px; border-bottom: 1px solid var(--border);
}
.catalog-title { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 15px; word-break: break-all; }
.catalog-source { color: var(--muted); font-size: 12px; margin-top: 2px; }
.token-badge {
  background: var(--token-badge-bg); color: var(--token-badge-fg);
  border: 1px solid var(--token-badge-border); border-radius: 10px;
  padding: 10px 16px; display: inline-flex; align-items: baseline; gap: 8px;
  font-variant-numeric: tabular-nums;
}
.token-badge-num { font-size: 28px; font-weight: 700; line-height: 1; }
.token-badge-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; }
.catalog-metrics {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px; padding-top: 14px;
}
.metric { display: flex; flex-direction: column; gap: 2px; }
.metric-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.metric-value { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.metric-value.measured { color: var(--measured-fg); }
.metric-value.estimated { color: var(--estimated-fg); }

/* Grade pills (shared with score-table-metadata). */
.grade-pill {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 36px; height: 36px; padding: 0 12px;
  border-radius: 8px; font-weight: 700; font-size: 16px;
  border: 1px solid transparent;
}
.grade-A { background: var(--grade-a-bg); color: var(--grade-a-fg); }
.grade-B { background: var(--grade-b-bg); color: var(--grade-b-fg); }
.grade-C { background: var(--grade-c-bg); color: var(--grade-c-fg); }
.grade-D { background: var(--grade-d-bg); color: var(--grade-d-fg); }
.grade-F { background: var(--grade-f-bg); color: var(--grade-f-fg); }

/* Pass/partial/fail/na pills (shared). */
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
  white-space: nowrap;
}
.pill .dot { width: 6px; height: 6px; border-radius: 50%; }
.pill.pass { background: var(--pill-pass-bg); color: var(--pill-pass-fg); }
.pill.pass .dot { background: var(--pill-pass-dot); }
.pill.partial { background: var(--pill-partial-bg); color: var(--pill-partial-fg); }
.pill.partial .dot { background: var(--pill-partial-dot); }
.pill.fail { background: var(--pill-fail-bg); color: var(--pill-fail-fg); }
.pill.fail .dot { background: var(--pill-fail-dot); }
.pill.na { background: var(--pill-na-bg); color: var(--pill-na-fg); }
.pill.na .dot { background: var(--pill-na-dot); }

/* Tier badges. */
.tier-badge {
  display: inline-flex; align-items: center; padding: 1px 6px;
  border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
}
.tier-badge.t1 { background: var(--tier-1-bg); color: var(--tier-1-fg); }
.tier-badge.t2 { background: var(--tier-2-bg); color: var(--tier-2-fg); }
.tier-badge.t3 { background: var(--tier-3-bg); color: var(--tier-3-fg); }

/* Generic table styles for offenders / refactors. */
.summary-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.summary-table th, .summary-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.summary-table tr:last-child td { border-bottom: none; }
.summary-table th { font-weight: 600; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
.summary-table td.num { font-variant-numeric: tabular-nums; text-align: right; width: 1%; white-space: nowrap; }
.summary-table td.tool-cell { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.summary-table td.tool-cell a { color: inherit; text-decoration: none; }
.summary-table td.tool-cell a:hover { text-decoration: underline; color: var(--accent); }
.savings-est { color: var(--muted); font-size: 10px; margin-left: 4px; }
.section-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 18px; margin-bottom: 16px;
}
.section-card h2 { font-size: 14px; margin: 0 0 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }

/* Per-tool scorecard. */
.scorecard {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 20px; margin-bottom: 16px; scroll-margin-top: 16px;
}
.sc-head {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--border);
}
.sc-name { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 15px; word-break: break-all; }
.sc-score { display: flex; align-items: center; gap: 12px; }
.sc-num { font-size: 28px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.sc-num .of { color: var(--muted); font-size: 14px; font-weight: 500; }
.token-line {
  font-size: 12px; color: var(--muted); margin: 10px 0 6px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  display: flex; flex-wrap: wrap; gap: 14px;
}
.token-line strong { color: var(--fg); }
.annotation-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.annotation-chip { padding: 2px 8px; border-radius: 4px; background: var(--bg); border: 1px solid var(--border); font-size: 11px; font-family: ui-monospace, "SF Mono", Menlo, monospace; color: var(--muted); }
.annotation-chip strong { color: var(--fg); }

/* Description panel. */
details.desc-panel { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0 12px; margin: 12px 0; }
details.desc-panel > summary { padding: 8px 0; cursor: pointer; font-size: 12px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
details.desc-panel[open] > summary { border-bottom: 1px solid var(--border); margin-bottom: 8px; }
.desc-body { padding: 4px 0 10px; font-size: 13px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.desc-body.empty { color: var(--muted); font-style: italic; }

/* Criteria list grouped by tier. */
.tier-group { margin: 8px 0; }
.tier-group h4 { font-size: 11px; margin: 8px 0 4px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; }
.crit-list { display: grid; gap: 6px; margin: 4px 0 8px; }
.crit-row { display: grid; grid-template-columns: max-content 1fr max-content; align-items: center; gap: 10px; padding: 6px 0; }
.crit-name { font-weight: 500; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.crit-evidence { color: var(--muted); font-size: 12px; word-break: break-word; }

/* Issues callout. */
.issues { background: var(--warn-bg); border: 1px solid var(--warn-border); border-radius: 8px; padding: 10px 14px; margin-top: 12px; }
.issues h3 { margin-top: 0; color: inherit; }
.issues ul { margin: 0; padding-left: 18px; font-size: 13px; }
.issues li { margin: 6px 0; }
.issue-suggestion { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; font-style: italic; }
.issue-suggestion::before { content: "Suggested: "; font-weight: 600; font-style: normal; }
.no-issues { color: var(--muted); font-size: 12px; font-style: italic; padding: 4px 0; }

/* Expectations strip. */
.exp-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }

/* Methodology footnote. */
.footnote { font-size: 11px; color: var(--muted); margin-top: 24px; padding: 12px 14px; border-top: 1px dashed var(--border); }
.footnote strong { color: var(--fg); }
""".strip()


def _theme_css(theme: str) -> str:
    """Assemble the CSS for the requested theme.

    ``light`` and ``dark`` bake in a single palette; ``auto`` ships both
    palettes and switches via ``prefers-color-scheme`` so the dashboard
    follows the viewer's OS preference.
    """
    if theme == "light":
        tokens = _HTML_LIGHT_TOKENS
    elif theme == "dark":
        tokens = _HTML_DARK_TOKENS
    elif theme == "auto":
        dark_override = "@media (prefers-color-scheme: dark) {\n" + _HTML_DARK_TOKENS + "\n}"
        tokens = _HTML_LIGHT_TOKENS + "\n" + dark_override
    else:
        raise ValueError(f"Unknown theme {theme!r}; expected one of: auto, light, dark.")
    return tokens + "\n" + _HTML_BASE_CSS


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    """HTML-escape a value for safe interpolation."""
    return html.escape("" if value is None else str(value), quote=True)


def _grade_class(grade: str) -> str:
    return f"grade-{grade}" if grade in ("A", "B", "C", "D", "F") else "grade-F"


def _criterion_status(c: dict[str, Any]) -> str:
    """``pass`` / ``partial`` / ``fail`` / ``na`` for a criterion dict."""
    if c.get("max", 0) == 0:
        return "na"
    pts = c.get("points", 0)
    mx = c.get("max", 0)
    if pts >= mx:
        return "pass"
    if pts > 0:
        return "partial"
    return "fail"


def _rubric_descriptor(report: dict[str, Any]) -> str:
    """Short human-readable string identifying the rubric used."""
    rc = report.get("rubric_config")
    rubric_v = report.get("rubric_version", "1.0")
    if not isinstance(rc, dict):
        return f"rubric v{rubric_v}"
    name = rc.get("name", "rubric")
    version = rc.get("version", rubric_v)
    source = rc.get("source", "builtin")
    sha = (rc.get("sha256") or "")[:8]
    if source == "builtin":
        return f"{name} v{version} (builtin)"
    return f"{name} v{version} ({source} @ {sha})" if sha else f"{name} v{version} ({source})"


def _server_descriptor(report: dict[str, Any]) -> tuple[str, str]:
    """Title and source line for the catalog summary card."""
    server = report.get("server") or {}
    name = server.get("name") or "(unnamed server)"
    version = server.get("version")
    source = server.get("source") or ""
    title = f"{name} v{version}" if version else name
    return title, source


def _tool_slug(name: str | None) -> str:
    """Stable HTML anchor id for a tool name."""
    text = (name or "tool").lower()
    out = [ch if ch.isalnum() else "-" for ch in text]
    slug = "".join(out).strip("-")
    return f"tool-{slug}" if slug else "tool-unknown"


def _sorted_tools(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Worst-first (lowest score, ties broken by highest token cost).

    The scoring layer already sorts the tools list this way; this helper
    is a safety net for reports built by hand or by older agents.
    """
    tools = list(report.get("tools") or [])
    tools.sort(key=lambda t: (t.get("score", 100), -((t.get("tokens") or {}).get("total", 0))))
    return tools


def _issue_parts(issue: Any) -> tuple[str, str]:
    """Return (message, suggestion) for either string- or dict-shaped issues."""
    if isinstance(issue, dict):
        return str(issue.get("message", "")), str(issue.get("suggestion", ""))
    return str(issue), ""


def _format_savings(saved: int, estimated: bool) -> str:
    """Render a tokens_saved figure with an `est.` marker when estimated."""
    if estimated:
        return f"{saved}<span class=\"savings-est\">est.</span>"
    return f"{saved}"


_METHODOLOGY_TEXT = (
    "Token counts are an approximation. Different model providers tokenize the same string "
    "slightly differently, and the way each provider serializes a tool block into the final "
    "prompt varies. This skill defaults to the cl100k_base tokenizer (GPT-4 family) because "
    "it is portable and offline, and serializes tool definitions as compact JSON. Both choices "
    "are documented assumptions, not ground truth. What is robust across tokenizers is the "
    "relative ranking of tools and the proportional savings from each refactor."
)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _md_status(c: dict[str, Any]) -> str:
    """Markdown-friendly status label for a criterion."""
    return {"pass": "[pass]", "partial": "[partial]", "fail": "[fail]", "na": "[n/a]"}[_criterion_status(c)]


def _md_escape_cell(text: str) -> str:
    """Escape a value for safe placement in a Markdown table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def make_markdown(report: dict[str, Any], *, theme: str = "auto") -> str:
    """Render a scorecard report as a Markdown document.

    ``theme`` is accepted for API symmetry with ``make_html`` and ignored.
    """
    del theme  # markdown has no theme

    lines: list[str] = []
    catalog = report.get("catalog") or {}
    server_title, server_source = _server_descriptor(report)

    lines.append("# MCP Schema Scorecard")
    lines.append("")
    rubric_desc = _rubric_descriptor(report)
    tokenizer = report.get("tokenizer", "cl100k_base")
    serialization = report.get("serialization", "raw")
    scored_at = report.get("scored_at", "")
    lines.append(f"_Server: `{server_title}` · {rubric_desc} · tokenizer `{tokenizer}` / serialization `{serialization}`_")
    if scored_at:
        lines.append(f"_Scored: {scored_at}_")
    if server_source:
        lines.append(f"_Source: `{server_source}`_")
    lines.append("")

    # Expectations and warnings.
    expectations = report.get("expectations") or []
    if expectations:
        lines.append("## Expectations")
        for exp in expectations:
            status = "[pass]" if exp.get("passed") else "[fail]"
            name = exp.get("name", "")
            limit = exp.get("limit", "")
            actual = exp.get("actual", "")
            lines.append(f"- **{name}** {status} (limit {limit}, actual {actual}): {exp.get('message', '')}")
        lines.append("")

    warnings = report.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Catalog summary.
    lines.append("## Catalog")
    lines.append("")
    total_tokens = catalog.get("total_tokens", 0)
    catalog_score = catalog.get("catalog_score", 0)
    catalog_grade = catalog.get("catalog_grade", "?")
    measured = catalog.get("addressable_savings_measured", 0)
    estimated = catalog.get("addressable_savings_estimated", 0)
    tool_count = catalog.get("tool_count", 0)
    lines.append(f"- **Total tokens:** {total_tokens}")
    lines.append(f"- **Catalog grade:** {catalog_score}/100 ({catalog_grade})")
    lines.append(f"- **Tool count:** {tool_count}")
    lines.append(f"- **Addressable savings:** {measured} measured, {estimated} estimated")
    lines.append("")

    # Top offenders.
    top_offenders = catalog.get("top_offenders") or []
    if top_offenders:
        lines.append("### Top offenders by token cost")
        lines.append("")
        lines.append("| Tool | Tokens | % of catalog |")
        lines.append("| --- | ---: | ---: |")
        for o in top_offenders:
            lines.append(f"| `{o.get('tool')}` | {o.get('tokens', 0)} | {o.get('pct_of_catalog', 0)}% |")
        lines.append("")

    # Top refactor opportunities.
    top_refactors = catalog.get("top_refactors") or []
    if top_refactors:
        lines.append("### Top refactor opportunities")
        lines.append("")
        lines.append("| Tool | Criterion | Tier | Tokens saved | Suggestion |")
        lines.append("| --- | --- | :---: | ---: | --- |")
        for r in top_refactors:
            saved = r.get("tokens_saved", 0)
            saved_str = f"{saved} (est.)" if r.get("estimated") else f"{saved}"
            suggestion = _md_escape_cell(r.get("suggestion", ""))
            lines.append(
                f"| `{r.get('tool')}` | `{r.get('criterion')}` | T{r.get('tier', '?')} | "
                f"{saved_str} | {suggestion} |"
            )
        lines.append("")

    # Per-tool sections, worst-first.
    sorted_tools = _sorted_tools(report)
    if sorted_tools:
        lines.append(f"## Tools ({len(sorted_tools)})")
        lines.append("")
    for tool in sorted_tools:
        name = tool.get("name", "(unnamed)")
        score = tool.get("score", 0)
        grade = tool.get("grade", "?")
        tokens = tool.get("tokens") or {}
        lines.append(f"### `{name}` {score}/100 ({grade})")
        lines.append("")
        lines.append(
            f"**Tokens:** name {tokens.get('name', 0)} · description {tokens.get('description', 0)}"
            f" · schema {tokens.get('schema', 0)} · annotations {tokens.get('annotations', 0)}"
            f" · **total {tokens.get('total', 0)}**"
        )
        lines.append("")
        annotations = tool.get("annotations") or {}
        if annotations:
            chips = " ".join(f"`{k}={v}`" for k, v in annotations.items())
            lines.append(f"**Annotations:** {chips}")
            lines.append("")

        # Criteria grouped by tier.
        criteria = tool.get("criteria") or []
        for tier in (1, 2, 3):
            tier_crit = [c for c in criteria if c.get("tier") == tier]
            if not tier_crit:
                continue
            lines.append(f"**Tier {tier}**")
            lines.append("")
            lines.append("| Criterion | Status | Evidence | Tokens saved |")
            lines.append("| --- | --- | --- | ---: |")
            for c in tier_crit:
                evidence = _md_escape_cell(c.get("evidence", ""))
                saved = c.get("tokens_saved", 0)
                saved_str = f"{saved} (est.)" if c.get("estimated") else (f"{saved}" if saved else "0")
                lines.append(f"| `{c.get('name')}` | {_md_status(c)} | {evidence} | {saved_str} |")
            lines.append("")

        issues = tool.get("issues") or []
        if issues:
            lines.append("**Issues**")
            lines.append("")
            for issue in issues:
                msg, suggestion = _issue_parts(issue)
                lines.append(f"- {msg}")
                if suggestion:
                    lines.append(f"  - _Suggested:_ {suggestion}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_Methodology: {_METHODOLOGY_TEXT}_")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _criterion_pill(c: dict[str, Any]) -> str:
    """Colored status pill for a criterion."""
    status = _criterion_status(c)
    label = {"pass": "pass", "partial": "partial", "fail": "fail", "na": "n/a"}[status]
    return f'<span class="pill {status}"><span class="dot"></span>{label} {c.get("points", 0)}/{c.get("max", 0)}</span>'


def _tier_badge(tier: int) -> str:
    """Small badge labeling a tier (T1/T2/T3)."""
    cls = f"t{tier}" if tier in (1, 2, 3) else "t3"
    return f'<span class="tier-badge {cls}">T{tier}</span>'


def _criteria_block(criteria: list[dict[str, Any]]) -> str:
    """Render criteria as tier-grouped pill rows."""
    blocks: list[str] = []
    for tier in (1, 2, 3):
        tier_crit = [c for c in criteria if c.get("tier") == tier]
        if not tier_crit:
            continue
        rows: list[str] = []
        for c in tier_crit:
            ev = _e(c.get("evidence", ""))
            rows.append(
                '<div class="crit-row">'
                f'<span class="crit-name"><code>{_e(c.get("name"))}</code></span>'
                f'<span class="crit-evidence">{ev}</span>'
                f'{_criterion_pill(c)}'
                '</div>'
            )
        tier_label = {1: "Tier 1 (direct cost)", 2: "Tier 2 (indirect cost)", 3: "Tier 3 (hygiene)"}[tier]
        blocks.append(
            '<div class="tier-group">'
            f'<h4>{tier_label}</h4>'
            f'<div class="crit-list">{"".join(rows)}</div>'
            '</div>'
        )
    return "".join(blocks)


def _description_panel(description: str | None, *, default_open: bool = False) -> str:
    """Collapsible description panel for a tool card."""
    desc = (description or "").strip()
    body_cls = "desc-body" if desc else "desc-body empty"
    body_text = _e(desc) if desc else "(no description)"
    open_attr = " open" if default_open else ""
    return (
        f'<details class="desc-panel"{open_attr}>'
        '<summary>Description</summary>'
        f'<div class="{body_cls}">{body_text}</div>'
        '</details>'
    )


def _annotation_chips(annotations: dict[str, Any]) -> str:
    """Inline chips for MCP annotation hints."""
    if not annotations:
        return ""
    items = "".join(
        f'<span class="annotation-chip"><strong>{_e(k)}</strong>: {_e(v)}</span>'
        for k, v in annotations.items()
    )
    return f'<div class="annotation-chips">{items}</div>'


def _token_breakdown(tokens: dict[str, Any]) -> str:
    """Single-line token breakdown for a tool card."""
    return (
        '<div class="token-line">'
        f'<span>name <strong>{tokens.get("name", 0)}</strong></span>'
        f'<span>description <strong>{tokens.get("description", 0)}</strong></span>'
        f'<span>schema <strong>{tokens.get("schema", 0)}</strong></span>'
        f'<span>annotations <strong>{tokens.get("annotations", 0)}</strong></span>'
        f'<span>total <strong>{tokens.get("total", 0)}</strong></span>'
        '</div>'
    )


def _catalog_summary_html(report: dict[str, Any]) -> str:
    """The top-of-page catalog summary card with the token-cost badge."""
    catalog = report.get("catalog") or {}
    server_title, server_source = _server_descriptor(report)
    score = catalog.get("catalog_score", 0)
    grade = catalog.get("catalog_grade", "?")
    grade_cls = _grade_class(grade)
    total_tokens = catalog.get("total_tokens", 0)
    measured = catalog.get("addressable_savings_measured", 0)
    estimated = catalog.get("addressable_savings_estimated", 0)
    tool_count = catalog.get("tool_count", 0)
    tokenizer = report.get("tokenizer", "cl100k_base")
    serialization = report.get("serialization", "raw")

    source_html = f'<div class="catalog-source">{_e(server_source)}</div>' if server_source else ""

    return (
        '<div class="catalog-card">'
        '<div class="catalog-head">'
        f'<div><h2 class="catalog-title">{_e(server_title)}</h2>{source_html}</div>'
        '<div style="display:flex; align-items:center; gap:14px;">'
        '<div class="token-badge">'
        f'<span class="token-badge-num">{total_tokens}</span>'
        '<span class="token-badge-label">tokens / turn</span>'
        '</div>'
        f'<div class="sc-num">{score}<span class="of"> / 100</span></div>'
        f'<span class="grade-pill {grade_cls}">{_e(grade)}</span>'
        '</div>'
        '</div>'
        '<div class="catalog-metrics">'
        f'<div class="metric"><span class="metric-label">Tools</span><span class="metric-value">{tool_count}</span></div>'
        f'<div class="metric"><span class="metric-label">Measured savings</span>'
        f'<span class="metric-value measured">{measured}</span></div>'
        f'<div class="metric"><span class="metric-label">Estimated savings</span>'
        f'<span class="metric-value estimated">{estimated}</span></div>'
        f'<div class="metric"><span class="metric-label">Tokenizer</span>'
        f'<span class="metric-value">{_e(tokenizer)} / {_e(serialization)}</span></div>'
        '</div>'
        '</div>'
    )


def _top_offenders_html(report: dict[str, Any]) -> str:
    offenders = (report.get("catalog") or {}).get("top_offenders") or []
    if not offenders:
        return ""
    rows = "".join(
        '<tr>'
        f'<td class="tool-cell"><a href="#{_tool_slug(o.get("tool"))}">{_e(o.get("tool"))}</a></td>'
        f'<td class="num">{o.get("tokens", 0)}</td>'
        f'<td class="num">{o.get("pct_of_catalog", 0)}%</td>'
        '</tr>'
        for o in offenders
    )
    return (
        '<div class="section-card">'
        '<h2>Top offenders by token cost</h2>'
        '<table class="summary-table">'
        '<thead><tr><th>Tool</th><th class="num">Tokens</th><th class="num">% of catalog</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        '</table>'
        '</div>'
    )


def _top_refactors_html(report: dict[str, Any]) -> str:
    refactors = (report.get("catalog") or {}).get("top_refactors") or []
    if not refactors:
        return ""
    rows_list: list[str] = []
    for r in refactors:
        saved_cell = _format_savings(int(r.get("tokens_saved", 0)), bool(r.get("estimated")))
        rows_list.append(
            '<tr>'
            f'<td class="tool-cell"><a href="#{_tool_slug(r.get("tool"))}">{_e(r.get("tool"))}</a></td>'
            f'<td><code>{_e(r.get("criterion"))}</code></td>'
            f'<td>{_tier_badge(int(r.get("tier", 0)))}</td>'
            f'<td class="num">{saved_cell}</td>'
            f'<td>{_e(r.get("suggestion", ""))}</td>'
            '</tr>'
        )
    return (
        '<div class="section-card">'
        '<h2>Top refactor opportunities</h2>'
        '<table class="summary-table">'
        '<thead><tr><th>Tool</th><th>Criterion</th><th>Tier</th>'
        '<th class="num">Tokens saved</th><th>Suggestion</th></tr></thead>'
        f'<tbody>{"".join(rows_list)}</tbody>'
        '</table>'
        '</div>'
    )


def _expectations_html(expectations: list[dict[str, Any]]) -> str:
    """Top-of-page strip of expectation pills."""
    if not expectations:
        return ""
    pills: list[str] = []
    for exp in expectations:
        passed = bool(exp.get("passed"))
        cls = "pass" if passed else "fail"
        name = exp.get("name", "")
        limit = exp.get("limit", "")
        actual = exp.get("actual", "")
        status = "passed" if passed else "failed"
        label = f"{name} (limit {limit}, actual {actual})"
        pills.append(
            f'<span class="pill {cls}"><span class="dot"></span>{_e(label)} [{status}]</span>'
        )
    return f'<div class="exp-list">{"".join(pills)}</div>'


def _warnings_html(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{_e(w)}</li>" for w in warnings)
    return f'<div class="issues"><h3>Warnings</h3><ul>{items}</ul></div>'


def _tool_card_html(tool: dict[str, Any]) -> str:
    """Per-tool scorecard card."""
    name = tool.get("name", "(unnamed)")
    score = tool.get("score", 0)
    grade = tool.get("grade", "?")
    grade_cls = _grade_class(grade)
    tokens = tool.get("tokens") or {}
    annotations = tool.get("annotations") or {}
    # The scored-tool report shape (SKILL.md Step 6) does not carry the raw
    # description text, only its token count and the criteria evidence. If a
    # report includes an optional "description" field (some Path A agents
    # add it), surface it; otherwise the panel is omitted.
    description = tool.get("description") or ""
    desc_panel = _description_panel(description) if description else ""

    # Issues.
    issues = tool.get("issues") or []
    issues_html = ""
    if issues:
        items: list[str] = []
        for issue in issues:
            msg, suggestion = _issue_parts(issue)
            sug_html = f'<span class="issue-suggestion">{_e(suggestion)}</span>' if suggestion else ""
            items.append(f"<li>{_e(msg)}{sug_html}</li>")
        issues_html = f'<div class="issues"><h3>Issues</h3><ul>{"".join(items)}</ul></div>'

    return (
        f'<div class="scorecard" id="{_tool_slug(name)}">'
        '<div class="sc-head">'
        f'<div><h2 class="sc-name"><code>{_e(name)}</code></h2></div>'
        '<div class="sc-score">'
        f'<div class="sc-num">{score}<span class="of"> / 100</span></div>'
        f'<span class="grade-pill {grade_cls}">{_e(grade)}</span>'
        '</div>'
        '</div>'
        f'{_token_breakdown(tokens)}'
        f'{_annotation_chips(annotations)}'
        f'{desc_panel}'
        f'{_criteria_block(tool.get("criteria") or [])}'
        f'{issues_html}'
        '</div>'
    )


def make_html(report: dict[str, Any], *, theme: str = "auto") -> str:
    """Render a scorecard report as a self-contained HTML document."""
    css = _theme_css(theme)
    server_title, _ = _server_descriptor(report)
    rubric_desc = _rubric_descriptor(report)
    tokenizer = report.get("tokenizer", "cl100k_base")
    serialization = report.get("serialization", "raw")
    scored_at = report.get("scored_at", "")

    sorted_tools = _sorted_tools(report)
    cards_html = "".join(_tool_card_html(t) for t in sorted_tools)
    if not cards_html:
        cards_html = '<div class="scorecard"><p class="no-issues">No tools scored.</p></div>'

    subtitle_parts = [_e(server_title), _e(rubric_desc), f"tokenizer {_e(tokenizer)} / {_e(serialization)}"]
    if scored_at:
        subtitle_parts.append(_e(scored_at))
    subtitle = " · ".join(subtitle_parts)

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>MCP Schema Scorecard</title>'
        f'<style>{css}</style>'
        '</head><body><div class="wrap">'
        '<h1>MCP Schema Scorecard</h1>'
        f'<p class="subtitle">{subtitle}</p>'
        f'{_expectations_html(report.get("expectations") or [])}'
        f'{_warnings_html(report.get("warnings") or [])}'
        f'{_catalog_summary_html(report)}'
        f'{_top_offenders_html(report)}'
        f'{_top_refactors_html(report)}'
        f'{cards_html}'
        f'<div class="footnote"><strong>Methodology.</strong> {_e(_METHODOLOGY_TEXT)}</div>'
        '</div></body></html>\n'
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _evaluate_gates(report: dict[str, Any], expect_max_tokens: int | None,
                    expect_min_score: int | None) -> list[dict[str, Any]]:
    """Replay the CLI's expectation gates against a loaded report.

    Replaces any existing expectations with the same names so a re-render
    reflects the new thresholds. Mirrors ``lint_mcp_schema.evaluate_expectations``
    without importing it (this script stays MCP-free).
    """
    out = [
        e for e in (report.get("expectations") or [])
        if e.get("name") not in ("expect_max_tokens", "expect_min_score")
    ]
    if expect_max_tokens is not None:
        total = int(report.get("catalog", {}).get("total_tokens", 0))
        passed = total <= expect_max_tokens
        out.append({
            "name": "expect_max_tokens",
            "limit": expect_max_tokens,
            "actual": total,
            "passed": passed,
            "message": (
                f"Catalog total {total} tokens within budget {expect_max_tokens}."
                if passed else
                f"Catalog total {total} tokens exceeds budget {expect_max_tokens}."
            ),
        })
    if expect_min_score is not None:
        tools = report.get("tools") or []
        offenders = [t.get("name", "") for t in tools if int(t.get("score", 0)) < expect_min_score]
        worst = min((int(t.get("score", 0)) for t in tools), default=100)
        passed = not offenders
        out.append({
            "name": "expect_min_score",
            "limit": expect_min_score,
            "actual": worst,
            "passed": passed,
            "offenders": offenders,
            "message": (
                f"All tools meet or exceed {expect_min_score}."
                if passed else
                f"{len(offenders)} tool(s) below {expect_min_score}: {', '.join(offenders[:5])}"
            ),
        })
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="render_scorecard.py",
        description="Render an MCP linter scorecard from a JSON report.",
    )
    p.add_argument("--input", required=True, help="Path to a JSON scorecard (use '-' for stdin)")
    p.add_argument("--output-md", default="scorecard.md", help="Markdown output path")
    p.add_argument("--output-html", default=None, help="Optional HTML output path")
    p.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="HTML theme")
    p.add_argument("--expect-max-tokens", type=int, default=None,
                   help="CI gate: exit 3 if catalog total_tokens exceeds N.")
    p.add_argument("--expect-min-score", type=int, default=None,
                   help="CI gate: exit 3 if any tool scores below N.")
    return p.parse_args(argv)


def _load_report(path: str) -> dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = _load_report(args.input)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"Error parsing report JSON: {exc}", file=sys.stderr)
        return 2

    if args.expect_max_tokens is not None or args.expect_min_score is not None:
        report["expectations"] = _evaluate_gates(report, args.expect_max_tokens, args.expect_min_score)

    Path(args.output_md).write_text(make_markdown(report, theme=args.theme), encoding="utf-8")
    print(f"Wrote {args.output_md}")

    if args.output_html:
        Path(args.output_html).write_text(make_html(report, theme=args.theme), encoding="utf-8")
        print(f"Wrote {args.output_html}")

    failed = [e for e in (report.get("expectations") or []) if not e.get("passed", True)]
    if failed:
        for e in failed:
            print(f"FAIL ({e.get('name', '')}): {e.get('message', '')}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
