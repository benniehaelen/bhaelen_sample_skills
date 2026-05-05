"""Markdown and HTML rendering for metadata scorecards.

The HTML output is a single self-contained file with inline CSS. No
external assets, no JavaScript. Theming uses CSS custom properties only;
see ``_HTML_LIGHT_TOKENS`` / ``_HTML_DARK_TOKENS``.

This module is the single source of truth for both the bundled CLI script
(``score_table_metadata.py``) and the connector-driven path
(``render_scorecard.py``). Drift cannot occur because both call the same
functions defined here.
"""

from __future__ import annotations

import html
from typing import Any

from _serialize import format_datetime

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
}
""".strip()

_HTML_BASE_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 0; }
h3 { font-size: 13px; margin: 14px 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.subtitle { color: var(--muted); margin: 0 0 24px; font-size: 13px; }
.scope-line { color: var(--muted); font-size: 12px; margin: 4px 0 0; }
.scope-line code { background: var(--card); padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.scorecard {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 20px; margin-bottom: 16px;
}
.sc-head {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--border);
}
.sc-id { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 14px; word-break: break-all; }
.sc-score { display: flex; align-items: center; gap: 12px; }
.sc-num {
  font-size: 28px; font-weight: 700; line-height: 1;
  font-variant-numeric: tabular-nums;
}
.sc-num .of { color: var(--muted); font-size: 14px; font-weight: 500; }
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
.crit-list { display: grid; gap: 6px; margin: 8px 0 12px; }
.crit-row {
  display: grid; grid-template-columns: max-content 1fr max-content;
  align-items: center; gap: 10px; padding: 6px 0;
}
.crit-name { font-weight: 500; }
.crit-evidence { color: var(--muted); font-size: 12px; word-break: break-word; }
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
.col-meta-summary { display: flex; gap: 18px; margin: 4px 0 8px; color: var(--muted); font-size: 12px; }
.col-meta-summary strong { color: var(--fg); }
.bar { height: 6px; background: var(--bar-track); border-radius: 3px; overflow: hidden; flex: 1; max-width: 280px; }
.bar > span { display: block; height: 100%; background: var(--bar-fill); border-radius: 3px; }
details.col-detail { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 0 12px; margin-top: 6px; }
details.col-detail summary { padding: 8px 0; cursor: pointer; font-size: 12px; }
details.col-detail summary code { background: rgba(0,0,0,0.04); padding: 1px 6px; border-radius: 3px; font-size: 11px; }
details.col-detail .crit-list { margin: 4px 0 8px; }
.issues { background: var(--warn-bg); border: 1px solid var(--warn-border); border-radius: 8px; padding: 10px 14px; margin-top: 12px; }
.issues h3 { margin-top: 0; color: inherit; }
.issues ul { margin: 0; padding-left: 18px; font-size: 13px; }
.issues li { margin: 3px 0; }
.exp-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }
.no-issues { color: var(--muted); font-size: 12px; font-style: italic; padding: 4px 0; }
""".strip()


def _theme_css(theme: str) -> str:
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
# Helpers
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _criterion_status(c: dict[str, Any]) -> str:
    """One of 'pass', 'partial', 'fail', 'na'."""
    if c.get("max", 0) == 0:
        return "na"
    pts = c.get("points", 0)
    mx = c.get("max", 0)
    if pts >= mx:
        return "pass"
    if pts > 0:
        return "partial"
    return "fail"


def _scope_summary(report: dict[str, Any]) -> str:
    scope = report.get("scope") or {}
    if scope.get("dataset"):
        return f"dataset `{scope['dataset']}`"
    tables = scope.get("tables") or []
    if not tables:
        return "no tables"
    if len(tables) == 1:
        return f"table `{tables[0]}`"
    return f"{len(tables)} tables"


def _sorted_tables(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Tables sorted by score ascending — most actionable first."""
    tables = list(report.get("tables") or [])
    tables.sort(key=lambda t: (t.get("score") or 0, t.get("table_id") or ""))
    return tables


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _md_status(c: dict[str, Any]) -> str:
    return {"pass": "[pass]", "partial": "[partial]", "fail": "[fail]", "na": "[n/a]"}[_criterion_status(c)]


def make_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Metadata Scorecard")
    lines.append("")
    scope = _scope_summary(report)
    scored_at = report.get("scored_at")
    rubric = report.get("rubric_version", "1.0")
    lines.append(f"_Scope: {scope} — rubric v{rubric}_")
    if scored_at:
        lines.append(f"_Scored: {format_datetime(scored_at)}_")
    lines.append("")

    expectations = report.get("expectations") or []
    if expectations:
        lines.append("## Expectations")
        for exp in expectations:
            status = exp.get("status", "")
            name = exp.get("name", "")
            detail = ""
            if exp.get("threshold") is not None:
                detail = f" (threshold {exp['threshold']})"
            lines.append(f"- **{name}** [{status}]{detail}")
            if exp.get("failing_tables"):
                for t in exp["failing_tables"]:
                    lines.append(f"  - `{t['table_id']}` scored {t['score']}")
        lines.append("")

    warnings = report.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    for table in _sorted_tables(report):
        tid = table.get("table_id", "(unknown)")
        score = table.get("score", 0)
        grade = table.get("grade", "?")
        lines.append(f"## `{tid}` — {score}/100 ({grade})")
        lines.append("")

        tm = table.get("table_metadata") or {}
        lines.append(f"### Table metadata: {tm.get('points', 0)}/{tm.get('max', 16)}")
        lines.append("")
        lines.append("| Criterion | Status | Evidence |")
        lines.append("| --- | --- | --- |")
        for c in tm.get("criteria") or []:
            evidence = c.get("evidence") or ""
            evidence = evidence.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{c.get('name')}` | {_md_status(c)} | {evidence} |")
        lines.append("")

        cm = table.get("column_metadata") or {}
        mean = cm.get("mean_normalized", 0.0)
        ccount = cm.get("column_count", 0)
        lines.append(f"### Column metadata: {ccount} columns, mean ratio {mean:.2f}")
        lines.append("")
        if cm.get("columns"):
            lines.append("| Column | Score | Failing criteria |")
            lines.append("| --- | --- | --- |")
            for col in cm["columns"]:
                fails = [c["name"] for c in (col.get("criteria") or [])
                         if _criterion_status(c) in ("fail", "partial")]
                fails_str = ", ".join(f"`{n}`" for n in fails) if fails else "—"
                pts = col.get("points", 0)
                mx = col.get("max", 0)
                lines.append(f"| `{col.get('name')}` | {pts}/{mx} | {fails_str} |")
            lines.append("")

        issues = table.get("issues") or []
        if issues:
            lines.append("### Issues")
            for issue in issues:
                lines.append(f"- {issue}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _criterion_pill(c: dict[str, Any]) -> str:
    status = _criterion_status(c)
    label = {"pass": "pass", "partial": "partial", "fail": "fail", "na": "n/a"}[status]
    return f'<span class="pill {status}"><span class="dot"></span>{label} {c.get("points", 0)}/{c.get("max", 0)}</span>'


def _criteria_block(criteria: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for c in criteria:
        ev = _e(c.get("evidence", ""))
        rows.append(
            f'<div class="crit-row">'
            f'<span class="crit-name"><code>{_e(c.get("name"))}</code></span>'
            f'<span class="crit-evidence">{ev}</span>'
            f'{_criterion_pill(c)}'
            f'</div>'
        )
    return f'<div class="crit-list">{"".join(rows)}</div>'


def _column_card(col: dict[str, Any]) -> str:
    pts = col.get("points", 0)
    mx = col.get("max", 0)
    pct = int(round(100 * pts / mx)) if mx else 100
    name = _e(col.get("name"))
    ctype = _e(col.get("type") or "")
    fails = [c for c in (col.get("criteria") or [])
             if _criterion_status(c) in ("fail", "partial")]
    inner = _criteria_block(col.get("criteria") or [])
    summary = (
        f'<summary>'
        f'<code>{name}</code> <span style="color:var(--muted)">{ctype}</span> '
        f'— <strong>{pts}/{mx}</strong> ({pct}%)'
        f'{f" · {len(fails)} issue(s)" if fails else ""}'
        f'</summary>'
    )
    return f'<details class="col-detail">{summary}{inner}</details>'


def _table_scorecard(table: dict[str, Any]) -> str:
    tid = _e(table.get("table_id"))
    score = table.get("score", 0)
    grade = table.get("grade", "?")
    grade_cls = f"grade-{grade}" if grade in ("A", "B", "C", "D", "F") else "grade-F"

    tm = table.get("table_metadata") or {}
    cm = table.get("column_metadata") or {}
    mean = cm.get("mean_normalized", 0.0)
    ccount = cm.get("column_count", 0)
    bar_pct = int(round(mean * 100))

    issues = table.get("issues") or []
    issues_html = ""
    if issues:
        items = "".join(f"<li>{_e(i)}</li>" for i in issues)
        issues_html = f'<div class="issues"><h3>Issues</h3><ul>{items}</ul></div>'

    columns_html = ""
    if cm.get("columns"):
        # Show worst columns first (lowest ratio)
        cols_sorted = sorted(
            cm["columns"],
            key=lambda c: ((c.get("points") or 0) / (c.get("max") or 1)),
        )
        columns_html = "".join(_column_card(c) for c in cols_sorted)

    return (
        f'<div class="scorecard">'
        f'<div class="sc-head">'
        f'<div><h2 class="sc-id"><code>{tid}</code></h2></div>'
        f'<div class="sc-score">'
        f'<div class="sc-num">{score}<span class="of"> / 100</span></div>'
        f'<span class="grade-pill {grade_cls}">{_e(grade)}</span>'
        f'</div>'
        f'</div>'
        f'<h3>Table metadata · {tm.get("points", 0)}/{tm.get("max", 16)}</h3>'
        f'{_criteria_block(tm.get("criteria") or [])}'
        f'<h3>Column metadata · {ccount} columns</h3>'
        f'<div class="col-meta-summary">'
        f'<span><strong>{ccount}</strong> columns</span>'
        f'<span>mean ratio <strong>{mean:.2f}</strong></span>'
        f'<div class="bar"><span style="width:{bar_pct}%"></span></div>'
        f'</div>'
        f'{columns_html}'
        f'{issues_html}'
        f'</div>'
    )


def _expectations_html(expectations: list[dict[str, Any]]) -> str:
    if not expectations:
        return ""
    pills: list[str] = []
    for exp in expectations:
        status = exp.get("status", "")
        name = exp.get("name", "")
        cls = "pass" if status == "passed" else ("fail" if status == "failed" else "na")
        threshold = exp.get("threshold")
        label = name if threshold is None else f"{name} ≥ {threshold}"
        pills.append(f'<span class="pill {cls}"><span class="dot"></span>{_e(label)} [{_e(status)}]</span>')
    return f'<div class="exp-list">{"".join(pills)}</div>'


def _warnings_html(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{_e(w)}</li>" for w in warnings)
    return f'<div class="issues"><h3>Warnings</h3><ul>{items}</ul></div>'


def make_html(report: dict[str, Any], *, theme: str = "auto") -> str:
    css = _theme_css(theme)
    scope = _scope_summary(report)
    rubric = report.get("rubric_version", "1.0")
    scored_at = report.get("scored_at")
    scored_str = f" · {format_datetime(scored_at)}" if scored_at else ""

    cards = "".join(_table_scorecard(t) for t in _sorted_tables(report))
    expectations_html = _expectations_html(report.get("expectations") or [])
    warnings_html = _warnings_html(report.get("warnings") or [])

    if not cards:
        cards = '<div class="scorecard"><p class="no-issues">No tables scored.</p></div>'

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Metadata Scorecard</title>'
        f'<style>{css}</style>'
        '</head><body><div class="wrap">'
        '<h1>Metadata Scorecard</h1>'
        f'<p class="subtitle">{_e(scope)} · rubric v{_e(rubric)}{_e(scored_str)}</p>'
        f'{expectations_html}'
        f'{warnings_html}'
        f'{cards}'
        '</div></body></html>\n'
    )
