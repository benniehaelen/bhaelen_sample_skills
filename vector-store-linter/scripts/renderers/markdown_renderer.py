"""Markdown renderer.

Renders a Scorecard to Markdown suitable for a terminal, a PR comment, or an
editor preview. Layout: title and subtitle, a summary (overall score, grade,
per-tier scores), any warnings (synthetic ground truth is surfaced here), the
ranked top fixes, a per-tier criterion breakdown, and, for retrieval runs, a
failing-queries table with the most likely failure mode per query.

Style follows the sibling skills: an H1 title, an italic subtitle line, H2
sections, and pipe tables. Output is plain text with no em-dashes.
"""

from __future__ import annotations

from typing import Any

from scoring import CheckResult, Scorecard

_TIER_ORDER = ("tier_1_config", "tier_2_content", "tier_3_retrieval")
_TIER_LABELS = {
    "tier_1_config": "Tier 1: configuration",
    "tier_2_content": "Tier 2: content",
    "tier_3_retrieval": "Tier 3: retrieval",
}


def render(scorecard: Scorecard, theme: str | None = None) -> str:
    """Render a Scorecard to a Markdown string.

    theme is accepted for a uniform renderer signature and ignored.
    """
    del theme
    lines: list[str] = ["# Vector Store Scorecard", ""]
    lines.extend(_subtitle(scorecard))
    lines.append("")

    lines += ["## Summary", ""]
    lines.append(f"- Overall: {scorecard.overall_score}/100 ({scorecard.grade})")
    for tier in _TIER_ORDER:
        if tier in scorecard.tier_scores:
            lines.append(f"- {_TIER_LABELS[tier]}: {scorecard.tier_scores[tier]}/100")
    lines.append("")

    warnings = (scorecard.metadata or {}).get("warnings") or []
    if warnings:
        lines += ["## Warnings", ""]
        lines += [f"- {w}" for w in warnings]
        lines.append("")

    fixes = scorecard.top_fixes()
    if fixes:
        lines += ["## Top fixes", "", "| Criterion | Severity | Score | Finding |", "| --- | --- | ---: | --- |"]
        for r in fixes:
            lines.append(f"| `{r.criterion_id}` | {r.severity} | {r.score:.2f} | {_cell(r.message)} |")
        lines.append("")

    by_tier = _group_by_tier(scorecard)
    for tier in _TIER_ORDER:
        tier_results = by_tier.get(tier)
        if not tier_results:
            continue
        lines += [f"## {_TIER_LABELS[tier]}", "", "| Criterion | Status | Score | Finding |", "| --- | --- | ---: | --- |"]
        for r in tier_results:
            lines.append(f"| `{r.criterion_id}` | {_status(r)} | {r.score:.2f} | {_cell(r.message)} |")
        lines.append("")

    failing = (scorecard.metadata or {}).get("failing_queries") or []
    if failing:
        lines += [
            "## Failing queries",
            "",
            "| Query | Type | Expected | Retrieved (top) | Likely failure |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in failing:
            classifications = f.get("classifications") or []
            top = classifications[0] if classifications else None
            failure = f"{top['mode']} ({top['confidence']:.2f})" if top else "unclassified"
            retrieved_ids = [d.get("id", "") for d in (f.get("retrieved") or [])][:3]
            lines.append(
                f"| {_cell(f.get('query'))} | {_cell(f.get('query_type') or '')} | "
                f"{_cell(', '.join(f.get('expected_ids') or []))} | "
                f"{_cell(', '.join(retrieved_ids))} | {failure} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _subtitle(scorecard: Scorecard) -> list[str]:
    metadata = scorecard.metadata or {}
    store = metadata.get("store") or {}
    parts: list[str] = []
    store_type = store.get("store_type")
    target = store.get("index") or store.get("table")
    if store_type:
        parts.append(f"store `{store_type}`" + (f" ({target})" if target else ""))
    modes = metadata.get("modes")
    if modes:
        parts.append("modes: " + ", ".join(modes))
    rubric = metadata.get("rubric") or {}
    if rubric.get("source"):
        parts.append(f"rubric: {rubric['source']}")
    out = [f"_{' . '.join(parts)}_"] if parts else []
    scored_at = metadata.get("scored_at")
    if scored_at:
        out.append(f"_Scored: {scored_at}_")
    return out


def _group_by_tier(scorecard: Scorecard) -> dict[str, list[CheckResult]]:
    grouped: dict[str, list[CheckResult]] = {}
    for result in scorecard.results:
        try:
            tier = scorecard.rubric.by_id(result.criterion_id).tier
        except KeyError:
            continue
        grouped.setdefault(tier, []).append(result)
    return grouped


def _status(result: CheckResult) -> str:
    return "[pass]" if result.passed else f"[{result.severity}]"


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")
