---
name: bigquery-table-evaluator
description: Use this skill when a user wants to evaluate, audit, profile, validate, or health-check a Google BigQuery table. It reports table metadata, schema, row/byte counts, partitioning/clustering, optional partition-health stats from INFORMATION_SCHEMA, optional null-rate profiling, optional freshness checks, and optional duplicate-key checks. Supports CI-style expectation flags (min rows, max null rate, freshness window, zero duplicates) that exit non-zero on failure.
---

# BigQuery Table Evaluator

## Purpose
Evaluate a BigQuery table and produce a compact health report. Prefer metadata-only checks first. Run data-scanning checks only when the user requests them or provides an explicit scan budget.

## Required inputs1
- Fully qualified table ID in the form `project.dataset.table`.

## Optional inputs
- `freshness_column`: a `DATE`, `DATETIME`, or `TIMESTAMP` column to evaluate recency with `MAX(column)`.
- `key_columns`: one or more columns used to check duplicate keys.
- `profile_columns`: explicit columns to profile for nulls and approximate distinct counts.
- `max_bytes_billed`: BigQuery scan cap for data checks.
- `sample_limit`: number of rows to fetch for a preview.
- `check_partitions`: query `INFORMATION_SCHEMA.PARTITIONS` for partition count, empty partitions, oldest/newest partition, and size skew.
- `where`: SQL expression applied to all data-scanning checks. Identifiers, comments, and statement separators are blocked. Use to constrain scans on partitioned tables (e.g. `event_date >= '2026-01-01'`).
- `baseline`: path to a prior JSON report; enables a schema-drift section comparing top-level fields.
- Expectations (any combination; exits with code 3 if any fail):
  - `expect_min_rows`: integer minimum.
  - `expect_zero_duplicates`: requires `key_columns`.
  - `expect_freshness_within`: duration like `24h`, `7d`, `30m`, `90s`; requires `freshness_column`.
  - `expect_max_null_rate`: one or more `column=rate` (rate in `[0, 1]`); requires the column to be in the column profile.
  - `expect_no_schema_drift`: requires `baseline`.

## Operating rules
1. Never interpolate unvalidated SQL identifiers. Validate the table ID and column names, then wrap identifiers in backticks.
2. Use table metadata for row count, byte count, schema, partitioning, clustering, labels, created time, and modified time.
3. Before running any query that scans table data, dry-run it and compare bytes-processed to the user's scan cap.
4. If the dry-run estimate exceeds the cap, skip that check and record `status: "skipped_estimate_exceeds_cap"` with the estimated bytes — do not run.
5. Keep reports concise: metadata, schema summary, check results, warnings.
6. For very large tables, profile only selected scalar top-level columns unless the user asks for more.

## Implementation

Two execution paths. **Prefer Path A** when the BigQuery MCP connector is available, since it works in any Claude environment (Claude Code, claude.ai web, etc.) without needing local Python deps or `gcloud` auth.

### Path A — BigQuery MCP connector (preferred)

Requires the [Google GenAI Toolbox](https://github.com/googleapis/genai-toolbox) MCP server registered with the `--prebuilt bigquery` configuration. Tool names below assume the MCP server entry is named `bigquery`. If it's named differently, the prefix changes accordingly (e.g. `mcp__<server-name>__execute_sql`).

**Step 1 — fetch metadata.** Call:
```
mcp__bigquery__get_table_info(project="<project>", dataset="<dataset>", table="<table>")
```
This returns table metadata including `Schema`, `NumRows`, `NumBytes`, `TimePartitioning`, `Clustering`, `Labels`, `CreationTime`, `LastModifiedTime`, `Description`, and `Type`. Map these into the `metadata` dict shape shown in "Report shape" below.

**Step 2 — for each requested check, run the SQL via `mcp__bigquery__execute_sql` with the dry-run cost guard.** Always pass `dry_run: true` first. If the returned `totalBytesProcessed` exceeds the user's scan cap, skip that check and put `{"status": "skipped_estimate_exceeds_cap", "estimated_bytes_processed": <n>}` in the report instead of running. Only when the estimate is under the cap, call `execute_sql` again with `dry_run: false` and use the returned rows.

Validate identifiers before interpolating: project/dataset/table must match `^[A-Za-z0-9_-]+$`/`^[A-Za-z0-9_]+$`/`^[A-Za-z0-9_*$-]+$`; column names must match `^[A-Za-z_][A-Za-z0-9_]*$`. Reject anything else. Wrap all identifiers in backticks.

SQL templates (replace `{...}` placeholders):

- **Freshness** — for `freshness_column`:
  ```sql
  SELECT MAX(`{col}`) AS max_value FROM `{project}.{dataset}.{table}`
  ```

- **Duplicate keys** — `key_cols` joined with `` ` , ` ``:
  ```sql
  SELECT
    COUNT(*) AS duplicate_key_groups,
    COALESCE(SUM(row_count - 1), 0) AS duplicate_excess_rows
  FROM (
    SELECT `{key_cols}`, COUNT(*) AS row_count
    FROM `{project}.{dataset}.{table}`
    GROUP BY `{key_cols}`
    HAVING COUNT(*) > 1
  )
  ```

- **Column profile** — one `COUNTIF` per profiled column, plus `APPROX_COUNT_DISTINCT` for scalar types only (skip for `BYTES`, `GEOGRAPHY`, `JSON`):
  ```sql
  SELECT
    COUNT(*) AS scanned_rows,
    COUNTIF(`{col}` IS NULL) AS `{col}__null_count`,
    APPROX_COUNT_DISTINCT(`{col}`) AS `{col}__approx_distinct`
    -- repeat the two lines above for each profiled column
  FROM `{project}.{dataset}.{table}`
  ```

- **Partition health** — only for partitioned tables:
  ```sql
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
  ```

- **Sample rows** — the toolbox does not expose `tabledata.list`, so this is also a query (still subject to the dry-run guard):
  ```sql
  SELECT * FROM `{project}.{dataset}.{table}` LIMIT {n}
  ```

If the user provided a `where` clause, append `WHERE ({where})` (or merge into the existing `WHERE` for partition health) to freshness, duplicate-key, and column-profile queries — never to partition health (which queries `INFORMATION_SCHEMA`) or sample rows (which the user controls separately).

**Step 3 — assemble the report dict.** The shape must match what `scripts/render_report.py` expects:

```json
{
  "metadata": {
    "table_id": "project.dataset.table",
    "num_rows": 1234567,
    "num_bytes": 893524000,
    "num_bytes_human": "852.13 MiB",
    "schema_field_count": 6,
    "table_type": "TABLE",
    "modified": "2026-05-03T11:30:00+00:00",
    "created": "2025-11-04T17:29:00+00:00",
    "schema": [
      {"name": "...", "type": "STRING", "mode": "NULLABLE", "description": "...", "fields": []}
    ],
    "time_partitioning": {"type": "DAY", "field": "event_date"},
    "clustering_fields": ["user_id"],
    "labels": {}
  },
  "checks": {
    "freshness": {
      "label": "freshness",
      "status": "complete",
      "estimated_bytes_processed": 1234567,
      "rows": [{"max_value": "2026-05-03T10:45:00+00:00"}]
    },
    "duplicate_keys": { "...": "..." },
    "column_profile": {
      "...": "...",
      "rows": [{"scanned_rows": 1000, "user_id__null_count": 5, "user_id__approx_distinct": 847}],
      "profiled_columns": ["user_id", "..."]
    },
    "partitions": { "...": "..." }
  },
  "sample": {"status": "complete", "limit": 5, "rows": [{"...": "..."}]},
  "warnings": ["..."]
}
```

For each check, set `status` to `"complete"` (with `rows`), `"skipped_estimate_exceeds_cap"` (with `estimated_bytes_processed`), `"skipped_missing_columns"`, or `"skipped_no_scalar_columns"`. The renderer treats anything other than `"complete"` as a non-fatal skip.

**Step 4 — render.** Write the assembled dict to a JSON file and run:

```bash
python scripts/render_report.py --input report.json --output-md report.md --output-html report.html --theme auto
```

Pass any expectation flags (same names as the main script: `--expect-min-rows`, `--expect-zero-duplicates`, `--expect-freshness-within`, `--expect-max-null-rate COL=RATE`, `--expect-no-schema-drift`) to evaluate them and exit `3` if any fail. The renderer is the single source of truth for the Markdown and HTML output — never hand-format these from the JSON.

`render_report.py` has no BigQuery dependency, so Path A works in any environment that has Python + the skill files (no `google-cloud-bigquery`, no `gcloud auth` needed).

### Path B — Bundled Python script (fallback)

Use this when the MCP connector isn't available — typically a local Claude Code session where `google-cloud-bigquery` is installed and `gcloud auth application-default login` has been run.

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --freshness-col event_timestamp \
  --key-cols event_id \
  --max-bytes-billed 1073741824 \
  --sample-limit 5 \
  --output-json report.json \
  --output-md report.md \
  --output-html report.html
```

CI-style health gate (exits 3 if any expectation fails):

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --freshness-col event_timestamp \
  --key-cols event_id \
  --expect-min-rows 1000 \
  --expect-zero-duplicates \
  --expect-freshness-within 24h \
  --expect-max-null-rate user_id=0 \
  --expect-max-null-rate referrer=0.05 \
  --output-json report.json --output-md report.md
```

The script and `render_report.py` produce byte-identical Markdown and HTML for the same JSON shape.

## Output
- JSON report for machine consumption (and as input to `render_report.py`).
- Markdown report for human review.
- Optional self-contained HTML dashboard (`--output-html report.html`) with status pills, per-column null-rate and distinct-count SVG charts, partition stats, and a collapsible schema. No external assets, no JavaScript. Pass `--theme {auto,light,dark}` (default `auto`, which follows the viewer's OS preference).

## Authentication
- **Path A**: handled by the MCP server (it picks up `GOOGLE_APPLICATION_CREDENTIALS` and `BIGQUERY_PROJECT` from its own environment).
- **Path B**: Google Application Default Credentials, or any environment accepted by the `google-cloud-bigquery` client library.
