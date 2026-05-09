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
.issues li { margin: 6px 0; }
.issue-suggestion { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; font-style: italic; }
.issue-suggestion::before { content: "Suggested: "; font-weight: 600; font-style: normal; }
.exp-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }
.no-issues { color: var(--muted); font-size: 12px; font-style: italic; padding: 4px 0; }
details.desc-panel { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0 12px; margin: 12px 0; }
details.desc-panel > summary { padding: 8px 0; cursor: pointer; font-size: 12px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
details.desc-panel[open] > summary { border-bottom: 1px solid var(--border); margin-bottom: 8px; }
.desc-body { padding: 4px 0 10px; font-size: 13px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.desc-body.empty { color: var(--muted); font-style: italic; }
.label-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.label-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; background: var(--card); border: 1px solid var(--border); font-size: 11px; font-family: ui-monospace, "SF Mono", Menlo, monospace; color: var(--muted); }
.label-chip strong { color: var(--fg); font-weight: 600; }
.col-desc { padding: 6px 0 4px; font-size: 12px; color: var(--fg); line-height: 1.5; white-space: pre-wrap; word-break: break-word; border-left: 3px solid var(--border); padding-left: 10px; margin: 4px 0 8px; background: var(--bg); }
.col-desc.empty { color: var(--muted); font-style: italic; }
.summary {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 20px;
}
.summary h2 { font-size: 14px; margin: 0 0 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.summary table { width: 100%; border-collapse: collapse; font-size: 13px; }
.summary th, .summary td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.summary tr:last-child td { border-bottom: none; }
.summary th { font-weight: 600; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
.summary td.score { font-variant-numeric: tabular-nums; font-weight: 600; }
.summary td.grade-cell { width: 1%; }
.summary td.grade-cell .grade-pill { min-width: 28px; height: 24px; font-size: 12px; padding: 0 8px; }
.summary td.id-cell a { color: inherit; text-decoration: none; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; word-break: break-all; }
.summary td.id-cell a:hover { text-decoration: underline; color: var(--accent); }
.summary td.issue-cell { color: var(--muted); font-size: 12px; }
.scorecard { scroll-margin-top: 16px; }
""".strip()


def _theme_css(theme: str) -> str:
    """Assemble the CSS for the requested theme.

    ``light`` and ``dark`` bake in a single palette; ``auto`` ships both
    palettes and switches via the ``prefers-color-scheme`` media query
    so the dashboard follows the viewer's OS preference.
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
# Helpers
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    """HTML-escape a value for safe interpolation into the dashboard."""
    return html.escape("" if value is None else str(value), quote=True)


def _criterion_status(c: dict[str, Any]) -> str:
    """Map a criterion dict to its UI status: ``pass`` / ``partial`` / ``fail`` / ``na``.

    ``na`` is the special case where ``max`` is 0 — used for the bonus
    ``caveats_present`` criterion when a column has nothing to caveat.
    """
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
    """One-line description of what was scored, for the page subtitle."""
    scope = report.get("scope") or {}
    if scope.get("dataset"):
        return f"dataset `{scope['dataset']}`"
    tables = scope.get("tables") or []
    if not tables:
        return "no tables"
    if len(tables) == 1:
        return f"table `{tables[0]}`"
    return f"{len(tables)} tables"


def _rubric_descriptor(report: dict[str, Any]) -> str:
    """Short human-readable string identifying the rubric used.

    Produces e.g. ``data-steward-default v1.0 (builtin)`` for the default
    rubric, or ``custom-name v2.0 (custom.json @ a1b2c3d4)`` when a config
    file was supplied. Falls back to ``rubric v<rubric_version>`` for old
    reports that pre-date the ``rubric_config`` block.
    """
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


def _sorted_tables(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Tables sorted by score ascending — most actionable first.

    Both the summary table and the per-table cards use this ordering so
    the worst metadata appears at the top of the page and at the top of
    the summary, matching where a steward would want to focus first.
    """
    tables = list(report.get("tables") or [])
    tables.sort(key=lambda t: (t.get("score") or 0, t.get("table_id") or ""))
    return tables


def _slug(table_id: str | None) -> str:
    """HTML anchor id derived from a fully qualified table id.

    Replaces dots/dashes/underscores with hyphens and strips other chars
    so ``project.dataset.table`` becomes ``t-project-dataset-table`` —
    stable, deterministic, and safe in URL fragments.
    """
    text = (table_id or "table").lower()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif ch in (".", "-", "_"):
            out.append("-")
    slug = "".join(out).strip("-")
    return f"t-{slug}" if slug else "t-unknown"


def _issue_parts(issue: Any) -> tuple[str, str]:
    """Return (message, suggestion) for either string- or dict-shaped issues.

    The rubric has emitted dict-shaped issues since the suggested-fix feature
    landed. Older Path A reports (or hand-built fixtures) may still ship plain
    strings; we accept both so old reports keep rendering.
    """
    if isinstance(issue, dict):
        return str(issue.get("message", "")), str(issue.get("suggestion", ""))
    return str(issue), ""


def _order_columns_for_display(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order columns for display: roots worst-first, descendants in schema order.

    For tables with nested ``RECORD`` columns, sorting strictly by ratio
    scatters siblings (e.g. ``address.street`` and ``address.city`` could end
    up at opposite ends of the list). This walker keeps each root column's
    descendants contiguous under it: roots are sorted worst-first by ratio,
    and within each subtree descendants stay in their original (schema)
    order. Tables with no nested fields render exactly as before — the
    flat list ends up sorted worst-first.
    """
    by_name = {c.get("name"): c for c in columns if c.get("name")}
    children_of: dict[str, list[dict[str, Any]]] = {n: [] for n in by_name}
    roots: list[dict[str, Any]] = []
    for col in columns:
        parent = col.get("parent")
        if parent and parent in by_name:
            children_of[parent].append(col)
        else:
            roots.append(col)

    def ratio(c: dict[str, Any]) -> float:
        return (c.get("points") or 0) / (c.get("max") or 1)
    roots.sort(key=ratio)

    out: list[dict[str, Any]] = []

    def emit(col: dict[str, Any]) -> None:
        out.append(col)
        for child in children_of.get(col.get("name"), ()):
            emit(child)

    for root in roots:
        emit(root)
    return out


def _column_depth(col: dict[str, Any]) -> int:
    """Nesting depth of a column for visual indentation. 0 for top-level."""
    name = col.get("name") or ""
    return name.count(".")


def _top_issue(table: dict[str, Any]) -> str:
    """First (highest-priority) issue's message, or empty.

    Used by the summary table at the top of the scorecard. The suggestion
    is intentionally dropped — there's no room for it in a one-line cell;
    the per-table card below shows the full message + suggestion.
    """
    issues = table.get("issues") or []
    if not issues:
        return ""
    msg, _ = _issue_parts(issues[0])
    return msg


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _md_status(c: dict[str, Any]) -> str:
    """Markdown-friendly status label for a criterion."""
    return {"pass": "[pass]", "partial": "[partial]", "fail": "[fail]", "na": "[n/a]"}[_criterion_status(c)]


def make_markdown(report: dict[str, Any]) -> str:
    """Render a scorecard report as a Markdown document.

    Layout:

    1. H1 title + scope subtitle.
    2. Expectations / warnings sections (when present).
    3. Summary table — every scored table worst-first with score, grade,
       and top issue.
    4. Per-table sections (worst-first), each with: full description as a
       blockquote, labels, table-criteria table, column-criteria table
       (including each column's full description), and an issues list.
    """
    lines: list[str] = []
    lines.append("# Metadata Scorecard")
    lines.append("")
    scope = _scope_summary(report)
    scored_at = report.get("scored_at")
    rubric_desc = _rubric_descriptor(report)
    lines.append(f"_Scope: {scope} — {rubric_desc}_")
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

    sorted_tables = _sorted_tables(report)
    if sorted_tables:
        lines.append(f"## Summary — {len(sorted_tables)} table{'s' if len(sorted_tables) != 1 else ''}")
        lines.append("")
        lines.append("| Table | Score | Grade | Top issue |")
        lines.append("| --- | --- | --- | --- |")
        for t in sorted_tables:
            tid = t.get("table_id") or "(unknown)"
            score = t.get("score", 0)
            grade = t.get("grade", "?")
            top = _top_issue(t).replace("|", "\\|").replace("\n", " ") or "—"
            lines.append(f"| `{tid}` | {score} | {grade} | {top} |")
        lines.append("")

    for table in sorted_tables:
        tid = table.get("table_id", "(unknown)")
        score = table.get("score", 0)
        grade = table.get("grade", "?")
        lines.append(f"## `{tid}` — {score}/100 ({grade})")
        lines.append("")

        tm = table.get("table_metadata") or {}
        table_desc = (tm.get("description") or "").strip()
        if table_desc:
            lines.append("**Description:**")
            lines.append("")
            for desc_line in table_desc.split("\n"):
                lines.append(f"> {desc_line}")
            lines.append("")
        else:
            lines.append("**Description:** _(none)_")
            lines.append("")
        labels = tm.get("labels") or {}
        if labels:
            label_str = ", ".join(f"`{k}={v}`" for k, v in sorted(labels.items()))
            lines.append(f"**Labels:** {label_str}")
            lines.append("")

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
            lines.append("| Column | Type | Score | Description | Failing criteria |")
            lines.append("| --- | --- | --- | --- | --- |")
            cols_sorted = _order_columns_for_display(cm["columns"])
            for col in cols_sorted:
                fails = [c["name"] for c in (col.get("criteria") or [])
                         if _criterion_status(c) in ("fail", "partial")]
                fails_str = ", ".join(f"`{n}`" for n in fails) if fails else "—"
                pts = col.get("points", 0)
                mx = col.get("max", 0)
                col_desc = (col.get("description") or "").strip().replace("|", "\\|").replace("\n", " ")
                if not col_desc:
                    col_desc = "_(none)_"
                ctype = col.get("type") or ""
                lines.append(f"| `{col.get('name')}` | {ctype} | {pts}/{mx} | {col_desc} | {fails_str} |")
            lines.append("")

        issues = table.get("issues") or []
        if issues:
            lines.append("### Issues")
            for issue in issues:
                msg, suggestion = _issue_parts(issue)
                lines.append(f"- {msg}")
                if suggestion:
                    lines.append(f"  - _Suggested:_ {suggestion}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _criterion_pill(c: dict[str, Any]) -> str:
    """Colored status pill (e.g. ``pass 2/2`` or ``fail 0/2``) for a criterion."""
    status = _criterion_status(c)
    label = {"pass": "pass", "partial": "partial", "fail": "fail", "na": "n/a"}[status]
    return f'<span class="pill {status}"><span class="dot"></span>{label} {c.get("points", 0)}/{c.get("max", 0)}</span>'


def _description_panel(description: str | None, labels: dict[str, str] | None = None,
                       *, default_open: bool = True) -> str:
    """Collapsible 'Description' panel for a table card. Open by default."""
    desc = (description or "").strip()
    body_cls = "desc-body" if desc else "desc-body empty"
    body_text = _e(desc) if desc else "(no description)"
    chips_html = ""
    if labels:
        chips = "".join(
            f'<span class="label-chip"><strong>{_e(k)}</strong>={_e(v)}</span>'
            for k, v in sorted(labels.items())
        )
        chips_html = f'<div class="label-chips">{chips}</div>'
    open_attr = " open" if default_open else ""
    return (
        f'<details class="desc-panel"{open_attr}>'
        f'<summary>Description &amp; labels</summary>'
        f'<div class="{body_cls}">{body_text}</div>'
        f'{chips_html}'
        f'</details>'
    )


def _column_description_block(description: str | None) -> str:
    """Inline description shown at the top of an expanded column panel."""
    desc = (description or "").strip()
    if desc:
        return f'<div class="col-desc">{_e(desc)}</div>'
    return '<div class="col-desc empty">(no description)</div>'


def _criteria_block(criteria: list[dict[str, Any]]) -> str:
    """Render a list of criterion dicts as a vertical row stack of pills."""
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
    """Render a single column as a collapsible ``<details>`` panel.

    Closed by default; the summary line shows the column name, type,
    score, and a count of failing/partial criteria. Expanding reveals
    the full description and the per-criterion pills.

    Nested columns are visually indented based on their depth (number of
    dots in the dotted name) so a steward scanning the scorecard sees the
    parent-child relationship at a glance.
    """
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
    desc_block = _column_description_block(col.get("description"))
    depth = _column_depth(col)
    style_attr = f' style="margin-left: {depth * 18}px"' if depth > 0 else ""
    return f'<details class="col-detail"{style_attr}>{summary}{desc_block}{inner}</details>'


def _table_scorecard(table: dict[str, Any]) -> str:
    """Render one table as a full scorecard card.

    Card layout: header (id + score + grade pill), description-and-labels
    panel (open by default), table-criteria pills, column-metadata roll-up
    bar, per-column collapsibles (worst first), and an issues callout.
    The card has an ``id`` attribute matching ``_slug(table_id)`` so the
    summary table at the top of the page can anchor-link into it.
    """
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
        items_list: list[str] = []
        for issue in issues:
            msg, suggestion = _issue_parts(issue)
            sug_html = f'<span class="issue-suggestion">{_e(suggestion)}</span>' if suggestion else ""
            items_list.append(f"<li>{_e(msg)}{sug_html}</li>")
        items = "".join(items_list)
        issues_html = f'<div class="issues"><h3>Issues</h3><ul>{items}</ul></div>'

    columns_html = ""
    if cm.get("columns"):
        # Roots worst-first; descendants kept under their root in schema order.
        cols_sorted = _order_columns_for_display(cm["columns"])
        columns_html = "".join(_column_card(c) for c in cols_sorted)

    desc_panel = _description_panel(tm.get("description"), tm.get("labels"), default_open=True)
    anchor_id = _slug(table.get("table_id"))

    return (
        f'<div class="scorecard" id="{anchor_id}">'
        f'<div class="sc-head">'
        f'<div><h2 class="sc-id"><code>{tid}</code></h2></div>'
        f'<div class="sc-score">'
        f'<div class="sc-num">{score}<span class="of"> / 100</span></div>'
        f'<span class="grade-pill {grade_cls}">{_e(grade)}</span>'
        f'</div>'
        f'</div>'
        f'{desc_panel}'
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


def _summary_html(tables: list[dict[str, Any]]) -> str:
    """Render the at-the-top summary table for an HTML scorecard.

    One row per table: clickable id (anchors to the per-table card below),
    score, grade pill, top issue. Tables are passed in display order;
    the caller is responsible for sorting (worst-first).
    """
    if not tables:
        return ""
    rows: list[str] = []
    for t in tables:
        tid = t.get("table_id") or "(unknown)"
        score = t.get("score", 0)
        grade = t.get("grade", "?")
        grade_cls = f"grade-{grade}" if grade in ("A", "B", "C", "D", "F") else "grade-F"
        anchor = _slug(tid)
        top = _top_issue(t)
        top_cell = _e(top) if top else "&mdash;"
        rows.append(
            f'<tr>'
            f'<td class="id-cell"><a href="#{anchor}">{_e(tid)}</a></td>'
            f'<td class="score">{score}</td>'
            f'<td class="grade-cell"><span class="grade-pill {grade_cls}">{_e(grade)}</span></td>'
            f'<td class="issue-cell">{top_cell}</td>'
            f'</tr>'
        )
    return (
        '<div class="summary">'
        f'<h2>Summary &middot; {len(tables)} table{"s" if len(tables) != 1 else ""}</h2>'
        '<table>'
        '<thead><tr><th>Table</th><th>Score</th><th>Grade</th><th>Top issue</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</div>'
    )


def _expectations_html(expectations: list[dict[str, Any]]) -> str:
    """Render a top-of-page strip of expectation status pills."""
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
    """Render top-of-page warnings (e.g. 'dataset has no tables') in a callout."""
    if not warnings:
        return ""
    items = "".join(f"<li>{_e(w)}</li>" for w in warnings)
    return f'<div class="issues"><h3>Warnings</h3><ul>{items}</ul></div>'


def make_html(report: dict[str, Any], *, theme: str = "auto") -> str:
    """Render a scorecard report as a single self-contained HTML document.

    Layout: H1 + scope subtitle, expectations / warnings strips, a summary
    table linking to each per-table card, then one card per table
    (worst-first). All CSS is inlined; no external assets, no JavaScript.
    """
    css = _theme_css(theme)
    scope = _scope_summary(report)
    rubric_desc = _rubric_descriptor(report)
    scored_at = report.get("scored_at")
    scored_str = f" · {format_datetime(scored_at)}" if scored_at else ""

    sorted_tables = _sorted_tables(report)
    summary_html = _summary_html(sorted_tables)
    cards = "".join(_table_scorecard(t) for t in sorted_tables)
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
        f'<p class="subtitle">{_e(scope)} · {_e(rubric_desc)}{_e(scored_str)}</p>'
        f'{expectations_html}'
        f'{warnings_html}'
        f'{summary_html}'
        f'{cards}'
        '</div></body></html>\n'
    )
