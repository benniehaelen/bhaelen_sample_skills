#!/usr/bin/env python3
"""Score one or more BigQuery tables against the data-steward metadata rubric.

This is the local-script (Path B) entry point. It fetches table metadata
via the ``google-cloud-bigquery`` client (no data scanning), applies the
deterministic heuristic from ``_rubric.py``, and writes a scorecard.

Path A — the BigQuery MCP connector path — is documented in ``SKILL.md``
and emits the same JSON shape so ``render_scorecard.py`` can render it
without a BigQuery dependency.

Code organization:

- Validation helpers live in ``_validation.py``.
- JSON-safe serialization lives in ``_serialize.py``.
- Heuristic rubric lives in ``_rubric.py``.
- Markdown / HTML rendering lives in ``_scorecard_render.py``.

This module owns the CLI, the BigQuery client interactions, and ``main()``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any, TYPE_CHECKING

from _rubric import (
    DEFAULT_CONFIG,
    RUBRIC_VERSION,
    load_rubric_config,
    rubric_config_metadata,
    score_table,
)
from _scorecard_render import make_html, make_markdown
from _serialize import serialize
from _validation import csv_arg, split_dataset_id, split_table_id

if TYPE_CHECKING:
    from google.cloud import bigquery


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the Path B CLI."""
    parser = argparse.ArgumentParser(description="Score BigQuery table metadata against the data-steward rubric.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--dataset", default=None, help="Score every table in project.dataset")
    scope.add_argument("--tables", default=None, help="Comma-separated list of project.dataset.table IDs")
    parser.add_argument("--billing-project", default=None, help="Project used by the BigQuery client")
    parser.add_argument("--location", default=None, help="BigQuery location, e.g. US")
    parser.add_argument("--output-json", default="metadata_scorecard.json", help="JSON report path")
    parser.add_argument("--output-md", default="metadata_scorecard.md", help="Markdown report path")
    parser.add_argument("--output-html", default=None, help="Optional self-contained HTML scorecard path")
    parser.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="Dashboard theme")
    parser.add_argument("--expect-min-score", type=int, default=None, help="Fail (exit 3) if any table scores below N")
    parser.add_argument(
        "--rubric-config",
        default=None,
        help="Path to a JSON rubric config file (overrides weights, keywords, regexes, thresholds, grade cutoffs). "
             "Omitted sections fall back to the built-in defaults.",
    )
    return parser.parse_args()


def _normalize_table(table: "bigquery.Table") -> dict[str, Any]:
    """Convert a ``bigquery.Table`` into the rubric's normalized input shape.

    BigQuery's ``full_table_id`` uses ``project:dataset.table`` (colon
    after project); we rewrite it to the dotted form everywhere else uses.
    Policy tags are flattened to the underlying name list — that's what
    ``_check_sensitivity_flagged`` looks at.

    Nested fields (``RECORD`` / ``STRUCT`` columns and ``ARRAY<STRUCT>``,
    which BigQuery represents as ``mode=REPEATED, field_type=RECORD``) are
    walked recursively. Each leaf and each parent struct gets its own entry
    in ``columns`` with a dotted name (``address.street``, ``events.event_id``)
    and a ``parent`` field linking to the immediate parent's dotted name
    (``None`` for top-level columns). Each entry is graded with the same
    rubric — leaf names like ``user.email`` correctly trigger the sensitivity
    criterion because ``_SENSITIVE_RE``'s boundaries accept ``.`` as well as ``_``.
    """
    columns: list[dict[str, Any]] = []

    def walk(field: Any, parent: str | None = None) -> None:
        full_name = f"{parent}.{field.name}" if parent else field.name
        policy_tags = (field.policy_tags.names if getattr(field, "policy_tags", None) else None)
        columns.append({
            "name": full_name,
            "type": field.field_type,
            "mode": field.mode,
            "description": field.description,
            "policy_tags": list(policy_tags or []),
            "parent": parent,
        })
        for sub in getattr(field, "fields", None) or ():
            walk(sub, parent=full_name)

    for top_field in table.schema:
        walk(top_field)

    return {
        "table_id": table.full_table_id.replace(":", "."),
        "description": table.description,
        "labels": dict(table.labels or {}),
        "columns": columns,
    }


def _enumerate_dataset(client: "bigquery.Client", dataset_id: str) -> list[str]:
    """List every table in a ``project.dataset`` as fully qualified table IDs."""
    project, dataset = split_dataset_id(dataset_id)
    full_ref = f"{project}.{dataset}"
    listed = client.list_tables(full_ref)
    return [f"{project}.{dataset}.{ref.table_id}" for ref in listed]


def _evaluate_expectations(report: dict[str, Any], min_score: int | None) -> list[dict[str, Any]]:
    """Build the expectations block for the report, given the user's threshold.

    Returns an empty list when no threshold was set. When set, returns a
    single expectation entry naming any tables below the threshold so the
    renderer can show them and the CLI can exit non-zero.
    """
    if min_score is None:
        return []
    failing = [
        {"table_id": t["table_id"], "score": t["score"]}
        for t in report["tables"]
        if t.get("score", 0) < min_score
    ]
    return [{
        "name": "min_score",
        "threshold": min_score,
        "status": "passed" if not failing else "failed",
        "failing_tables": failing,
    }]


def main() -> int:
    args = parse_args()
    warnings: list[str] = []

    try:
        if args.dataset:
            split_dataset_id(args.dataset)
            scope = {"dataset": args.dataset}
            table_ids: list[str] = []  # populated after client is created
        else:
            table_ids = csv_arg_tables(args.tables)
            scope = {"tables": list(table_ids)}
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    try:
        rubric_config = load_rubric_config(args.rubric_config) if args.rubric_config else DEFAULT_CONFIG
    except (OSError, ValueError) as exc:
        print(f"Rubric config error: {exc}", file=sys.stderr)
        return 2
    rubric_meta = rubric_config_metadata(
        rubric_config, args.rubric_config if args.rubric_config else None,
    )

    try:
        from google.cloud import bigquery
        from google.api_core.exceptions import GoogleAPIError
    except ImportError as exc:
        print(
            f"google-cloud-bigquery is required for direct script use ({exc}). "
            "Install it via `pip install -r requirements.txt`, or use the "
            "BigQuery MCP connector + render_scorecard.py path instead.",
            file=sys.stderr,
        )
        return 1

    client = bigquery.Client(project=args.billing_project, location=args.location)

    if args.dataset:
        try:
            table_ids = _enumerate_dataset(client, args.dataset)
        except GoogleAPIError as exc:
            print(f"BigQuery API error while listing dataset: {exc}", file=sys.stderr)
            return 1
        scope["tables"] = list(table_ids)
        if not table_ids:
            warnings.append(f"Dataset {args.dataset} contains no tables.")

    scored_tables: list[dict[str, Any]] = []
    for table_id in table_ids:
        try:
            tbl = client.get_table(table_id)
        except GoogleAPIError as exc:
            warnings.append(f"Could not fetch {table_id}: {exc}")
            continue
        normalized = _normalize_table(tbl)
        scored_tables.append(score_table(normalized, config=rubric_config))

    report = {
        "rubric_version": RUBRIC_VERSION,
        "rubric_config": rubric_meta,
        "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "scope": scope,
        "tables": scored_tables,
        "warnings": warnings,
    }

    expectations = _evaluate_expectations(report, args.expect_min_score)
    if expectations:
        report["expectations"] = expectations

    serialized = serialize(report)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2, sort_keys=True)
    with open(args.output_md, "w", encoding="utf-8") as f:
        f.write(make_markdown(serialized))
    print(f"Wrote {args.output_json}")
    print(f"Wrote {args.output_md}")

    if args.output_html:
        with open(args.output_html, "w", encoding="utf-8") as f:
            f.write(make_html(serialized, theme=args.theme))
        print(f"Wrote {args.output_html}")

    if expectations and any(e.get("status") == "failed" for e in expectations):
        failed = [t["table_id"] for e in expectations for t in e.get("failing_tables", [])]
        print(f"Expectation failed: {len(failed)} table(s) below min_score: {', '.join(failed)}", file=sys.stderr)
        return 3
    return 0


def csv_arg_tables(value: str | None) -> list[str]:
    """Parse a comma-separated list of fully qualified table IDs, validating each.

    Raises ``ValueError`` (via ``split_table_id``) on any malformed entry,
    so the CLI catches input errors before contacting BigQuery.
    """
    if not value:
        return []
    out: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        split_table_id(part)
        out.append(part)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
