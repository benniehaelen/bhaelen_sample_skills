#!/usr/bin/env python3
"""Render a SQL guardrail report from a JSON report.

Single source of truth for both:

- **Path A** (agent-driven): the CLI ``python render_report.py --input
  report.json --output-md report.md --output-html report.html``. The renderer
  has no BigQuery dependency, so an agent can assemble the report dict, dump it
  as JSON, and pipe it through this script in any environment.
- **Path B** (bundled CLI): ``lint_sql.py`` imports ``make_markdown`` and
  ``make_html`` from this module so both paths produce identical output.

HTML output is a single self-contained file with inline CSS. No external
assets, no JavaScript. Theming uses CSS custom properties only.

Layout (HTML):

1. Summary card: statement count, statements-with-violations at the gate
   severity, total estimated bytes, worst score.
2. Expectations and warnings strips when present.
3. Top fixes table (severity-ranked).
4. Per-statement cards, worst-first: score + grade pill, statement-type chip,
   violation chips, estimated-bytes bar, controls grouped by tier, and a
   collapsible raw-SQL panel.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# CSS
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
  --pill-warn-bg: #fffbeb; --pill-warn-fg: #92400e; --pill-warn-dot: #d97706;
  --pill-fail-bg: #fef2f2; --pill-fail-fg: #991b1b; --pill-fail-dot: #dc2626;
  --pill-na-bg: #f3f4f6; --pill-na-fg: #6b7280; --pill-na-dot: #9ca3af;
  --warn-bg: #fffbeb; --warn-border: #fde68a;
  --bar-track: #e5e7eb; --bar-fill: #2563eb;
  --badge-bg: #eff6ff; --badge-fg: #1e40af; --badge-border: #bfdbfe;
  --sev-critical-bg: #fef2f2; --sev-critical-fg: #991b1b;
  --sev-high-bg: #fff7ed; --sev-high-fg: #9a3412;
  --sev-medium-bg: #fffbeb; --sev-medium-fg: #92400e;
  --sev-low-bg: #f3f4f6; --sev-low-fg: #4b5563;
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
  --pill-warn-bg: #422006; --pill-warn-fg: #fde68a; --pill-warn-dot: #fbbf24;
  --pill-fail-bg: #7f1d1d; --pill-fail-fg: #fecaca; --pill-fail-dot: #f87171;
  --pill-na-bg: #1e293b; --pill-na-fg: #94a3b8; --pill-na-dot: #64748b;
  --warn-bg: #422006; --warn-border: #92400e;
  --bar-track: #334155; --bar-fill: #60a5fa;
  --badge-bg: #0c2849; --badge-fg: #93c5fd; --badge-border: #1e3a8a;
  --sev-critical-bg: #7f1d1d; --sev-critical-fg: #fecaca;
  --sev-high-bg: #431407; --sev-high-fg: #fed7aa;
  --sev-medium-bg: #422006; --sev-medium-fg: #fde68a;
  --sev-low-bg: #1e293b; --sev-low-fg: #cbd5e1;
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

.summary-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 20px 24px; margin-bottom: 20px;
}
.summary-metrics {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px;
}
.metric { display: flex; flex-direction: column; gap: 2px; }
.metric-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.metric-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.metric-value.bad { color: var(--grade-f-fg); }

.grade-pill {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 36px; height: 36px; padding: 0 12px;
  border-radius: 8px; font-weight: 700; font-size: 16px; border: 1px solid transparent;
}
.grade-A { background: var(--grade-a-bg); color: var(--grade-a-fg); }
.grade-B { background: var(--grade-b-bg); color: var(--grade-b-fg); }
.grade-C { background: var(--grade-c-bg); color: var(--grade-c-fg); }
.grade-D { background: var(--grade-d-bg); color: var(--grade-d-fg); }
.grade-F { background: var(--grade-f-bg); color: var(--grade-f-fg); }

.pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; white-space: nowrap;
}
.pill .dot { width: 6px; height: 6px; border-radius: 50%; }
.pill.pass { background: var(--pill-pass-bg); color: var(--pill-pass-fg); }
.pill.pass .dot { background: var(--pill-pass-dot); }
.pill.warn { background: var(--pill-warn-bg); color: var(--pill-warn-fg); }
.pill.warn .dot { background: var(--pill-warn-dot); }
.pill.fail { background: var(--pill-fail-bg); color: var(--pill-fail-fg); }
.pill.fail .dot { background: var(--pill-fail-dot); }
.pill.na { background: var(--pill-na-bg); color: var(--pill-na-fg); }
.pill.na .dot { background: var(--pill-na-dot); }

.chip { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; }
.chip.sev-critical { background: var(--sev-critical-bg); color: var(--sev-critical-fg); }
.chip.sev-high { background: var(--sev-high-bg); color: var(--sev-high-fg); }
.chip.sev-medium { background: var(--sev-medium-bg); color: var(--sev-medium-fg); }
.chip.sev-low { background: var(--sev-low-bg); color: var(--sev-low-fg); }
.chip.type { background: var(--badge-bg); color: var(--badge-fg); border: 1px solid var(--badge-border); font-family: ui-monospace, "SF Mono", Menlo, monospace; }

.tier-badge { display: inline-flex; align-items: center; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing: 0.04em; }
.tier-badge.t1 { background: var(--tier-1-bg); color: var(--tier-1-fg); }
.tier-badge.t2 { background: var(--tier-2-bg); color: var(--tier-2-fg); }
.tier-badge.t3 { background: var(--tier-3-bg); color: var(--tier-3-fg); }

.summary-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.summary-table th, .summary-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.summary-table tr:last-child td { border-bottom: none; }
.summary-table th { font-weight: 600; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
.summary-table td.num { font-variant-numeric: tabular-nums; text-align: right; width: 1%; white-space: nowrap; }
.summary-table td.id-cell a { color: inherit; text-decoration: none; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.summary-table td.id-cell a:hover { text-decoration: underline; color: var(--accent); }
.section-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 18px; margin-bottom: 16px; }
.section-card h2 { font-size: 14px; margin: 0 0 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }

.statement {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 20px; margin-bottom: 16px; scroll-margin-top: 16px;
}
.st-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.st-id { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 15px; }
.st-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.st-score { display: flex; align-items: center; gap: 12px; }
.st-num { font-size: 26px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.st-num .of { color: var(--muted); font-size: 13px; font-weight: 500; }
.st-info { font-size: 12px; color: var(--muted); margin: 10px 0 4px; display: flex; flex-wrap: wrap; gap: 14px; font-family: ui-monospace, "SF Mono", Menlo, monospace; }
.st-info strong { color: var(--fg); }
.violation-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }

.bytes-bar { margin: 10px 0; }
.bytes-bar .track { background: var(--bar-track); border-radius: 6px; height: 10px; overflow: hidden; }
.bytes-bar .fill { background: var(--bar-fill); height: 100%; }
.bytes-bar .label { font-size: 11px; color: var(--muted); margin-top: 4px; font-variant-numeric: tabular-nums; }

details.sql-panel { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0 12px; margin: 12px 0; }
details.sql-panel > summary { padding: 8px 0; cursor: pointer; font-size: 12px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
details.sql-panel[open] > summary { border-bottom: 1px solid var(--border); margin-bottom: 8px; }
.sql-body { padding: 4px 0 10px; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, "SF Mono", Menlo, monospace; }

.tier-group { margin: 8px 0; }
.tier-group h4 { font-size: 11px; margin: 8px 0 4px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; }
.ctrl-list { display: grid; gap: 6px; margin: 4px 0 8px; }
.ctrl-row { display: grid; grid-template-columns: max-content 1fr max-content; align-items: start; gap: 10px; padding: 6px 0; }
.ctrl-name { font-weight: 500; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.ctrl-name .cid { color: var(--muted); }
.ctrl-body { font-size: 12px; }
.ctrl-evidence { color: var(--muted); word-break: break-word; }
.ctrl-suggestion { display: block; margin-top: 3px; font-style: italic; color: var(--fg); }
.ctrl-suggestion::before { content: "Fix: "; font-weight: 600; font-style: normal; }

.issues { background: var(--warn-bg); border: 1px solid var(--warn-border); border-radius: 8px; padding: 10px 14px; margin-top: 12px; }
.issues h3 { margin-top: 0; color: inherit; }
.issues ul { margin: 0; padding-left: 18px; font-size: 13px; }
.exp-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }
.no-data { color: var(--muted); font-size: 12px; font-style: italic; padding: 4px 0; }
.footnote { font-size: 11px; color: var(--muted); margin-top: 24px; padding: 12px 14px; border-top: 1px dashed var(--border); }
.footnote strong { color: var(--fg); }
""".strip()

_METHODOLOGY_TEXT = (
    "This report answers whether a statement would be allowed through the control plane, not whether it is "
    "well written. The score is a weighted quality lens; the gate is driven by violation severity, because a "
    "single blocking violation should stop a statement regardless of how clean the rest of it is. The linter "
    "never executes the SQL it audits: byte estimates come from a dry-run only, and are na in static mode."
)


def _theme_css(theme: str) -> str:
    if theme == "light":
        tokens = _HTML_LIGHT_TOKENS
    elif theme == "dark":
        tokens = _HTML_DARK_TOKENS
    elif theme == "auto":
        tokens = _HTML_LIGHT_TOKENS + "\n@media (prefers-color-scheme: dark) {\n" + _HTML_DARK_TOKENS + "\n}"
    else:
        raise ValueError(f"Unknown theme {theme!r}; expected one of: auto, light, dark.")
    return tokens + "\n" + _HTML_BASE_CSS


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _grade_class(grade: str) -> str:
    return f"grade-{grade}" if grade in ("A", "B", "C", "D", "F") else "grade-F"


_STATUS_LABEL = {"pass": "pass", "warn": "warn", "fail": "fail", "na": "n/a"}


def _status_class(status: str) -> str:
    return status if status in ("pass", "warn", "fail", "na") else "na"


def _rubric_descriptor(report: dict[str, Any]) -> str:
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


def _scope_descriptor(report: dict[str, Any]) -> str:
    scope = report.get("scope") or {}
    source = scope.get("source", "sql")
    table = scope.get("table")
    window = scope.get("window")
    parts = [f"source {source}"]
    if table:
        parts.append(f"`{table}`")
    if window:
        parts.append(f"window {window}")
    return " · ".join(parts)


def _statement_slug(statement_id: str | None) -> str:
    text = (statement_id or "statement").lower()
    out = [ch if ch.isalnum() else "-" for ch in text]
    slug = "".join(out).strip("-")
    return slug or "statement"


def _sorted_statements(report: dict[str, Any]) -> list[dict[str, Any]]:
    statements = list(report.get("statements") or [])
    statements.sort(key=lambda s: (s.get("score", 100), -(s.get("estimated_bytes") or 0)))
    return statements


def _max_bytes(statements: list[dict[str, Any]]) -> int:
    return max((int(s.get("estimated_bytes") or 0) for s in statements), default=0)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _md_escape_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _md_status(status: str) -> str:
    return f"[{_STATUS_LABEL.get(status, 'n/a')}]"


def make_markdown(report: dict[str, Any], *, theme: str = "auto") -> str:
    """Render a guardrail report as a Markdown document. ``theme`` is ignored."""
    del theme

    lines: list[str] = []
    summary = report.get("summary") or {}
    mode = report.get("mode", "static")

    lines.append("# SQL Guardrail Report")
    lines.append("")
    lines.append(
        f"_Mode `{mode}` · {_rubric_descriptor(report)} · {_scope_descriptor(report)}_"
    )
    linted_at = report.get("linted_at", "")
    if linted_at:
        lines.append(f"_Linted: {linted_at}_")
    lines.append("")

    # Expectations.
    expectations = report.get("expectations") or []
    if expectations:
        lines.append("## Expectations")
        for exp in expectations:
            status = "[pass]" if exp.get("passed") else "[fail]"
            lines.append(
                f"- **{exp.get('name', '')}** {status} (limit {exp.get('limit', '')}, "
                f"actual {exp.get('actual', '')}): {exp.get('message', '')}"
            )
        lines.append("")

    warnings = report.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Summary.
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Statements:** {summary.get('statement_count', 0)}")
    lines.append(f"- **Worst score:** {summary.get('worst_score', 0)}/100")
    lines.append(
        f"- **With violations at {summary.get('gate_severity', 'high')}+:** "
        f"{summary.get('statements_with_violations', 0)}"
    )
    teb = summary.get("total_estimated_bytes_human")
    lines.append(f"- **Total estimated scan:** {teb if teb else 'n/a'}")
    lines.append("")

    # Top fixes.
    top_fixes = report.get("top_fixes") or []
    if top_fixes:
        lines.append("## Top fixes")
        lines.append("")
        lines.append("| Statement | Control | Severity | Impact | Fix |")
        lines.append("| --- | --- | --- | --- | --- |")
        for f in top_fixes:
            impact = (
                f"{f.get('estimated_bytes_saved')} bytes saved"
                if f.get("estimated_bytes_saved") else f.get("impact", "")
            )
            lines.append(
                f"| `{f.get('statement_id')}` | `{f.get('control')}` | {f.get('severity')} | "
                f"{_md_escape_cell(str(impact))} | {_md_escape_cell(f.get('fix', ''))} |"
            )
        lines.append("")

    # Per-statement.
    statements = _sorted_statements(report)
    lines.append(f"## Statements ({len(statements)})")
    lines.append("")
    for s in statements:
        lines.extend(_md_statement(s))

    lines.append("---")
    lines.append("")
    lines.append(f"_Methodology: {_METHODOLOGY_TEXT}_")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _md_statement(s: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    sid = s.get("statement_id", "?")
    score = s.get("score", 0)
    grade = s.get("grade", "?")
    stype = s.get("statement_type", "?")
    lines.append(f"### `{sid}` {score}/100 ({grade}) · {stype}")
    lines.append("")
    lines.append(f"`{_md_escape_cell(s.get('sql_preview', ''))}`")
    lines.append("")
    tables = s.get("referenced_tables") or []
    if tables:
        lines.append(f"**Tables:** {', '.join(f'`{t}`' for t in tables)}")
    eb = s.get("estimated_bytes_human")
    if eb:
        lines.append(f"**Estimated scan:** {eb}")
    viol = s.get("violations") or {}
    viol_str = ", ".join(f"{k} {v}" for k, v in viol.items() if v)
    lines.append(f"**Violations:** {viol_str if viol_str else 'none'}")
    lines.append("")

    controls = s.get("controls") or []
    for tier in (1, 2, 3):
        tier_ctrls = [c for c in controls if c.get("tier") == tier]
        if not tier_ctrls:
            continue
        lines.append(f"**Tier {tier}**")
        lines.append("")
        lines.append("| Control | Status | Severity | Evidence | Fix |")
        lines.append("| --- | --- | --- | --- | --- |")
        for c in tier_ctrls:
            lines.append(
                f"| `{c.get('id')}` {c.get('name')} | {_md_status(c.get('status', 'na'))} | "
                f"{c.get('severity', '')} | {_md_escape_cell(c.get('evidence', ''))} | "
                f"{_md_escape_cell(c.get('suggestion', ''))} |"
            )
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _ctrl_pill(status: str) -> str:
    cls = _status_class(status)
    return f'<span class="pill {cls}"><span class="dot"></span>{_STATUS_LABEL.get(cls, "n/a")}</span>'


def _tier_badge(tier: int) -> str:
    cls = f"t{tier}" if tier in (1, 2, 3) else "t3"
    return f'<span class="tier-badge {cls}">T{tier}</span>'


def _sev_chip(severity: str) -> str:
    sev = (severity or "low").lower()
    cls = sev if sev in ("critical", "high", "medium", "low") else "low"
    return f'<span class="chip sev-{cls}">{_e(sev)}</span>'


def _controls_block(controls: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    tier_label = {
        1: "Tier 1 (statement safety)",
        2: "Tier 2 (cost and scan)",
        3: "Tier 3 (governance and hygiene)",
    }
    for tier in (1, 2, 3):
        tier_ctrls = [c for c in controls if c.get("tier") == tier]
        if not tier_ctrls:
            continue
        rows: list[str] = []
        for c in tier_ctrls:
            suggestion = c.get("suggestion", "")
            sug_html = f'<span class="ctrl-suggestion">{_e(suggestion)}</span>' if suggestion else ""
            rows.append(
                '<div class="ctrl-row">'
                f'<span class="ctrl-name"><span class="cid">{_e(c.get("id"))}</span> {_e(c.get("name"))}</span>'
                f'<span class="ctrl-body"><span class="ctrl-evidence">{_e(c.get("evidence", ""))}</span>{sug_html}</span>'
                f'{_ctrl_pill(c.get("status", "na"))}'
                '</div>'
            )
        blocks.append(
            f'<div class="tier-group"><h4>{_e(tier_label[tier])}</h4>'
            f'<div class="ctrl-list">{"".join(rows)}</div></div>'
        )
    return "".join(blocks)


def _violation_chips(violations: dict[str, Any]) -> str:
    chips: list[str] = []
    for sev in ("critical", "high", "medium", "low"):
        count = int((violations or {}).get(sev, 0) or 0)
        if count:
            chips.append(f'<span class="chip sev-{sev}">{count} {sev}</span>')
    if not chips:
        chips.append('<span class="chip sev-low">no violations</span>')
    return f'<div class="violation-chips">{"".join(chips)}</div>'


def _bytes_bar(statement: dict[str, Any], max_bytes: int) -> str:
    eb = statement.get("estimated_bytes")
    if eb is None or max_bytes <= 0:
        return ""
    pct = max(1, round(100 * int(eb) / max_bytes)) if eb else 0
    human = statement.get("estimated_bytes_human") or ""
    return (
        '<div class="bytes-bar">'
        f'<div class="track"><div class="fill" style="width:{pct}%"></div></div>'
        f'<div class="label">estimated scan: {_e(human)}</div>'
        '</div>'
    )


def _sql_panel(sql_preview: str) -> str:
    body = _e(sql_preview or "")
    return (
        '<details class="sql-panel"><summary>SQL</summary>'
        f'<div class="sql-body">{body}</div></details>'
    )


def _statement_card(s: dict[str, Any], max_bytes: int) -> str:
    sid = s.get("statement_id", "?")
    score = s.get("score", 0)
    grade = s.get("grade", "?")
    stype = s.get("statement_type", "?")
    tables = s.get("referenced_tables") or []
    tables_html = ", ".join(f"<strong>{_e(t)}</strong>" for t in tables) if tables else "none"

    return (
        f'<div class="statement" id="{_statement_slug(sid)}">'
        '<div class="st-head">'
        f'<div class="st-id">{_e(sid)}</div>'
        '<div class="st-score">'
        f'<span class="chip type">{_e(stype)}</span>'
        f'<div class="st-num">{score}<span class="of"> / 100</span></div>'
        f'<span class="grade-pill {_grade_class(grade)}">{_e(grade)}</span>'
        '</div>'
        '</div>'
        f'{_violation_chips(s.get("violations") or {})}'
        f'<div class="st-info"><span>tables: {tables_html}</span></div>'
        f'{_bytes_bar(s, max_bytes)}'
        f'{_sql_panel(s.get("sql_preview", ""))}'
        f'{_controls_block(s.get("controls") or [])}'
        '</div>'
    )


def _summary_card(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    teb = summary.get("total_estimated_bytes_human") or "n/a"
    with_viol = summary.get("statements_with_violations", 0)
    gate = summary.get("gate_severity", "high")
    return (
        '<div class="summary-card"><div class="summary-metrics">'
        f'<div class="metric"><span class="metric-label">Statements</span>'
        f'<span class="metric-value">{summary.get("statement_count", 0)}</span></div>'
        f'<div class="metric"><span class="metric-label">Worst score</span>'
        f'<span class="metric-value">{summary.get("worst_score", 0)}</span></div>'
        f'<div class="metric"><span class="metric-label">Violations at {_e(gate)}+</span>'
        f'<span class="metric-value {"bad" if with_viol else ""}">{with_viol}</span></div>'
        f'<div class="metric"><span class="metric-label">Total estimated scan</span>'
        f'<span class="metric-value">{_e(teb)}</span></div>'
        '</div></div>'
    )


def _top_fixes_html(report: dict[str, Any]) -> str:
    fixes = report.get("top_fixes") or []
    if not fixes:
        return ""
    rows: list[str] = []
    for f in fixes:
        impact = (
            f"{f.get('estimated_bytes_saved'):,} bytes saved"
            if f.get("estimated_bytes_saved") else _e(f.get("impact", ""))
        )
        rows.append(
            '<tr>'
            f'<td class="id-cell"><a href="#{_statement_slug(f.get("statement_id"))}">{_e(f.get("statement_id"))}</a></td>'
            f'<td><code>{_e(f.get("control"))}</code></td>'
            f'<td>{_sev_chip(f.get("severity", "low"))}</td>'
            f'<td>{impact}</td>'
            f'<td>{_e(f.get("fix", ""))}</td>'
            '</tr>'
        )
    return (
        '<div class="section-card"><h2>Top fixes</h2>'
        '<table class="summary-table">'
        '<thead><tr><th>Statement</th><th>Control</th><th>Severity</th><th>Impact</th><th>Fix</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _expectations_html(expectations: list[dict[str, Any]]) -> str:
    if not expectations:
        return ""
    pills: list[str] = []
    for exp in expectations:
        passed = bool(exp.get("passed"))
        cls = "pass" if passed else "fail"
        status = "passed" if passed else "failed"
        label = f"{exp.get('name', '')} (limit {exp.get('limit', '')}, actual {exp.get('actual', '')})"
        pills.append(f'<span class="pill {cls}"><span class="dot"></span>{_e(label)} [{status}]</span>')
    return f'<div class="exp-list">{"".join(pills)}</div>'


def _warnings_html(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{_e(w)}</li>" for w in warnings)
    return f'<div class="issues"><h3>Warnings</h3><ul>{items}</ul></div>'


def make_html(report: dict[str, Any], *, theme: str = "auto") -> str:
    """Render a guardrail report as a self-contained HTML document."""
    css = _theme_css(theme)
    statements = _sorted_statements(report)
    max_bytes = _max_bytes(statements)
    cards = "".join(_statement_card(s, max_bytes) for s in statements)
    if not cards:
        cards = '<div class="statement"><p class="no-data">No statements linted.</p></div>'

    subtitle_parts = [
        f"mode {_e(report.get('mode', 'static'))}",
        _e(_rubric_descriptor(report)),
        _e(_scope_descriptor(report)),
    ]
    linted_at = report.get("linted_at", "")
    if linted_at:
        subtitle_parts.append(_e(linted_at))
    subtitle = " · ".join(subtitle_parts)

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>SQL Guardrail Report</title>'
        f'<style>{css}</style>'
        '</head><body><div class="wrap">'
        '<h1>SQL Guardrail Report</h1>'
        f'<p class="subtitle">{subtitle}</p>'
        f'{_expectations_html(report.get("expectations") or [])}'
        f'{_warnings_html(report.get("warnings") or [])}'
        f'{_summary_card(report)}'
        f'{_top_fixes_html(report)}'
        f'{cards}'
        f'<div class="footnote"><strong>Methodology.</strong> {_e(_METHODOLOGY_TEXT)}</div>'
        '</div></body></html>\n'
    )


# ---------------------------------------------------------------------------
# CLI (gate replay)
# ---------------------------------------------------------------------------

def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get((severity or "").lower(), 0)


def _max_violation_rank(statement: dict[str, Any]) -> int:
    rank = 0
    for sev, count in (statement.get("violations") or {}).items():
        if count:
            rank = max(rank, _severity_rank(sev))
    return rank


def _parse_human_bytes(value: str) -> int:
    """Minimal byte parser for the renderer CLI (mirror of _validation.parse_bytes)."""
    import re
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTP]?I?B?)\s*$", text, re.IGNORECASE)
    if not m:
        raise ValueError(f"Invalid byte value {value!r}.")
    units = {
        "": 1, "B": 1, "KB": 1000, "KIB": 1024, "K": 1024, "MB": 1000 ** 2, "MIB": 1024 ** 2, "M": 1024 ** 2,
        "GB": 1000 ** 3, "GIB": 1024 ** 3, "G": 1024 ** 3, "TB": 1000 ** 4, "TIB": 1024 ** 4, "T": 1024 ** 4,
        "PB": 1000 ** 5, "PIB": 1024 ** 5, "P": 1024 ** 5,
    }
    return int(float(m.group(1)) * units[m.group(2).upper()])


def _evaluate_gates(report: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Replay the gates against a loaded report, replacing same-named entries."""
    keep = [
        e for e in (report.get("expectations") or [])
        if e.get("name") not in ("expect_min_score", "expect_no_violations_at", "expect_max_bytes")
    ]
    statements = report.get("statements") or []

    if args.expect_min_score is not None:
        offenders = [s.get("statement_id") for s in statements if int(s.get("score", 0)) < args.expect_min_score]
        worst = min((int(s.get("score", 0)) for s in statements), default=100)
        keep.append({
            "name": "expect_min_score", "limit": args.expect_min_score, "actual": worst,
            "passed": not offenders, "offenders": offenders,
            "message": (f"All statements meet or exceed {args.expect_min_score}." if not offenders
                        else f"{len(offenders)} statement(s) below {args.expect_min_score}."),
        })
    if args.expect_no_violations_at is not None:
        threshold = _severity_rank(args.expect_no_violations_at)
        offenders = [s.get("statement_id") for s in statements if _max_violation_rank(s) >= threshold]
        keep.append({
            "name": "expect_no_violations_at", "limit": args.expect_no_violations_at, "actual": len(offenders),
            "passed": not offenders, "offenders": offenders,
            "message": (f"No statement violates at or above {args.expect_no_violations_at}." if not offenders
                        else f"{len(offenders)} statement(s) violate at or above {args.expect_no_violations_at}."),
        })
    if args.expect_max_bytes is not None:
        limit = _parse_human_bytes(args.expect_max_bytes)
        offenders = [
            s.get("statement_id") for s in statements
            if s.get("estimated_bytes") is not None and int(s["estimated_bytes"]) > limit
        ]
        worst = max((int(s["estimated_bytes"]) for s in statements if s.get("estimated_bytes") is not None), default=0)
        keep.append({
            "name": "expect_max_bytes", "limit": limit, "actual": worst,
            "passed": not offenders, "offenders": offenders,
            "message": (f"All statements scan within {limit} bytes." if not offenders
                        else f"{len(offenders)} statement(s) exceed {limit} bytes."),
        })
    return keep


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="render_report.py", description="Render a SQL guardrail report from a JSON report.")
    p.add_argument("--input", required=True, help="Path to a JSON report (use '-' for stdin)")
    p.add_argument("--output-md", default="report.md", help="Markdown output path")
    p.add_argument("--output-html", default=None, help="Optional HTML output path")
    p.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="HTML theme")
    p.add_argument("--expect-min-score", type=int, default=None)
    p.add_argument("--expect-no-violations-at", choices=("critical", "high", "medium", "low"), default=None)
    p.add_argument("--expect-max-bytes", default=None)
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

    if any(getattr(args, name) is not None for name in ("expect_min_score", "expect_no_violations_at", "expect_max_bytes")):
        report["expectations"] = _evaluate_gates(report, args)

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
