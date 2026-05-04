#!/usr/bin/env python3
"""Render a BigQuery evaluation report from a JSON dict.

Reads a JSON report (produced by ``evaluate_bigquery_table.py`` or assembled
by an agent using the BigQuery MCP connector) and writes Markdown + optional
HTML. Optionally evaluates expectation flags against the loaded report and
exits 3 if any fail.

This script has no BigQuery dependency — it only reads JSON and renders. It
exists so the connector path (where queries are issued via MCP tools) and the
script path (which uses ``google-cloud-bigquery`` directly) produce identical
output by sharing the renderer modules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _expectations import evaluate_expectations
from _render import make_html, make_markdown
from _serialize import serialize


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a BigQuery evaluation report.")
    parser.add_argument("--input", required=True, help="Path to a JSON report (use '-' for stdin)")
    parser.add_argument("--output-md", default="bigquery_table_report.md", help="Markdown output path")
    parser.add_argument("--output-html", default=None, help="Optional HTML dashboard path")
    parser.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="HTML dashboard theme")
    parser.add_argument("--expect-min-rows", type=int, default=None)
    parser.add_argument("--expect-zero-duplicates", action="store_true")
    parser.add_argument("--expect-freshness-within", default=None)
    parser.add_argument("--expect-max-null-rate", action="append", default=None, metavar="COL=RATE")
    parser.add_argument("--expect-no-schema-drift", action="store_true")
    return parser.parse_args()


def _load_report(path: str) -> dict:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    args = parse_args()
    report = _load_report(args.input)

    has_expectations = (
        args.expect_min_rows is not None
        or args.expect_zero_duplicates
        or args.expect_freshness_within
        or args.expect_max_null_rate
        or args.expect_no_schema_drift
    )
    if has_expectations:
        report["expectations"] = evaluate_expectations(report, args)

    serialized = serialize(report)

    Path(args.output_md).write_text(make_markdown(serialized), encoding="utf-8")
    print(f"Wrote {args.output_md}")

    if args.output_html:
        Path(args.output_html).write_text(make_html(serialized, theme=args.theme), encoding="utf-8")
        print(f"Wrote {args.output_html}")

    if has_expectations and any(e.get("status") == "failed" for e in report["expectations"]):
        failed = [e["name"] for e in report["expectations"] if e.get("status") == "failed"]
        print(f"Expectation(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
