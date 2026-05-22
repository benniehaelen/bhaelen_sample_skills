"""HTML dashboard renderer.

Renders a Scorecard to a single self-contained HTML file: inline CSS, no
external assets, no CDN, and no JavaScript (the per-query drill-down uses native
details and summary elements, so it works offline in any browser). Theming uses
CSS custom properties and prefers-color-scheme.

Layout: header with the overall score, grade, and run metadata; a warnings
callout (synthetic ground truth is surfaced here); a per-tier summary with
bars; a top-fixes table; per-tier criterion breakdowns with pass and severity
pills; and, for retrieval runs, an expandable card per failing query showing the
expected ids, the retrieved ids with scores, and the classified failure modes.

Visual language follows the sibling skills' dashboards: the same grade-pill and
status-pill palette and the same light, dark, and auto theming.
"""

from __future__ import annotations

import html
from typing import Any

from scoring import CheckResult, Scorecard

_TIER_ORDER = ("tier_1_config", "tier_2_content", "tier_3_retrieval")
_TIER_LABELS = {
    "tier_1_config": "Tier 1: configuration",
    "tier_2_content": "Tier 2: content",
    "tier_3_retrieval": "Tier 3: retrieval",
}

_LIGHT_TOKENS = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --border: #e5e7eb; --card: #f9fafb;
  --accent: #2563eb;
  --grade-a-bg: #ecfdf5; --grade-a-fg: #065f46;
  --grade-b-bg: #ecfeff; --grade-b-fg: #155e75;
  --grade-c-bg: #fffbeb; --grade-c-fg: #92400e;
  --grade-d-bg: #ffedd5; --grade-d-fg: #9a3412;
  --grade-f-bg: #fef2f2; --grade-f-fg: #991b1b;
  --pass-bg: #ecfdf5; --pass-fg: #065f46; --pass-dot: #16a34a;
  --warn-bg: #fffbeb; --warn-fg: #92400e; --warn-dot: #d97706;
  --fail-bg: #fef2f2; --fail-fg: #991b1b; --fail-dot: #dc2626;
  --critical-bg: #fee2e2; --critical-fg: #7f1d1d; --critical-dot: #b91c1c;
  --info-bg: #f3f4f6; --info-fg: #4b5563; --info-dot: #9ca3af;
  --bar-track: #e5e7eb; --bar-fill: #2563eb;
  --callout-bg: #fffbeb; --callout-border: #fde68a;
}
""".strip()

_DARK_TOKENS = """
:root {
  --bg: #0f172a; --fg: #e2e8f0; --muted: #94a3b8; --border: #334155; --card: #1e293b;
  --accent: #60a5fa;
  --grade-a-bg: #064e3b; --grade-a-fg: #a7f3d0;
  --grade-b-bg: #0e3a4a; --grade-b-fg: #a5f3fc;
  --grade-c-bg: #422006; --grade-c-fg: #fde68a;
  --grade-d-bg: #431407; --grade-d-fg: #fed7aa;
  --grade-f-bg: #7f1d1d; --grade-f-fg: #fecaca;
  --pass-bg: #064e3b; --pass-fg: #a7f3d0; --pass-dot: #34d399;
  --warn-bg: #422006; --warn-fg: #fde68a; --warn-dot: #fbbf24;
  --fail-bg: #7f1d1d; --fail-fg: #fecaca; --fail-dot: #f87171;
  --critical-bg: #7f1d1d; --critical-fg: #fecaca; --critical-dot: #ef4444;
  --info-bg: #1e293b; --info-fg: #cbd5e1; --info-dot: #64748b;
  --bar-track: #334155; --bar-fill: #60a5fa;
  --callout-bg: #422006; --callout-border: #92400e;
}
""".strip()

_BASE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.wrap { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
.subtitle { color: var(--muted); margin: 0 0 20px; font-size: 13px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 20px; margin-bottom: 16px; }
.card h2 { font-size: 13px; margin: 0 0 12px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.05em; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.score-num { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums; }
.score-num .of { color: var(--muted); font-size: 15px; font-weight: 500; }
.grade-pill { display: inline-flex; align-items: center; justify-content: center; min-width: 40px;
  height: 40px; padding: 0 14px; border-radius: 8px; font-weight: 700; font-size: 18px; }
.grade-A { background: var(--grade-a-bg); color: var(--grade-a-fg); }
.grade-B { background: var(--grade-b-bg); color: var(--grade-b-fg); }
.grade-C { background: var(--grade-c-bg); color: var(--grade-c-fg); }
.grade-D { background: var(--grade-d-bg); color: var(--grade-d-fg); }
.grade-F { background: var(--grade-f-bg); color: var(--grade-f-fg); }
.tier-row { display: grid; grid-template-columns: 200px 1fr 56px; align-items: center; gap: 12px;
  padding: 6px 0; }
.bar { height: 8px; background: var(--bar-track); border-radius: 4px; overflow: hidden; }
.bar > span { display: block; height: 100%; background: var(--bar-fill); }
.tier-score { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
tr:last-child td { border-bottom: none; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 600; white-space: nowrap; }
.pill .dot { width: 6px; height: 6px; border-radius: 50%; }
.pill.pass { background: var(--pass-bg); color: var(--pass-fg); } .pill.pass .dot { background: var(--pass-dot); }
.pill.warn { background: var(--warn-bg); color: var(--warn-fg); } .pill.warn .dot { background: var(--warn-dot); }
.pill.fail { background: var(--fail-bg); color: var(--fail-fg); } .pill.fail .dot { background: var(--fail-dot); }
.pill.critical { background: var(--critical-bg); color: var(--critical-fg); } .pill.critical .dot { background: var(--critical-dot); }
.pill.info { background: var(--info-bg); color: var(--info-fg); } .pill.info .dot { background: var(--info-dot); }
.callout { background: var(--callout-bg); border: 1px solid var(--callout-border); border-radius: 8px;
  padding: 10px 14px; margin-bottom: 16px; }
.callout ul { margin: 0; padding-left: 18px; }
.evidence { color: var(--muted); }
details.fq { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 0 14px; margin: 8px 0; }
details.fq > summary { padding: 10px 0; cursor: pointer; display: flex; gap: 10px; align-items: center;
  flex-wrap: wrap; }
details.fq[open] > summary { border-bottom: 1px solid var(--border); margin-bottom: 8px; }
.fq-query { font-weight: 600; }
.fq-body { padding: 4px 0 12px; font-size: 13px; }
.fq-row { margin: 6px 0; }
.footnote { font-size: 11px; color: var(--muted); margin-top: 20px; padding-top: 12px;
  border-top: 1px dashed var(--border); }
""".strip()

_FOOTNOTE = (
    "Tier scores are quality measures: for retrieval, the score is the metric value itself "
    "(recall, precision, NDCG, MRR, hit rate). The overall score weights the tiers by the rubric, "
    "rebalancing to Tier 1 and Tier 2 when no ground truth is supplied."
)


def render(scorecard: Scorecard, *, theme: str = "auto") -> str:
    """Render a Scorecard to a self-contained HTML document."""
    css = _theme_css(theme)
    body = "".join([
        _header(scorecard),
        _warnings(scorecard),
        _summary(scorecard),
        _top_fixes(scorecard),
        _tiers(scorecard),
        _failing_queries(scorecard),
        f'<div class="footnote">{_e(_FOOTNOTE)}</div>',
    ])
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Vector Store Scorecard</title>"
        f"<style>{css}</style></head><body><div class=\"wrap\">{body}</div></body></html>\n"
    )


def _theme_css(theme: str) -> str:
    if theme == "light":
        tokens = _LIGHT_TOKENS
    elif theme == "dark":
        tokens = _DARK_TOKENS
    elif theme == "auto":
        tokens = _LIGHT_TOKENS + "\n@media (prefers-color-scheme: dark) {\n" + _DARK_TOKENS + "\n}"
    else:
        raise ValueError(f"Unknown theme {theme!r}; expected auto, light, or dark.")
    return tokens + "\n" + _BASE_CSS


def _header(scorecard: Scorecard) -> str:
    grade_class = _grade_class(scorecard.grade)
    return (
        '<div class="head">'
        f"<div><h1>Vector Store Scorecard</h1><p class=\"subtitle\">{_subtitle(scorecard)}</p></div>"
        '<div style="display:flex;align-items:center;gap:14px">'
        f'<div class="score-num">{scorecard.overall_score}<span class="of"> / 100</span></div>'
        f'<span class="grade-pill {grade_class}">{_e(scorecard.grade)}</span>'
        "</div></div>"
    )


def _warnings(scorecard: Scorecard) -> str:
    warnings = (scorecard.metadata or {}).get("warnings") or []
    if not warnings:
        return ""
    items = "".join(f"<li>{_e(w)}</li>" for w in warnings)
    return f'<div class="callout"><strong>Warnings</strong><ul>{items}</ul></div>'


def _summary(scorecard: Scorecard) -> str:
    rows = []
    for tier in _TIER_ORDER:
        if tier not in scorecard.tier_scores:
            continue
        score = scorecard.tier_scores[tier]
        rows.append(
            f'<div class="tier-row"><span>{_e(_TIER_LABELS[tier])}</span>'
            f'<span class="bar"><span style="width:{score}%"></span></span>'
            f'<span class="tier-score">{score}</span></div>'
        )
    return f'<div class="card"><h2>Per-tier scores</h2>{"".join(rows)}</div>'


def _top_fixes(scorecard: Scorecard) -> str:
    fixes = scorecard.top_fixes()
    if not fixes:
        return ""
    rows = "".join(
        f"<tr><td><code>{_e(r.criterion_id)}</code></td><td>{_pill(r)}</td>"
        f'<td class="num">{r.score:.2f}</td><td class="evidence">{_e(r.message)}</td></tr>'
        for r in fixes
    )
    return (
        '<div class="card"><h2>Top fixes</h2><table>'
        "<thead><tr><th>Criterion</th><th>Severity</th><th>Score</th><th>Finding</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _tiers(scorecard: Scorecard) -> str:
    grouped: dict[str, list[CheckResult]] = {}
    for result in scorecard.results:
        try:
            tier = scorecard.rubric.by_id(result.criterion_id).tier
        except KeyError:
            continue
        grouped.setdefault(tier, []).append(result)

    cards = []
    for tier in _TIER_ORDER:
        results = grouped.get(tier)
        if not results:
            continue
        rows = "".join(
            f"<tr><td><code>{_e(r.criterion_id)}</code></td><td>{_pill(r)}</td>"
            f'<td class="num">{r.score:.2f}</td><td class="evidence">{_e(r.message)}</td></tr>'
            for r in results
        )
        cards.append(
            f'<div class="card"><h2>{_e(_TIER_LABELS[tier])}</h2><table>'
            "<thead><tr><th>Criterion</th><th>Status</th><th>Score</th><th>Finding</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )
    return "".join(cards)


def _failing_queries(scorecard: Scorecard) -> str:
    failing = (scorecard.metadata or {}).get("failing_queries") or []
    if not failing:
        return ""
    blocks = []
    for f in failing:
        classifications = f.get("classifications") or []
        top = classifications[0] if classifications else None
        top_pill = (
            f'<span class="pill fail"><span class="dot"></span>{_e(top["mode"])} {top["confidence"]:.2f}</span>'
            if top else '<span class="pill info"><span class="dot"></span>unclassified</span>'
        )
        retrieved_rows = "".join(
            f'<tr><td><code>{_e(d.get("id"))}</code></td><td class="num">{_fmt_score(d.get("score"))}</td></tr>'
            for d in (f.get("retrieved") or [])
        )
        modes = "".join(
            f"<li><strong>{_e(c.get('mode'))}</strong> ({c.get('confidence', 0):.2f}): {_e(c.get('explanation'))}</li>"
            for c in classifications
        )
        blocks.append(
            '<details class="fq"><summary>'
            f'<span class="fq-query">{_e(f.get("query"))}</span>{top_pill}</summary>'
            '<div class="fq-body">'
            f'<div class="fq-row"><strong>Expected:</strong> {_e(", ".join(f.get("expected_ids") or [])) or "(none)"}</div>'
            f'<div class="fq-row"><strong>Retrieved:</strong></div>'
            f"<table><thead><tr><th>Document</th><th>Score</th></tr></thead><tbody>{retrieved_rows}</tbody></table>"
            + (f'<div class="fq-row"><strong>Likely failure modes:</strong></div><ul>{modes}</ul>' if modes else "")
            + "</div></details>"
        )
    return f'<div class="card"><h2>Failing queries</h2>{"".join(blocks)}</div>'


def _subtitle(scorecard: Scorecard) -> str:
    metadata = scorecard.metadata or {}
    store = metadata.get("store") or {}
    parts = []
    store_type = store.get("store_type")
    target = store.get("index") or store.get("table")
    if store_type:
        parts.append(f"store {store_type}" + (f" ({target})" if target else ""))
    if metadata.get("modes"):
        parts.append("modes: " + ", ".join(metadata["modes"]))
    rubric = metadata.get("rubric") or {}
    if rubric.get("source"):
        parts.append(f"rubric: {rubric['source']}")
    if metadata.get("scored_at"):
        parts.append(str(metadata["scored_at"]))
    return _e(" · ".join(parts))


def _grade_class(grade: str) -> str:
    return f"grade-{grade}" if grade in ("A", "B", "C", "D", "F") else "grade-F"


def _pill(result: CheckResult) -> str:
    status = "pass" if result.passed else (result.severity if result.severity in ("warn", "fail", "critical", "info") else "fail")
    label = "pass" if result.passed else result.severity
    return f'<span class="pill {status}"><span class="dot"></span>{_e(label)}</span>'


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return _e(value)


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)
