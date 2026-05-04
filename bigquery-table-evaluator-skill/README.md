# BigQuery Table Evaluator Skill

A small skill for evaluating a Google BigQuery table. It can run metadata-only checks, or optional data-quality checks with BigQuery dry-run cost protection.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gcloud auth application-default login
```

## Metadata-only evaluation

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --output-json report.json \
  --output-md report.md
```

## Data checks with a scan cap

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --freshness-col event_timestamp \
  --key-cols event_id \
  --max-bytes-billed 1073741824 \
  --sample-limit 5 \
  --output-json report.json \
  --output-md report.md
```

## What it checks

- Table metadata: type, created/modified timestamps, rows, bytes, labels, description.
- Schema summary: top-level fields, type, mode, description.
- Partitioning and clustering metadata.
- Optional partition health (`--check-partitions` or `--run-data-checks` on a partitioned table): partition count, empty partitions, oldest/newest partition, total/max/avg partition bytes — via `INFORMATION_SCHEMA.PARTITIONS`.
- Optional freshness: `MAX(freshness_column)`.
- Optional duplicate keys: duplicate groups and duplicate excess rows.
- Optional column profile: null counts and approximate distinct counts for scalar columns.
- Optional sample rows via the BigQuery table row API.
- Optional schema drift: pass `--baseline previous_report.json` to surface added, removed, or retyped columns vs a prior run.

## Constraining scans with --where

For partitioned or large tables, pass `--where` to restrict the data-scanning checks (freshness, duplicate keys, column profile). The clause is appended to each query as `WHERE (...)`. Backticks, semicolons, and SQL comments are rejected.

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --where "event_date >= '2026-04-26'" \
  --key-cols event_id \
  --max-bytes-billed 1073741824
```

## Expectations (CI-style health gate)

Pass any combination of expectation flags and the script exits with code `3` if any fail. The report still gets written so you can inspect what broke.

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
  --expect-max-null-rate referrer=0.05
```

Available expectations:

- `--expect-min-rows N` — uses metadata `num_rows`.
- `--expect-zero-duplicates` — requires `--key-cols`.
- `--expect-freshness-within DURATION` — `30s`, `15m`, `24h`, `7d`. Requires `--freshness-col`.
- `--expect-max-null-rate COL=RATE` — repeatable. Requires the column to be in the profile.
- `--expect-no-schema-drift` — requires `--baseline`.

Exit codes: `0` clean, `1` BigQuery API error, `2` input/validation error, `3` expectation failure.

## HTML dashboard (optional)

Add `--output-html report.html` to also emit a single self-contained HTML dashboard alongside the JSON/Markdown. It has inline CSS, an SVG bar chart of per-column null rates, status pills for each expectation, a partition-stats card, and a collapsible schema table. No external assets, no JavaScript — open it locally or attach it as a CI artifact.

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --check-partitions \
  --freshness-col event_timestamp \
  --output-html report.html \
  --theme auto
```

Use `--theme {auto,light,dark}` (default `auto`) to control the palette. `auto` ships both palettes and switches via the `prefers-color-scheme` media query; `light` or `dark` bake in a single fixed palette — useful for artifacts that need consistent appearance regardless of viewer.

Pre-rendered examples live in [`examples/`](examples/) — open `examples/sample_dashboard_auto.html` (or `_light` / `_dark`) in a browser to see the output without running against a real table. Re-render any time after changing the renderer:

```bash
python examples/render_sample_dashboard.py
```

The generator stubs `google.cloud.bigquery`, so it does not need GCP credentials or the real client library. The script in `scripts/` is the source of truth; the HTML files are illustrative and may lag if not re-rendered.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

The test suite stubs `google.cloud.bigquery` so it runs without GCP credentials. It covers the input validators, the expectation evaluator, the schema-drift differ, and the WHERE-clause guard.

## Notes

Data checks can scan table data and incur BigQuery costs. The script performs a dry run first and skips a check when the estimated bytes exceed `--max-bytes-billed`.
