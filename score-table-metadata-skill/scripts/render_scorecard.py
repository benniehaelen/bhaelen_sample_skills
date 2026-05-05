#!/usr/bin/env python3
"""Render a metadata scorecard from a JSON report.

Reads a JSON scorecard (produced by ``score_table_metadata.py`` or assembled
by an agent using the BigQuery MCP connector following ``SKILL.md``) and
writes Markdown + optional HTML. Optionally evaluates the ``min_score``
expectation against the loaded report and exits 3 if any table falls below.

This script has no BigQuery dependency — it only reads JSON and renders. It
exists so the connector path (Path A) and the script path (Path B) produce
identical output by sharing the renderer module.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _scorecard_render import make_html, make_markdown
from _serialize import serialize


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a BigQuery metadata scorecard.")
    parser.add_argument("--input", required=True, help="Path to a JSON scorecard (use '-' for stdin)")
    parser.add_argument("--output-md", default="metadata_scorecard.md", help="Markdown output path")
    parser.add_argument("--output-html", default=None, help="Optional HTML scorecard path")
    parser.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="HTML theme")
    parser.add_argument("--expect-min-score", type=int, default=None, help="Fail (exit 3) if any table scores below N")
    return parser.parse_args()


def _load_report(path: str) -> dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _evaluate_min_score(report: dict[str, Any], min_score: int) -> dict[str, Any]:
    failing = [
        {"table_id": t["table_id"], "score": t["score"]}
        for t in report.get("tables") or []
        if t.get("score", 0) < min_score
    ]
    return {
        "name": "min_score",
        "threshold": min_score,
        "status": "passed" if not failing else "failed",
        "failing_tables": failing,
    }


def main() -> int:
    args = parse_args()
    report = _load_report(args.input)

    if args.expect_min_score is not None:
        expectations = list(report.get("expectations") or [])
        # Replace any prior min_score expectation so re-renders reflect the new threshold.
        expectations = [e for e in expectations if e.get("name") != "min_score"]
        expectations.append(_evaluate_min_score(report, args.expect_min_score))
        report["expectations"] = expectations

    serialized = serialize(report)

    Path(args.output_md).write_text(make_markdown(serialized), encoding="utf-8")
    print(f"Wrote {args.output_md}")

    if args.output_html:
        Path(args.output_html).write_text(make_html(serialized, theme=args.theme), encoding="utf-8")
        print(f"Wrote {args.output_html}")

    if args.expect_min_score is not None:
        for e in serialized.get("expectations") or []:
            if e.get("name") == "min_score" and e.get("status") == "failed":
                failed = [t["table_id"] for t in e.get("failing_tables", [])]
                print(f"Expectation failed: {len(failed)} table(s) below {args.expect_min_score}: {', '.join(failed)}", file=sys.stderr)
                return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
