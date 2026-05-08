#!/usr/bin/env python3
"""Evaluate a BigQuery table and emit JSON / Markdown / HTML reports.

Conservative by design:

- Metadata checks use the BigQuery table resource (no scan).
- Data-scanning checks are opt-in via ``--run-data-checks``.
- Every SQL check dry-runs first and is skipped when the estimated bytes
  exceed ``--max-bytes-billed``.

Code organization:

- Validation / arg parsing helpers live in ``_validation.py``.
- JSON-safe serialization and human formatting live in ``_serialize.py``.
- Markdown / HTML rendering and SVG charts live in ``_render.py``.
- Expectation evaluation and schema-drift live in ``_expectations.py``.

This module owns the CLI, the BigQuery client interactions, and ``main()``.
``google-cloud-bigquery`` is lazy-imported in the two functions that touch
it so the renderer modules can be reused by ``render_report.py`` without
that dependency installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, TYPE_CHECKING

from _expectations import evaluate_expectations, schema_drift
from _render import make_html, make_markdown
from _serialize import bytes_human, serialize
from _validation import (
    csv_arg,
    quote_column,
    quote_table,
    split_table_id,
    validate_column,
    validate_where_clause,
)

if TYPE_CHECKING:
    from google.cloud import bigquery


# Types that ``APPROX_COUNT_DISTINCT`` accepts directly. We use this set to
# decide which columns get a distinct-cardinality estimate in the column
# profile — calling APPROX_COUNT_DISTINCT on BYTES/GEOGRAPHY/JSON would
# fail at execution time.
APPROX_DISTINCT_TYPES = {
    "STRING",
    "INT64",
    "INTEGER",
    "FLOAT64",
    "FLOAT",
    "NUMERIC",
    "BIGNUMERIC",
    "BOOL",
    "BOOLEAN",
    "DATE",
    "DATETIME",
    "TIME",
    "TIMESTAMP",
}

# All scalar (non-RECORD) types. The column profile collects null counts
# for any of these; only ``APPROX_DISTINCT_TYPES`` get the distinct estimate.
SCALAR_TYPES = APPROX_DISTINCT_TYPES | {"BYTES", "GEOGRAPHY", "JSON"}


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the Path B CLI."""
    parser = argparse.ArgumentParser(description="Evaluate a Google BigQuery table.")
    parser.add_argument("--table", required=True, help="Fully qualified table ID: project.dataset.table")
    parser.add_argument("--billing-project", default=None, help="Project used by the BigQuery client for jobs")
    parser.add_argument("--location", default=None, help="BigQuery location, for example US or europe-west2")
    parser.add_argument("--run-data-checks", action="store_true", help="Run checks that scan table data")
    parser.add_argument("--check-partitions", action="store_true", help="Query INFORMATION_SCHEMA.PARTITIONS for partition health (low cost, also implied by --run-data-checks)")
    parser.add_argument("--freshness-col", default=None, help="DATE/DATETIME/TIMESTAMP column for MAX(column)")
    parser.add_argument("--key-cols", default=None, help="Comma-separated key columns for duplicate-key check")
    parser.add_argument("--profile-cols", default=None, help="Comma-separated columns to profile")
    parser.add_argument("--max-profile-cols", type=int, default=20, help="Max scalar columns to profile when --profile-cols is omitted")
    parser.add_argument("--max-bytes-billed", type=int, default=1_073_741_824, help="Scan cap for each data query; default 1 GiB")
    parser.add_argument("--where", default=None, help="WHERE clause applied to freshness, duplicate-key, and column-profile queries (do not include the WHERE keyword). Backticks, semicolons, and SQL comments are forbidden.")
    parser.add_argument("--sample-limit", type=int, default=0, help="Fetch this many rows via list_rows; 0 disables sample")
    parser.add_argument("--baseline", default=None, help="Path to a prior JSON report; enables schema-drift section against this baseline")
    parser.add_argument("--output-json", default="bigquery_table_report.json", help="JSON report path")
    parser.add_argument("--output-md", default="bigquery_table_report.md", help="Markdown report path")
    parser.add_argument("--output-html", default=None, help="Optional self-contained HTML dashboard path")
    parser.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="Dashboard theme: auto follows the viewer's OS preference (default); light/dark force a single palette")
    parser.add_argument("--expect-min-rows", type=int, default=None, help="Fail (exit 3) if metadata num_rows is below this")
    parser.add_argument("--expect-zero-duplicates", action="store_true", help="Fail (exit 3) if duplicate-key check finds any duplicate excess rows")
    parser.add_argument("--expect-freshness-within", default=None, help="Fail (exit 3) if MAX(freshness_col) is older than this duration, e.g. 24h, 7d, 30m, 90s")
    parser.add_argument("--expect-max-null-rate", action="append", default=None, metavar="COL=RATE", help="Fail (exit 3) if column null rate exceeds RATE in [0, 1]; repeatable")
    parser.add_argument("--expect-no-schema-drift", action="store_true", help="Fail (exit 3) if --baseline reveals added, removed, or retyped columns")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# BigQuery-specific helpers (require the google-cloud-bigquery client)
# ---------------------------------------------------------------------------

def field_to_dict(field: "bigquery.SchemaField") -> dict[str, Any]:
    """Recursively convert a ``SchemaField`` into a JSON-safe dict, preserving nested ``RECORD`` structure."""
    return {
        "name": field.name,
        "type": field.field_type,
        "mode": field.mode,
        "description": field.description,
        "fields": [field_to_dict(child) for child in field.fields],
    }


def is_top_level_scalar(field: "bigquery.SchemaField") -> bool:
    """True if a column is profile-eligible: scalar type, not REPEATED.

    REPEATED columns and nested RECORD types are excluded from the default
    profile because COUNTIF / APPROX_COUNT_DISTINCT would scan/aggregate
    array elements, which scales worse and changes the meaning of "null".
    """
    return field.mode != "REPEATED" and field.field_type.upper() in SCALAR_TYPES


def metadata_report(table: "bigquery.Table") -> dict[str, Any]:
    """Build the ``metadata`` block of the report from a ``bigquery.Table``.

    No data is scanned — this reads only the table resource fields
    (description, partitioning, clustering, labels, schema, row/byte
    counts that BigQuery already maintains as metadata).
    """
    time_partitioning = None
    if table.time_partitioning:
        time_partitioning = {
            "type": table.time_partitioning.type_,
            "field": table.time_partitioning.field,
            "expiration_ms": table.time_partitioning.expiration_ms,
            "require_partition_filter": table.require_partition_filter,
        }

    range_partitioning = None
    if table.range_partitioning:
        range_partitioning = serialize(table.range_partitioning.to_api_repr())

    return {
        "table_id": table.full_table_id.replace(":", "."),
        "friendly_name": table.friendly_name,
        "description": table.description,
        "created": serialize(table.created),
        "modified": serialize(table.modified),
        "expires": serialize(table.expires),
        "num_rows": table.num_rows,
        "num_bytes": table.num_bytes,
        "num_bytes_human": bytes_human(table.num_bytes),
        "table_type": table.table_type,
        "location": table.location,
        "labels": dict(table.labels or {}),
        "schema": [field_to_dict(field) for field in table.schema],
        "schema_field_count": len(table.schema),
        "time_partitioning": time_partitioning,
        "range_partitioning": range_partitioning,
        "clustering_fields": list(table.clustering_fields or []),
    }


def run_query_with_guard(
    client: "bigquery.Client",
    sql: str,
    *,
    location: str | None,
    max_bytes_billed: int,
    label: str,
) -> dict[str, Any]:
    """Run a data-scanning query under a dry-run cost guard.

    Cost-safety invariant: every query that scans table data goes through
    here. We dry-run first to estimate ``totalBytesProcessed``; if that
    estimate exceeds ``max_bytes_billed``, we skip execution and record
    ``status: "skipped_estimate_exceeds_cap"``. Only when the estimate is
    under the cap do we actually run the query, with ``maximum_bytes_billed``
    enforced as a hard stop on the BigQuery side as a second line of defense.
    """
    from google.cloud import bigquery  # lazy: only needed when actually scanning data
    dry_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    dry_job = client.query(sql, job_config=dry_config, location=location)
    estimated = int(dry_job.total_bytes_processed or 0)

    result: dict[str, Any] = {
        "label": label,
        "status": "dry_run_complete",
        "estimated_bytes_processed": estimated,
        "estimated_bytes_human": bytes_human(estimated),
        "max_bytes_billed": max_bytes_billed,
    }

    if max_bytes_billed and estimated > max_bytes_billed:
        result["status"] = "skipped_estimate_exceeds_cap"
        return result

    job_config = bigquery.QueryJobConfig(
        use_query_cache=False,
        maximum_bytes_billed=max_bytes_billed if max_bytes_billed else None,
    )
    job = client.query(sql, job_config=job_config, location=location)
    rows = [dict(row) for row in job.result()]
    result.update(
        {
            "status": "complete",
            "rows": serialize(rows),
            "bytes_processed": int(job.total_bytes_processed or 0),
            "bytes_billed": int(job.total_bytes_billed or 0),
            "bytes_billed_human": bytes_human(int(job.total_bytes_billed or 0)),
        }
    )
    return result


def check_partitions(
    client: "bigquery.Client",
    table_id: str,
    *,
    location: str | None,
    max_bytes_billed: int,
) -> dict[str, Any]:
    """Query ``INFORMATION_SCHEMA.PARTITIONS`` for partition count, skew, and freshness.

    Reads metadata, not table data — typically very cheap, but still
    routed through ``run_query_with_guard`` for consistency. The
    ``__NULL__`` and ``__UNPARTITIONED__`` magic partition IDs are
    excluded from oldest/newest aggregation but counted separately.
    """
    project, dataset, table = split_table_id(table_id)
    sql = f"""
    SELECT
      COUNT(*) AS partition_count,
      COUNTIF(total_rows = 0) AS empty_partition_count,
      COUNTIF(partition_id = '__NULL__') AS null_partition_count,
      COUNTIF(partition_id = '__UNPARTITIONED__') AS unpartitioned_partition_count,
      MIN(NULLIF(partition_id, '__NULL__')) AS oldest_partition_id,
      MAX(NULLIF(partition_id, '__NULL__')) AS newest_partition_id,
      MAX(last_modified_time) AS last_partition_modified,
      SUM(total_rows) AS total_rows,
      SUM(total_logical_bytes) AS total_logical_bytes,
      MAX(total_logical_bytes) AS max_partition_bytes,
      AVG(total_logical_bytes) AS avg_partition_bytes
    FROM `{project}.{dataset}.INFORMATION_SCHEMA.PARTITIONS`
    WHERE table_name = '{table}'
    """
    return run_query_with_guard(
        client, sql, location=location, max_bytes_billed=max_bytes_billed, label="partitions"
    )


def _where_suffix(where_clause: str | None) -> str:
    """Wrap the user's WHERE expression in parens for safe AND-combinable injection."""
    return f"\nWHERE ({where_clause})" if where_clause else ""


def check_freshness(
    client: "bigquery.Client",
    table_id: str,
    column: str,
    *,
    location: str | None,
    max_bytes_billed: int,
    where_clause: str | None = None,
) -> dict[str, Any]:
    """Compute ``MAX(column)`` to gauge the most recent value in a DATE/DATETIME/TIMESTAMP column."""
    sql = f"SELECT MAX({quote_column(column)}) AS max_value FROM {quote_table(table_id)}{_where_suffix(where_clause)}"
    result = run_query_with_guard(client, sql, location=location, max_bytes_billed=max_bytes_billed, label="freshness")
    if where_clause:
        result["where_clause"] = where_clause
    return result


def check_duplicate_keys(
    client: "bigquery.Client",
    table_id: str,
    key_cols: Iterable[str],
    *,
    location: str | None,
    max_bytes_billed: int,
    where_clause: str | None = None,
) -> dict[str, Any]:
    """Count distinct key groups with >1 row and the total excess rows.

    ``duplicate_excess_rows`` = sum of (group_size - 1), which is the
    number of rows that would be removed by a perfect dedupe. A clean
    table has both counters at 0.
    """
    quoted = [quote_column(col) for col in key_cols]
    select_keys = ", ".join(quoted)
    sql = f"""
    SELECT
      COUNT(*) AS duplicate_key_groups,
      COALESCE(SUM(row_count - 1), 0) AS duplicate_excess_rows
    FROM (
      SELECT {select_keys}, COUNT(*) AS row_count
      FROM {quote_table(table_id)}{_where_suffix(where_clause)}
      GROUP BY {select_keys}
      HAVING COUNT(*) > 1
    )
    """
    result = run_query_with_guard(client, sql, location=location, max_bytes_billed=max_bytes_billed, label="duplicate_keys")
    if where_clause:
        result["where_clause"] = where_clause
    return result


def profile_columns(
    client: "bigquery.Client",
    table_id: str,
    fields: list["bigquery.SchemaField"],
    requested_cols: list[str],
    *,
    max_profile_cols: int,
    location: str | None,
    max_bytes_billed: int,
    where_clause: str | None = None,
) -> dict[str, Any]:
    """Build one query that emits a null count + (optional) approx-distinct per column.

    Selection rules:
    - If ``requested_cols`` is non-empty, profile exactly those — but bail
      with ``skipped_missing_columns`` if any aren't in the schema.
    - Otherwise, profile up to ``max_profile_cols`` top-level scalar columns.
    The query is a single SELECT with one COUNTIF and (where applicable)
    one APPROX_COUNT_DISTINCT per column, so total cost ~ one full scan
    regardless of how many columns are profiled.
    """
    field_by_name = {field.name: field for field in fields}
    if requested_cols:
        missing = [col for col in requested_cols if col not in field_by_name]
        if missing:
            return {"label": "column_profile", "status": "skipped_missing_columns", "missing_columns": missing}
        selected = [field_by_name[col] for col in requested_cols]
    else:
        selected = [field for field in fields if is_top_level_scalar(field)][:max_profile_cols]

    expressions: list[str] = ["COUNT(*) AS scanned_rows"]
    for field in selected:
        col = quote_column(field.name)
        safe = field.name
        expressions.append(f"COUNTIF({col} IS NULL) AS `{safe}__null_count`")
        if field.field_type.upper() in APPROX_DISTINCT_TYPES:
            expressions.append(f"APPROX_COUNT_DISTINCT({col}) AS `{safe}__approx_distinct`")

    if len(expressions) == 1:
        return {"label": "column_profile", "status": "skipped_no_scalar_columns"}

    sql = f"SELECT\n  " + ",\n  ".join(expressions) + f"\nFROM {quote_table(table_id)}{_where_suffix(where_clause)}"
    result = run_query_with_guard(client, sql, location=location, max_bytes_billed=max_bytes_billed, label="column_profile")
    result["profiled_columns"] = [field.name for field in selected]
    if where_clause:
        result["where_clause"] = where_clause
    return result


def sample_rows(client: "bigquery.Client", table: "bigquery.Table", limit: int) -> dict[str, Any]:
    """Fetch up to ``limit`` rows via ``tabledata.list`` (no scan, no SQL).

    This uses the BigQuery Storage API row endpoint, which is free and
    doesn't count against query quotas — that's why it isn't subject to
    the dry-run guard.
    """
    if limit <= 0:
        return {"status": "disabled", "rows": []}
    rows = []
    for row in client.list_rows(table, max_results=limit):
        rows.append(serialize(dict(row)))
    return {"status": "complete", "limit": limit, "rows": rows}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    warnings: list[str] = []

    try:
        split_table_id(args.table)
        key_cols = csv_arg(args.key_cols)
        profile_cols = csv_arg(args.profile_cols)
        if args.freshness_col:
            args.freshness_col = validate_column(args.freshness_col)
        where_clause = validate_where_clause(args.where)
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    # Lazy-imported here so the renderer modules in this package remain usable
    # by render_report.py without google-cloud-bigquery installed.
    try:
        from google.cloud import bigquery
        from google.api_core.exceptions import GoogleAPIError
    except ImportError as exc:
        print(
            f"google-cloud-bigquery is required for direct script use ({exc}). "
            "Install it via `pip install -r requirements.txt`, or use the "
            "BigQuery MCP connector + render_report.py path instead.",
            file=sys.stderr,
        )
        return 1

    client = bigquery.Client(project=args.billing_project, location=args.location)

    try:
        table = client.get_table(args.table)
    except GoogleAPIError as exc:
        print(f"BigQuery API error while reading table metadata: {exc}", file=sys.stderr)
        return 1

    report: dict[str, Any] = {
        "metadata": metadata_report(table),
        "checks": {},
        "sample": {},
        "warnings": warnings,
    }

    if table.table_type == "EXTERNAL":
        warnings.append("External/federated table: metadata num_rows and num_bytes are unavailable; use --run-data-checks for an actual count (subject to --max-bytes-billed).")
    elif table.table_type == "VIEW":
        warnings.append("Logical VIEW: row/byte metadata reflects the view definition, not materialized rows; data checks will execute the view query each time.")

    if where_clause and not args.run_data_checks:
        warnings.append("--where was provided but --run-data-checks is not set; the WHERE clause has no effect on metadata-only output.")

    is_partitioned = bool(table.time_partitioning or table.range_partitioning)
    if (args.check_partitions or args.run_data_checks) and is_partitioned:
        report["checks"]["partitions"] = check_partitions(
            client,
            args.table,
            location=args.location,
            max_bytes_billed=args.max_bytes_billed,
        )
    elif args.check_partitions and not is_partitioned:
        warnings.append("--check-partitions requested but table is not partitioned; skipped.")

    if args.run_data_checks:
        if args.freshness_col:
            report["checks"]["freshness"] = check_freshness(
                client,
                args.table,
                args.freshness_col,
                location=args.location,
                max_bytes_billed=args.max_bytes_billed,
                where_clause=where_clause,
            )
        if key_cols:
            report["checks"]["duplicate_keys"] = check_duplicate_keys(
                client,
                args.table,
                key_cols,
                location=args.location,
                max_bytes_billed=args.max_bytes_billed,
                where_clause=where_clause,
            )
        report["checks"]["column_profile"] = profile_columns(
            client,
            args.table,
            list(table.schema),
            profile_cols,
            max_profile_cols=args.max_profile_cols,
            location=args.location,
            max_bytes_billed=args.max_bytes_billed,
            where_clause=where_clause,
        )
    else:
        warnings.append("Data checks were not run. Add --run-data-checks to profile columns, check freshness, or detect duplicate keys.")

    if args.sample_limit > 0:
        report["sample"] = sample_rows(client, table, args.sample_limit)

    if args.baseline:
        try:
            with open(args.baseline, "r", encoding="utf-8") as f:
                baseline_report = json.load(f)
            baseline_schema = (baseline_report.get("metadata") or {}).get("schema") or []
            drift = schema_drift(report["metadata"]["schema"], baseline_schema)
            drift["baseline_path"] = args.baseline
            report["schema_drift"] = drift
            for col in drift["removed"]:
                warnings.append(f"Schema drift: column '{col}' removed since baseline.")
            for col in drift["added"]:
                warnings.append(f"Schema drift: column '{col}' added since baseline.")
            for ch in drift["changed"]:
                warnings.append(f"Schema drift: column '{ch['name']}' changed: {{'type': {ch.get('type')}, 'mode': {ch.get('mode')}}}.")
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Failed to read --baseline {args.baseline}: {exc}")
            report["schema_drift"] = {"error": str(exc), "baseline_path": args.baseline}

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

    if has_expectations and any(e.get("status") == "failed" for e in report["expectations"]):
        failed = [e["name"] for e in report["expectations"] if e.get("status") == "failed"]
        print(f"Expectation(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
