"""Per-tool scoring, catalog roll-up, and top-refactor ranking.

Single source of truth for the score formula in ``SKILL.md``. Applies the
rule modules from ``_rules`` to each tool, computes catalog-level metrics,
and assembles the JSON report dict that ``render_scorecard.py`` consumes.

Public entry point:

- ``score_catalog(catalog, rubric, tokenizer, serialization)`` returns a
  report dict matching the shape documented in ``SKILL.md`` Step 6.

The function mutates the input ``catalog``: each tool dict gets a
``tokens`` block attached before rules run, so subsequent calls observe
the precomputed counts. Callers that need the original payload should
copy the catalog before passing it in.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from _rules import CriterionResult, all_rules
from _suggest import make_issues
from _tokenizer import count_tool

RUBRIC_VERSION = "1.0"


# ----- public API -----------------------------------------------------------


def score_catalog(
    catalog: dict[str, Any],
    rubric: dict[str, Any],
    tokenizer: str,
    serialization: str,
) -> dict[str, Any]:
    """Score every tool in the catalog and return the full report dict."""
    tools = catalog.get("tools", [])
    for tool in tools:
        tool["tokens"] = count_tool(tool, tokenizer, serialization)

    scored_tools = [_score_one_tool(tool, catalog, rubric) for tool in tools]
    # Worst-first ordering per SKILL.md: lowest score, ties broken by highest tokens.
    scored_tools.sort(key=lambda t: (t["score"], -t["tokens"]["total"]))

    catalog_summary = _catalog_summary(scored_tools, rubric)
    warnings = _build_warnings(catalog, tokenizer, rubric)

    return {
        "rubric_version": RUBRIC_VERSION,
        "rubric_config": _rubric_config_provenance(rubric),
        "tokenizer": tokenizer,
        "serialization": serialization,
        "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "server": catalog.get("server", {}),
        "catalog": catalog_summary,
        "tools": scored_tools,
        "expectations": [],
        "warnings": warnings,
    }


def grade_from_score(score: int, cutoffs: dict[str, Any]) -> str:
    """Letter grade per the rubric's ``grade_cutoffs``."""
    if score >= int(cutoffs.get("A", 90)):
        return "A"
    if score >= int(cutoffs.get("B", 80)):
        return "B"
    if score >= int(cutoffs.get("C", 70)):
        return "C"
    if score >= int(cutoffs.get("D", 60)):
        return "D"
    return "F"


# ----- per-tool scoring -----------------------------------------------------


def _score_one_tool(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    results: list[CriterionResult] = [check(tool, catalog, rubric) for _name, check in all_rules()]

    # N/A criteria are excluded from the denominator and from the output.
    applicable = [r for r in results if not r.n_a]
    points = sum(r.points for r in applicable)
    max_points = sum(r.max for r in applicable)
    score = round(100 * points / max_points) if max_points else 0
    grade = grade_from_score(score, rubric.get("grade_cutoffs", {}))

    return {
        "name": tool["name"],
        "score": score,
        "grade": grade,
        "tokens": tool["tokens"],
        "annotations": tool.get("annotations", {}),
        "criteria": [_criterion_dict(r) for r in applicable],
        "issues": make_issues(tool, results, rubric),
    }


def _criterion_dict(r: CriterionResult) -> dict[str, Any]:
    return {
        "name": r.name,
        "tier": r.tier,
        "points": r.points,
        "max": r.max,
        "passed": r.passed,
        "evidence": r.evidence,
        "tokens_saved": r.tokens_saved,
        "estimated": r.estimated,
    }


# ----- catalog roll-up ------------------------------------------------------


def _catalog_summary(scored_tools: list[dict[str, Any]], rubric: dict[str, Any]) -> dict[str, Any]:
    total_tokens = sum(t["tokens"]["total"] for t in scored_tools)

    measured, estimated = _addressable_savings(scored_tools)
    catalog_score, catalog_grade = _catalog_grade(scored_tools, rubric)
    top_offenders = _top_offenders(scored_tools, total_tokens)
    top_refactors = _top_refactors(scored_tools)

    return {
        "tool_count": len(scored_tools),
        "total_tokens": total_tokens,
        "addressable_savings_measured": measured,
        "addressable_savings_estimated": estimated,
        "catalog_score": catalog_score,
        "catalog_grade": catalog_grade,
        "top_offenders": top_offenders,
        "top_refactors": top_refactors,
    }


def _addressable_savings(scored_tools: list[dict[str, Any]]) -> tuple[int, int]:
    """Tier 1 fails contribute to ``measured``; Tier 2 fails to ``estimated``."""
    measured = 0
    estimated = 0
    for t in scored_tools:
        for c in t["criteria"]:
            if c["passed"]:
                continue
            saved = int(c["tokens_saved"])
            if saved <= 0:
                continue
            if c["tier"] == 1:
                measured += saved
            else:
                estimated += saved
    return measured, estimated


def _catalog_grade(scored_tools: list[dict[str, Any]], rubric: dict[str, Any]) -> tuple[int, str]:
    """Weighted-mean score and grade per ``catalog_grade_weighting``."""
    if not scored_tools:
        return 0, "F"
    mode = rubric.get("catalog_grade_weighting", "by_token_cost")
    if mode == "by_token_cost":
        weights = [t["tokens"]["total"] for t in scored_tools]
    else:
        weights = [1] * len(scored_tools)
    total_w = sum(weights) or 1
    score = round(sum(t["score"] * w for t, w in zip(scored_tools, weights)) / total_w)
    return score, grade_from_score(score, rubric.get("grade_cutoffs", {}))


def _top_offenders(scored_tools: list[dict[str, Any]], total_tokens: int) -> list[dict[str, Any]]:
    """The 5 tools with the highest absolute token cost."""
    by_tokens = sorted(scored_tools, key=lambda t: t["tokens"]["total"], reverse=True)
    return [
        {
            "tool": t["name"],
            "tokens": t["tokens"]["total"],
            "pct_of_catalog": round(100.0 * t["tokens"]["total"] / total_tokens, 1) if total_tokens else 0.0,
        }
        for t in by_tokens[:5]
    ]


def _top_refactors(scored_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All failed criteria with positive tokens_saved, sorted high-to-low, capped at 10."""
    refactors: list[dict[str, Any]] = []
    suggestions_by_tool: dict[str, dict[str, str]] = {
        t["name"]: {issue["criterion"]: issue["suggestion"] for issue in t["issues"]}
        for t in scored_tools
    }
    for t in scored_tools:
        for c in t["criteria"]:
            if c["passed"]:
                continue
            saved = int(c["tokens_saved"])
            if saved <= 0:
                continue
            refactors.append({
                "tool": t["name"],
                "criterion": c["name"],
                "tier": c["tier"],
                "tokens_saved": saved,
                "estimated": c["estimated"],
                "message": c["evidence"],
                "suggestion": suggestions_by_tool.get(t["name"], {}).get(c["name"], ""),
            })
    refactors.sort(key=lambda r: r["tokens_saved"], reverse=True)
    return refactors[:10]


# ----- warnings & provenance -----------------------------------------------


def _build_warnings(catalog: dict[str, Any], tokenizer: str, rubric: dict[str, Any]) -> list[str]:
    """Server-export warnings + tokenizer methodology note."""
    warnings: list[str] = []

    extras = catalog.get("extras", {}) or {}
    rc = int(extras.get("resource_count", 0) or 0)
    pc = int(extras.get("prompt_count", 0) or 0)
    if rc or pc:
        parts = []
        if rc:
            parts.append(f"{rc} resource(s)")
        if pc:
            parts.append(f"{pc} prompt(s)")
        warnings.append(f"Server exposes {' and '.join(parts)}; this skill scores tools only.")

    if tokenizer == "cl100k_base":
        warnings.append(
            "Tokenizer is cl100k_base; absolute counts vary ~10-20% for Claude. Relative ranking is stable."
        )

    # Catalog weighting fallback note.
    mode = rubric.get("catalog_grade_weighting", "by_token_cost")
    if mode not in ("by_token_cost", "equal"):
        warnings.append(f"Unknown catalog_grade_weighting {mode!r}; falling back to equal weighting.")

    return warnings


def _rubric_config_provenance(rubric: dict[str, Any]) -> dict[str, Any]:
    """Provenance block from ``rubric._meta`` if the loader set it, else builtin defaults."""
    meta = rubric.get("_meta", {}) or {}
    return {
        "source": meta.get("source", "builtin"),
        "name": rubric.get("name", "token-economy-default"),
        "version": rubric.get("version", "1.0"),
        "sha256": meta.get("sha256", ""),
    }
