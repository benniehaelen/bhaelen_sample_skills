"""Markdown and HTML rendering for evaluation reports.

The HTML output is a single self-contained file with inline CSS and SVG —
no external assets, no JavaScript. Theming uses CSS custom properties only;
see ``_HTML_LIGHT_TOKENS`` / ``_HTML_DARK_TOKENS``.

This module is the single source of truth for both the bundled CLI script
(``evaluate_bigquery_table.py``) and the connector-driven path
(``render_report.py``). Drift between rendering paths cannot occur because
both call the same functions defined here.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
from typing import Any, Iterable

from _serialize import bytes_human, format_datetime, humanize_seconds, short_int


# ---------------------------------------------------------------------------
# CSS tokens and base styles
# ---------------------------------------------------------------------------

_HTML_LIGHT_TOKENS = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --border: #e5e7eb; --card: #f9fafb;
  --accent: #2563eb; --success: #16a34a; --warn: #d97706; --danger: #dc2626;
  --pill-pass-bg: #ecfdf5; --pill-pass-fg: #065f46;
  --pill-fail-bg: #fef2f2; --pill-fail-fg: #991b1b;
  --pill-skip-bg: #f3f4f6; --pill-skip-fg: #6b7280;
  --pill-error-bg: #fffbeb; --pill-error-fg: #92400e;
  --warn-bg: #fffbeb; --warn-border: #fde68a;
  --code-bg: #0f172a; --code-fg: #e2e8f0;
  --bar-zero: #cbd5e1; --grid: #e5e7eb;
}
""".strip()

_HTML_DARK_TOKENS = """
:root {
  --bg: #0f172a; --fg: #e2e8f0; --muted: #94a3b8; --border: #334155; --card: #1e293b;
  --accent: #60a5fa; --success: #34d399; --warn: #fbbf24; --danger: #f87171;
  --pill-pass-bg: #064e3b; --pill-pass-fg: #a7f3d0;
  --pill-fail-bg: #7f1d1d; --pill-fail-fg: #fecaca;
  --pill-skip-bg: #1e293b; --pill-skip-fg: #94a3b8;
  --pill-error-bg: #78350f; --pill-error-fg: #fde68a;
  --warn-bg: #422006; --warn-border: #92400e;
  --code-bg: #020617; --code-fg: #e2e8f0;
  --bar-zero: #475569; --grid: #334155;
}
""".strip()

_HTML_BASE_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 960px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; word-break: break-all; }
h1 code { background: var(--card); padding: 2px 8px; border-radius: 6px; font-size: 18px; }
h2 { font-size: 16px; margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
.subtitle { color: var(--muted); margin: 0 0 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.metric { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.metric .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
.metric .value { font-size: 18px; font-weight: 600; margin-top: 4px; word-break: break-all; }
.pills { display: flex; flex-wrap: wrap; gap: 8px; }
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 10px; border-radius: 999px; font-size: 12px; font-weight: 500;
  border: 1px solid transparent;
}
.pill .dot { width: 8px; height: 8px; border-radius: 50%; }
.pill.passed { background: var(--pill-pass-bg); color: var(--pill-pass-fg); } .pill.passed .dot { background: var(--success); }
.pill.failed { background: var(--pill-fail-bg); color: var(--pill-fail-fg); } .pill.failed .dot { background: var(--danger); }
.pill.skipped_no_data { background: var(--pill-skip-bg); color: var(--pill-skip-fg); } .pill.skipped_no_data .dot { background: var(--muted); }
.pill.error { background: var(--pill-error-bg); color: var(--pill-error-fg); } .pill.error .dot { background: var(--warn); }
.warn-list { background: var(--warn-bg); border: 1px solid var(--warn-border); border-radius: 8px; padding: 12px 16px; }
.warn-list ul { margin: 0; padding-left: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
code { background: var(--card); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
details { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0 12px; }
details + details { margin-top: 8px; }
summary { padding: 10px 0; cursor: pointer; font-weight: 500; }
details[open] summary { border-bottom: 1px solid var(--border); margin-bottom: 8px; }
pre { background: var(--code-bg); color: var(--code-fg); padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; }
.chart { width: 100%; height: auto; display: block; }
.chart .bar-row:hover .bar { filter: brightness(1.05); }
.chart .label, .chart .value { font: 12px -apple-system, sans-serif; fill: var(--fg); }
.chart .label { fill: var(--muted); }
.chart .grid-label, .chart .chart-title { font: 10px -apple-system, sans-serif; fill: var(--muted); }
.chart .gridline { stroke: var(--grid); stroke-dasharray: 2 3; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 11px; color: var(--muted); margin: 4px 0 8px; }
.chart-legend .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; vertical-align: middle; margin-right: 4px; }
.check-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; }
.check-card h3 { font-size: 14px; margin: 0 0 6px; }
.check-card dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0; font-size: 12px; }
.check-card dt { color: var(--muted); }
.check-card dd { margin: 0; }
""".strip()


def _theme_css(theme: str) -> str:
    """Assemble inline CSS for the requested theme.

    ``light`` / ``dark`` bake in a single palette; ``auto`` ships both
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
# Small formatting helpers
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    """HTML-escape a value for safe interpolation; renders ``None`` as empty."""
    return html.escape("" if value is None else str(value), quote=True)


def _format_int(value: Any) -> str:
    """Render ints with thousands separators; pass through everything else."""
    if isinstance(value, bool) or value is None:
        return _e(value)
    if isinstance(value, int):
        return f"{value:,}"
    return _e(value)


# ---------------------------------------------------------------------------
# Chart layout (shared across all SVG charts)
# ---------------------------------------------------------------------------

_CHART_LABEL_W = 200
_CHART_BAR_W = 380
_CHART_ROW_H = 30
_CHART_PAD = 16
_CHART_HEADER_H = 28
_CHART_VALUE_GUTTER = 130  # right-side space for the value label

# Null-rate severity buckets used by the per-column null-rate chart.
# Thresholds reflect typical operational expectations: 0% is ideal,
# ≤1% is healthy, ≤10% is worth a look, >10% is a flag.
_BAR_SEVERITIES = (
    ("zero", "var(--bar-zero)", "0%"),
    ("ok", "var(--accent)", "≤1%"),
    ("warn", "var(--warn)", "≤10%"),
    ("bad", "var(--danger)", ">10%"),
)


def _severity_for_rate(rate: float) -> str:
    """Map a null-rate (0.0–1.0) to a severity bucket key from ``_BAR_SEVERITIES``."""
    if rate <= 0:
        return "zero"
    if rate <= 0.01:
        return "ok"
    if rate <= 0.10:
        return "warn"
    return "bad"


def _gradient_defs(prefix: str, severities: Iterable[tuple[str, str, str]]) -> str:
    """Build SVG ``<defs>`` with one vertical gradient per severity bucket."""
    defs = ['<defs>']
    for key, color, _label in severities:
        defs.append(
            f'<linearGradient id="{prefix}-{key}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" style="stop-color: {color}; stop-opacity: 1"/>'
            f'<stop offset="100%" style="stop-color: {color}; stop-opacity: 0.7"/>'
            f'</linearGradient>'
        )
    defs.append('</defs>')
    return "".join(defs)


def _null_rate_legend() -> str:
    """Render the color-key legend for the null-rate chart."""
    swatches = []
    for _key, color, label in _BAR_SEVERITIES:
        swatches.append(
            f'<span><span class="swatch" style="background: {color}"></span>{_e(label)}</span>'
        )
    return f'<div class="chart-legend">{"".join(swatches)}</div>'


def _null_rate_chart(report: dict[str, Any]) -> str:
    """Render the per-column null-rate SVG bar chart, or empty if no profile data."""
    profile = report.get("checks", {}).get("column_profile")
    if not profile or profile.get("status") != "complete":
        return ""
    rows = profile.get("rows") or []
    if not rows:
        return ""
    first = rows[0]
    scanned = first.get("scanned_rows")
    if not scanned:
        return ""
    scanned_n = int(scanned)
    rates: list[tuple[str, float, int]] = []
    for key, value in first.items():
        if not key.endswith("__null_count"):
            continue
        col = key[: -len("__null_count")]
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        rates.append((col, count / scanned_n, count))
    if not rates:
        return ""
    rates.sort(key=lambda r: r[1], reverse=True)
    rates = rates[:15]

    height = _CHART_PAD * 2 + _CHART_HEADER_H + _CHART_ROW_H * len(rates)
    width = _CHART_LABEL_W + _CHART_BAR_W + _CHART_VALUE_GUTTER
    parts: list[str] = []
    parts.append(_null_rate_legend())
    parts.append(
        f'<svg class="chart" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"'
        f' role="img" aria-label="Per-column null rate">'
    )
    parts.append(_gradient_defs("nrg", _BAR_SEVERITIES))
    parts.append(
        f'<text class="chart-title" x="{_CHART_LABEL_W}" y="{_CHART_PAD + 8}">'
        f'Scanned {scanned_n:,} rows · null counts shown right of each bar'
        f'</text>'
    )

    grid_top = _CHART_PAD + _CHART_HEADER_H - 4
    grid_bottom = height - _CHART_PAD
    for pct in (25, 50, 75, 100):
        x = _CHART_LABEL_W + (pct / 100.0) * _CHART_BAR_W
        parts.append(
            f'<line class="gridline" x1="{x:.1f}" y1="{grid_top}" x2="{x:.1f}" y2="{grid_bottom}"/>'
        )
        parts.append(
            f'<text class="grid-label" x="{x:.1f}" y="{grid_top - 4}" text-anchor="middle">{pct}%</text>'
        )

    for i, (col, rate, count) in enumerate(rates):
        y = _CHART_PAD + _CHART_HEADER_H + i * _CHART_ROW_H
        severity = _severity_for_rate(rate)
        bw = rate * _CHART_BAR_W if rate > 0 else 0
        title = f"{col}: {count:,} of {scanned_n:,} rows null ({rate * 100:.2f}%)"
        parts.append(f'<g class="bar-row"><title>{_e(title)}</title>')
        parts.append(
            f'<text class="label" x="{_CHART_LABEL_W - 12}" y="{y + _CHART_ROW_H // 2 + 4}" text-anchor="end">{_e(col)}</text>'
        )
        if bw > 0:
            parts.append(
                f'<rect class="bar" fill="url(#nrg-{severity})" x="{_CHART_LABEL_W}" y="{y + 4}"'
                f' width="{bw:.1f}" height="{_CHART_ROW_H - 8}" rx="3"/>'
            )
        else:
            parts.append(
                f'<rect class="bar" fill="url(#nrg-zero)" x="{_CHART_LABEL_W}" y="{y + 4}"'
                f' width="3" height="{_CHART_ROW_H - 8}" rx="2"/>'
            )
        text_x = _CHART_LABEL_W + max(bw, 6) + 8
        if count == 0:
            value_label = "0"
        else:
            value_label = f"{count:,} ({rate * 100:.2f}%)"
        parts.append(
            f'<text class="value" x="{text_x:.1f}" y="{y + _CHART_ROW_H // 2 + 4}">{_e(value_label)}</text>'
        )
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def _distinct_count_chart(report: dict[str, Any]) -> str:
    """Render the per-column approx-distinct SVG bar chart, or empty if no profile data."""
    profile = report.get("checks", {}).get("column_profile")
    if not profile or profile.get("status") != "complete":
        return ""
    rows = profile.get("rows") or []
    if not rows:
        return ""
    first = rows[0]
    distincts: list[tuple[str, int]] = []
    for key, value in first.items():
        if not key.endswith("__approx_distinct"):
            continue
        col = key[: -len("__approx_distinct")]
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        distincts.append((col, count))
    if not distincts:
        return ""
    distincts.sort(key=lambda r: r[1], reverse=True)
    distincts = distincts[:15]

    max_count = max(c for _, c in distincts)
    log_max = math.log10(max_count + 1) if max_count > 0 else 1.0
    log_max = max(log_max, 1.0)

    height = _CHART_PAD * 2 + _CHART_HEADER_H + _CHART_ROW_H * len(distincts)
    width = _CHART_LABEL_W + _CHART_BAR_W + _CHART_VALUE_GUTTER
    parts: list[str] = []
    parts.append(
        f'<svg class="chart" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"'
        f' role="img" aria-label="Per-column approximate distinct count">'
    )
    parts.append(_gradient_defs("dcg", (("ok", "var(--accent)", ""),)))
    parts.append(
        f'<text class="chart-title" x="{_CHART_LABEL_W}" y="{_CHART_PAD + 8}">'
        f'Approximate distinct values per column (log scale)'
        f'</text>'
    )

    grid_top = _CHART_PAD + _CHART_HEADER_H - 4
    grid_bottom = height - _CHART_PAD
    decade = 1
    while decade <= 10 ** int(math.ceil(log_max)):
        if decade > max_count and decade > 10:
            break
        x = _CHART_LABEL_W + (math.log10(decade + 1) / log_max) * _CHART_BAR_W
        if x <= _CHART_LABEL_W + _CHART_BAR_W + 0.5:
            parts.append(
                f'<line class="gridline" x1="{x:.1f}" y1="{grid_top}" x2="{x:.1f}" y2="{grid_bottom}"/>'
            )
            parts.append(
                f'<text class="grid-label" x="{x:.1f}" y="{grid_top - 4}" text-anchor="middle">{_e(short_int(decade))}</text>'
            )
        decade *= 10

    for i, (col, count) in enumerate(distincts):
        y = _CHART_PAD + _CHART_HEADER_H + i * _CHART_ROW_H
        log_val = math.log10(count + 1) if count > 0 else 0
        bw = (log_val / log_max) * _CHART_BAR_W if log_max > 0 else 0
        title = f"{col}: ~{count:,} distinct values"
        parts.append(f'<g class="bar-row"><title>{_e(title)}</title>')
        parts.append(
            f'<text class="label" x="{_CHART_LABEL_W - 12}" y="{y + _CHART_ROW_H // 2 + 4}" text-anchor="end">{_e(col)}</text>'
        )
        if bw > 0:
            parts.append(
                f'<rect class="bar" fill="url(#dcg-ok)" x="{_CHART_LABEL_W}" y="{y + 4}"'
                f' width="{bw:.1f}" height="{_CHART_ROW_H - 8}" rx="3"/>'
            )
        else:
            parts.append(
                f'<rect class="bar" fill="url(#dcg-ok)" x="{_CHART_LABEL_W}" y="{y + 4}"'
                f' width="3" height="{_CHART_ROW_H - 8}" rx="2"/>'
            )
        text_x = _CHART_LABEL_W + max(bw, 6) + 8
        parts.append(
            f'<text class="value" x="{text_x:.1f}" y="{y + _CHART_ROW_H // 2 + 4}">{_e(short_int(count))}</text>'
        )
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def _expectation_pill(entry: dict[str, Any]) -> str:
    """Render one expectation as a colored status pill (``passed`` / ``failed`` / ``skipped`` / ``error``)."""
    status = entry.get("status", "skipped_no_data")
    name = entry.get("name", "expectation")
    if entry.get("column"):
        name = f"{name} ({entry['column']})"
    detail = ""
    if status == "failed":
        if entry["name"] == "min_rows":
            detail = f": {entry.get('actual'):,} < {entry.get('expected_min'):,}" if isinstance(entry.get("actual"), int) else ""
        elif entry["name"] == "zero_duplicates":
            detail = f": {entry.get('duplicate_excess_rows', 0):,} excess rows"
        elif entry["name"] == "freshness_within":
            age = entry.get("age_seconds")
            if isinstance(age, int):
                detail = f": {humanize_seconds(age)} ago > {entry.get('max_age')}"
            elif entry.get("reason"):
                detail = f": {entry['reason']}"
        elif entry["name"] == "max_null_rate":
            actual = entry.get("actual_null_rate")
            if isinstance(actual, (int, float)):
                detail = f": {actual * 100:.2f}% > {entry.get('max_null_rate', 0) * 100:.2f}%"
        elif entry["name"] == "no_schema_drift":
            parts = []
            for key in ("added", "removed", "changed"):
                if entry.get(key):
                    parts.append(f"{entry[key]} {key}")
            if parts:
                detail = ": " + ", ".join(parts)
    return (
        f'<span class="pill {_e(status)}" title="{_e(json.dumps(entry, default=str))}">'
        f'<span class="dot"></span>{_e(name)}{_e(detail)}</span>'
    )


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def make_markdown(report: dict[str, Any]) -> str:
    """Render an evaluation report as a Markdown document.

    Layout: title, summary table (rows / bytes / partitioning / clustering),
    expectations strip, warnings, then per-check sections (freshness,
    duplicate keys, column profile, partition health, sample rows) and a
    collapsible schema list. Sections that didn't run are omitted; sections
    that ran but were skipped show their skip reason.
    """
    meta = report["metadata"]
    lines: list[str] = []
    lines.append(f"# BigQuery Table Evaluation: `{meta['table_id']}`")
    lines.append("")
    lines.append("## Summary")
    num_rows = meta.get("num_rows")
    if isinstance(num_rows, int):
        lines.append(f"- Rows: {num_rows:,}")
    else:
        lines.append("- Rows: unknown")
    lines.append(f"- Size: {meta.get('num_bytes_human', 'unknown')}")
    lines.append(f"- Fields: {meta.get('schema_field_count', 0)}")
    lines.append(f"- Created: {format_datetime(meta.get('created'), with_relative=False)}")
    lines.append(f"- Modified: {format_datetime(meta.get('modified'))}")
    lines.append(f"- Type: {meta.get('table_type')}")
    lines.append("")

    lines.append("## Layout")
    tp = meta.get("time_partitioning")
    rp = meta.get("range_partitioning")
    if tp:
        lines.append(f"- Time partitioning: `{tp}`")
    elif rp:
        lines.append(f"- Range partitioning: `{rp}`")
    else:
        lines.append("- Partitioning: none detected")
    clustering = meta.get("clustering_fields") or []
    lines.append(f"- Clustering: {', '.join(clustering) if clustering else 'none detected'}")
    lines.append("")

    lines.append("## Schema")
    lines.append("| Column | Type | Mode | Description |")
    lines.append("|---|---:|---:|---|")
    for field in meta.get("schema", []):
        description = (field.get("description") or "").replace("\n", " ")
        lines.append(f"| `{field['name']}` | `{field['type']}` | `{field['mode']}` | {description} |")
    lines.append("")

    checks = report.get("checks", {})
    lines.append("## Checks")
    if not checks:
        lines.append("- Data checks were not requested. Metadata-only evaluation completed.")
    for name, value in checks.items():
        lines.append(f"### {name.replace('_', ' ').title()}")
        lines.append(f"- Status: `{value.get('status')}`")
        if "estimated_bytes_human" in value:
            lines.append(f"- Dry-run estimate: {value['estimated_bytes_human']}")
        if "bytes_billed_human" in value:
            lines.append(f"- Bytes billed: {value['bytes_billed_human']}")
        if value.get("where_clause"):
            lines.append(f"- Where clause: `{value['where_clause']}`")
        if value.get("rows"):
            first = value["rows"][0]
            for key, item in first.items():
                if key.endswith("_bytes") and isinstance(item, (int, float)):
                    lines.append(f"- {key}: `{item}` ({bytes_human(int(item))})")
                else:
                    lines.append(f"- {key}: `{item}`")
        if value.get("missing_columns"):
            lines.append(f"- Missing columns: {', '.join(value['missing_columns'])}")
        if value.get("profiled_columns"):
            lines.append(f"- Profiled columns: {', '.join(value['profiled_columns'])}")
        lines.append("")

    drift = report.get("schema_drift")
    if drift and "error" not in drift:
        lines.append("## Schema Drift")
        lines.append(f"- Baseline: `{drift.get('baseline_path')}`")
        added = drift.get("added") or []
        removed = drift.get("removed") or []
        changed = drift.get("changed") or []
        if not (added or removed or changed):
            lines.append("- No drift detected.")
        else:
            if added:
                lines.append(f"- Added columns: {', '.join(f'`{c}`' for c in added)}")
            if removed:
                lines.append(f"- Removed columns: {', '.join(f'`{c}`' for c in removed)}")
            for ch in changed:
                parts = []
                if "type" in ch:
                    parts.append(f"type {ch['type']['baseline']} → {ch['type']['current']}")
                if "mode" in ch:
                    parts.append(f"mode {ch['mode']['baseline']} → {ch['mode']['current']}")
                lines.append(f"- Changed `{ch['name']}`: {'; '.join(parts)}")
        lines.append("")

    expectations = report.get("expectations", [])
    if expectations:
        lines.append("## Expectations")
        for entry in expectations:
            label = entry.get("name", "expectation")
            if entry.get("column"):
                label = f"{label} ({entry['column']})"
            lines.append(f"### {label.replace('_', ' ').title()}")
            for key, value in entry.items():
                if key == "name":
                    continue
                lines.append(f"- {key}: `{value}`")
            lines.append("")

    sample = report.get("sample", {})
    if sample.get("rows"):
        rows = sample["rows"]
        rendered = json.dumps(rows, indent=2, default=str)
        truncated_note = ""
        max_chars = 4000
        if len(rendered) > max_chars:
            kept: list[Any] = []
            for row in rows:
                trial = json.dumps(kept + [row], indent=2, default=str)
                if len(trial) > max_chars:
                    break
                kept.append(row)
            rendered = json.dumps(kept, indent=2, default=str)
            truncated_note = f" (showing {len(kept)} of {len(rows)} rows; truncated for readability)"
        lines.append(f"## Sample Rows{truncated_note}")
        lines.append("```json")
        lines.append(rendered)
        lines.append("```")
        lines.append("")

    warnings = report.get("warnings", [])
    if warnings:
        lines.append("## Warnings")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def make_html(report: dict[str, Any], theme: str = "auto") -> str:
    """Render an evaluation report as a single self-contained HTML dashboard.

    Layout: header with table id, key metrics grid (rows / bytes /
    partitioning / clustering), expectation pills, warnings callout,
    SVG charts (null rates and approx-distinct counts), per-check cards,
    and a collapsible schema. All CSS is inlined; no external assets,
    no JavaScript.
    """
    meta = report.get("metadata", {})
    table_id = meta.get("table_id", "(unknown)")
    color_scheme = {"light": "light", "dark": "dark", "auto": "light dark"}.get(theme, "light dark")
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta name="color-scheme" content="{color_scheme}">',
        f"<title>BigQuery Table Evaluation: {_e(table_id)}</title>",
        f"<style>{_theme_css(theme)}</style>",
        "</head><body><div class=\"wrap\">",
        f"<h1>BigQuery Table Evaluation</h1>",
        f'<p class="subtitle"><code>{_e(table_id)}</code></p>',
    ]

    metrics: list[tuple[str, str]] = []
    metrics.append(("Rows", _format_int(meta.get("num_rows"))))
    metrics.append(("Size", _e(meta.get("num_bytes_human", "unknown"))))
    metrics.append(("Fields", _format_int(meta.get("schema_field_count", 0))))
    metrics.append(("Type", _e(meta.get("table_type") or "unknown")))
    if meta.get("modified"):
        metrics.append(("Modified", _e(format_datetime(meta["modified"]))))
    if meta.get("created"):
        metrics.append(("Created", _e(format_datetime(meta["created"], with_relative=False))))
    fresh = report.get("checks", {}).get("freshness") or {}
    if fresh.get("status") == "complete":
        rows = fresh.get("rows") or []
        if rows:
            max_value = rows[0].get("max_value")
            if max_value is not None:
                metrics.append(("Freshness", _e(format_datetime(max_value))))
            else:
                metrics.append(("Freshness", "NULL"))
    parts.append('<div class="grid">')
    for label, value in metrics:
        parts.append(f'<div class="metric"><div class="label">{label}</div><div class="value">{value}</div></div>')
    parts.append("</div>")

    expectations = report.get("expectations") or []
    if expectations:
        parts.append("<h2>Expectations</h2>")
        parts.append('<div class="pills">')
        for entry in expectations:
            parts.append(_expectation_pill(entry))
        parts.append("</div>")

    warnings = report.get("warnings") or []
    if warnings:
        parts.append("<h2>Warnings</h2>")
        parts.append('<div class="warn-list"><ul>')
        for w in warnings:
            parts.append(f"<li>{_e(w)}</li>")
        parts.append("</ul></div>")

    drift = report.get("schema_drift")
    if drift and "error" not in drift:
        parts.append("<h2>Schema Drift</h2>")
        added = drift.get("added") or []
        removed = drift.get("removed") or []
        changed = drift.get("changed") or []
        if not (added or removed or changed):
            parts.append(f"<p>No drift detected vs <code>{_e(drift.get('baseline_path'))}</code>.</p>")
        else:
            parts.append("<ul>")
            for c in added:
                parts.append(f"<li><strong style=\"color:var(--success)\">+</strong> <code>{_e(c)}</code> added</li>")
            for c in removed:
                parts.append(f"<li><strong style=\"color:var(--danger)\">−</strong> <code>{_e(c)}</code> removed</li>")
            for ch in changed:
                bits = []
                if "type" in ch:
                    bits.append(f"type {_e(ch['type']['baseline'])} → {_e(ch['type']['current'])}")
                if "mode" in ch:
                    bits.append(f"mode {_e(ch['mode']['baseline'])} → {_e(ch['mode']['current'])}")
                parts.append(f"<li><strong style=\"color:var(--warn)\">~</strong> <code>{_e(ch['name'])}</code>: {'; '.join(bits)}</li>")
            parts.append("</ul>")

    chart = _null_rate_chart(report)
    if chart:
        parts.append("<h2>Null rates by column</h2>")
        parts.append(chart)

    distinct_chart = _distinct_count_chart(report)
    if distinct_chart:
        parts.append("<h2>Distinct values by column</h2>")
        parts.append(distinct_chart)

    checks = report.get("checks") or {}
    if checks:
        parts.append("<h2>Checks</h2>")
        for name, value in checks.items():
            parts.append('<div class="check-card">')
            parts.append(f"<h3>{_e(name.replace('_', ' ').title())}</h3>")
            parts.append("<dl>")
            parts.append(f"<dt>Status</dt><dd><code>{_e(value.get('status'))}</code></dd>")
            if "estimated_bytes_human" in value:
                parts.append(f"<dt>Dry-run estimate</dt><dd>{_e(value['estimated_bytes_human'])}</dd>")
            if "bytes_billed_human" in value:
                parts.append(f"<dt>Bytes billed</dt><dd>{_e(value['bytes_billed_human'])}</dd>")
            if value.get("where_clause"):
                parts.append(f"<dt>Where</dt><dd><code>{_e(value['where_clause'])}</code></dd>")
            if value.get("rows"):
                first = value["rows"][0]
                for key, item in first.items():
                    if key.endswith("_bytes") and isinstance(item, (int, float)):
                        parts.append(f"<dt>{_e(key)}</dt><dd><code>{_format_int(item)}</code> ({_e(bytes_human(int(item)))})</dd>")
                    else:
                        parts.append(f"<dt>{_e(key)}</dt><dd><code>{_e(item)}</code></dd>")
            if value.get("missing_columns"):
                parts.append(f"<dt>Missing</dt><dd>{_e(', '.join(value['missing_columns']))}</dd>")
            if value.get("profiled_columns"):
                parts.append(f"<dt>Profiled</dt><dd>{_e(', '.join(value['profiled_columns']))}</dd>")
            parts.append("</dl></div>")

    schema = meta.get("schema") or []
    if schema:
        parts.append(f"<h2>Schema ({len(schema)} fields)</h2>")
        parts.append(f"<details><summary>Show fields</summary>")
        parts.append("<table><thead><tr><th>Column</th><th>Type</th><th>Mode</th><th>Description</th></tr></thead><tbody>")
        for field in schema:
            parts.append(
                f"<tr><td><code>{_e(field.get('name'))}</code></td>"
                f"<td><code>{_e(field.get('type'))}</code></td>"
                f"<td><code>{_e(field.get('mode'))}</code></td>"
                f"<td>{_e(field.get('description') or '')}</td></tr>"
            )
        parts.append("</tbody></table></details>")

    sample = report.get("sample") or {}
    sample_rows_data = sample.get("rows") or []
    if sample_rows_data:
        parts.append("<h2>Sample rows</h2>")
        parts.append(f"<details><summary>Show {len(sample_rows_data)} row(s)</summary>")
        parts.append(f"<pre>{_e(json.dumps(sample_rows_data, indent=2, default=str))}</pre>")
        parts.append("</details>")

    parts.append("</div></body></html>")
    return "\n".join(parts)
